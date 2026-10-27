"""I/O-free logic shared by :class:`Client` and :class:`AsyncClient`.

Everything that decides *what* to send, *how* to read a response and *whether*
to retry lives here, once. The two clients only differ in how they perform the
HTTP exchange and how they sleep.
"""

from __future__ import annotations

import asyncio
import gzip
import ipaddress
import json
import math
import re
import secrets
import time
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Generic, NamedTuple, TypeVar
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import _constants as const
from ._errors import APIError, ResponseError, ResponseMeta, ValidationError
from ._memory import ScriptMemory
from ._models import _FieldTypeError

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Retry policy and delay math
# ---------------------------------------------------------------------------


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class RetryPolicy:
    """Retries after an explicit, retryable non-2xx API response.

    ``max_retries`` is the number of retries after the first attempt (0..10).
    ``429 solver_busy`` is retried within the client's solver_busy budget
    instead and does not count against it; ``max_retries=0`` disables every
    automatic retry, solver_busy included. ``base_delay`` is the first
    exponential-backoff ceiling and ``max_delay`` caps it, both in seconds; a
    server ``Retry-After`` hint may exceed ``max_delay`` up to one hour.
    Transport errors are never retried.
    """

    max_retries: int = const.DEFAULT_MAX_RETRIES
    base_delay: float = const.DEFAULT_BASE_DELAY
    max_delay: float = const.DEFAULT_MAX_DELAY

    def __post_init__(self) -> None:
        retries = self.max_retries
        if not isinstance(retries, int) or isinstance(retries, bool):
            raise ValidationError("max_retries", "must be an integer")
        if not 0 <= retries <= const.MAX_CONFIGURED_RETRIES:
            raise ValidationError(
                "max_retries", f"must be between 0 and {const.MAX_CONFIGURED_RETRIES}"
            )
        for name in ("base_delay", "max_delay"):
            value = getattr(self, name)
            if not _is_number(value) or not 0 <= value <= const.MAX_RETRY_DELAY:
                raise ValidationError(name, "must be between 0 and 3600 seconds")
        if self.base_delay > 0 and self.max_delay == 0:
            raise ValidationError("max_delay", "must be positive when base_delay is positive")
        if self.max_delay > 0 and self.base_delay > self.max_delay:
            raise ValidationError("base_delay", "must not exceed max_delay")


Jitter = Callable[[float], float]


def full_jitter(ceiling: float) -> float:
    """A uniformly random delay in ``[0, ceiling]`` seconds (microsecond
    resolution) from a CSPRNG, so clients rejected together do not return
    together."""
    if not ceiling > 0:
        return 0.0
    steps = int(ceiling * 1_000_000)
    try:
        return secrets.randbelow(steps + 1) / 1_000_000
    except Exception:  # pragma: no cover - OS entropy failure
        return ceiling / 2


def retry_delay(retry: int, retry_after: float, policy: RetryPolicy, jitter: Jitter) -> float:
    """The wait before a call's ``retry``-th ordinary retry (1-based): full
    jitter over an exponentially growing ceiling, never below the server
    hint."""
    base = policy.base_delay
    if retry > 1 and base > 0:
        i = 1
        while i < retry and base < policy.max_delay:
            if base > policy.max_delay / 2:
                base = policy.max_delay
                break
            base *= 2
            i += 1
    if policy.max_delay > 0 and base > policy.max_delay:
        base = policy.max_delay
    base = jitter(base)
    return retry_after if retry_after > base else base


def solver_busy_delay(retry: int, retry_after: float, remaining: float, jitter: Jitter) -> float:
    """The wait before a call's ``retry``-th solver_busy retry (1-based).

    The server hint (or a one-second floor) is never undercut; above it a full
    jitter window doubles per retry up to 10 s (or twice the floor, when that
    is larger). While the floor fits in the ``remaining`` budget the window is
    narrowed to end inside it.
    """
    floor = retry_after if retry_after > 0 else const.SOLVER_BUSY_RETRY_FLOOR
    if floor >= const.MAX_RETRY_DELAY:
        return const.MAX_RETRY_DELAY
    limit = min(max(const.SOLVER_BUSY_RETRY_MAX_DELAY, 2 * floor), const.MAX_RETRY_DELAY)
    ceiling = floor
    i = 0
    while i < retry and ceiling < limit:
        ceiling *= 2
        i += 1
    ceiling = min(ceiling, limit)
    if remaining >= floor:
        ceiling = min(ceiling, remaining)
    return floor + jitter(ceiling - floor)


# ---------------------------------------------------------------------------
# Retry hints and the error envelope
# ---------------------------------------------------------------------------

_INT_RE = re.compile(r"[+-]?[0-9]+")
_INT64_MAX = 2**63 - 1


def parse_retry_after_ms(value: object) -> float:
    """Seconds from the optional ``retry_after_ms`` body field. Absent, null,
    non-numeric, non-finite or non-positive values yield 0."""
    if not _is_number(value):
        return 0.0
    try:
        ms = float(value)  # type: ignore[arg-type]
    except OverflowError:
        return 0.0
    if not math.isfinite(ms) or ms <= 0:
        return 0.0
    if ms >= const.MAX_RETRY_DELAY * 1000:
        return const.MAX_RETRY_DELAY
    return ms / 1000


def parse_retry_after(value: str, now: float) -> float:
    """Seconds from a ``Retry-After`` header: delta-seconds or an HTTP-date,
    capped at one hour. Anything unusable yields 0."""
    value = value.strip()
    if not value:
        return 0.0
    if _INT_RE.fullmatch(value) and -_INT64_MAX - 1 <= int(value) <= _INT64_MAX:
        seconds = int(value)
        if seconds <= 0:
            return 0.0
        if seconds >= const.MAX_RETRY_DELAY:
            return const.MAX_RETRY_DELAY
        return float(seconds)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return 0.0
    if when is None:  # pragma: no cover - older Pythons return None
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delay = when.timestamp() - now
    if not delay > 0:
        return 0.0
    return min(delay, const.MAX_RETRY_DELAY)


class _ErrorEnvelope(NamedTuple):
    code: str
    message: str
    stage: str
    reason: str
    retry_after: float


_HTTP_ERROR = _ErrorEnvelope("http_error", "", "", "", 0.0)


def decode_error_envelope(payload: bytes) -> _ErrorEnvelope:
    """Decode ``{"error"|"error_code", "message", "stage", "reason",
    "retry_after_ms"}``. A body that is not an object of those types yields
    ``http_error`` and nothing else; a malformed hint never discards the
    code."""
    try:
        data = json.loads(payload.decode("utf-8", "replace"))
    except ValueError:
        return _HTTP_ERROR
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _HTTP_ERROR
    fields: dict[str, str] = {}
    for key in ("error", "error_code", "message", "stage", "reason"):
        value = data.get(key)
        if value is None:
            value = ""
        if not isinstance(value, str):
            return _HTTP_ERROR
        fields[key] = value
    code = fields["error"] or fields["error_code"] or "http_error"
    return _ErrorEnvelope(
        code=code,
        message=fields["message"],
        stage=fields["stage"],
        reason=fields["reason"],
        retry_after=parse_retry_after_ms(data.get("retry_after_ms")),
    )


# ---------------------------------------------------------------------------
# Request encoding
# ---------------------------------------------------------------------------


def _json_default(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def encode_json(value: object) -> bytes:
    """Compact UTF-8 JSON, as the API expects."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
        return text.encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValidationError("request", "could not encode JSON", exc) from exc


def normalize_base_url(raw: str) -> str:
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        parts.port  # noqa: B018 - validates the port
    except (TypeError, ValueError, AttributeError):
        raise ValidationError("base_url", "must be an absolute HTTP(S) URL") from None
    if not host or parts.scheme not in ("http", "https"):
        raise ValidationError("base_url", "must be an absolute HTTP(S) URL")
    if "@" in parts.netloc or parts.query or parts.fragment:
        raise ValidationError("base_url", "must not contain credentials, query, or fragment")
    if parts.scheme == "http" and not _is_loopback(host):
        raise ValidationError("base_url", "must use HTTPS unless it is loopback")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def _check_seconds(name: str, value: object, *, allow_zero: bool) -> float:
    if not _is_number(value):
        raise ValidationError(name, "must be a number of seconds")
    seconds = float(value)  # type: ignore[arg-type]
    low_ok = seconds >= 0 if allow_zero else seconds > 0
    if not (low_ok and seconds <= const.MAX_RETRY_DELAY):
        bound = "between 0 and 3600 seconds" if allow_zero else "positive, at most 3600 seconds"
        raise ValidationError(name, f"must be {bound}")
    return seconds


# ---------------------------------------------------------------------------
# Calls, prepared requests and response reading
# ---------------------------------------------------------------------------


SCRIPT_CACHE_CODES = ("script_cache_miss", "script_cache_unavailable")


@dataclass
class Call(Generic[T]):
    """One SDK operation: what to send, and how to build its typed result.

    A script-bearing call sets ``script_wire``, which renders the body with or
    without the script, ``script_key`` (its digest in the client's miss
    memory), ``has_script`` (false when the caller supplied only a script_id)
    and ``script_bytes`` (what the script adds to the body when sent). Every
    other call sends ``wire`` as is. ``finish`` turns the decoded JSON object
    into the typed response.
    """

    method: str
    path: str
    wire: dict[str, Any] | None
    limit: int
    timeout: float
    finish: Callable[[dict[str, Any], ResponseMeta], T]
    script_wire: Callable[[bool], dict[str, Any]] | None = None
    script_key: str = ""
    has_script: bool = False
    script_bytes: int = 0


class Prepared(NamedTuple):
    method: str
    url: str
    operation: str
    headers: dict[str, str]
    content: bytes | None
    timeout: float


def _first_header(headers: httpx.Headers, name: str) -> str:
    values = headers.get_list(name)
    return values[0] if values else ""


class _GzipBody:
    """Incremental gzip decoder (multi-member aware) with an output cap."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._decoder = zlib.decompressobj(31)
        self._started = False
        self.out = bytearray()

    def feed(self, data: bytes) -> None:
        while data:
            if self._decoder.eof:
                self._decoder = zlib.decompressobj(31)
            self._started = True
            room = self._limit + 1 - len(self.out)
            try:
                self.out += self._decoder.decompress(data, room)
            except zlib.error as exc:
                raise ValueError(f"decode gzip response: {exc}") from exc
            if len(self.out) > self._limit:
                raise ValueError(f"response exceeds {self._limit} bytes")
            data = self._decoder.unused_data if self._decoder.eof else self._decoder.unconsumed_tail

    def finish(self) -> bytes:
        if not self._started or not self._decoder.eof:
            raise ValueError("decode gzip response: unexpected EOF")
        return bytes(self.out)


class BodyReader:
    """Reads one response body exactly once: raw identity bytes, or gzip
    decoded here (never by httpx as well), capped at 4 MiB decoded."""

    def __init__(self, meta: ResponseMeta, encoding: str, deadline: float, clock: Clock) -> None:
        self._meta = meta
        self._deadline = deadline
        self._clock = clock
        self._gzip: _GzipBody | None = None
        self._identity = bytearray()
        if encoding == "gzip":
            self._gzip = _GzipBody(const.MAX_RESPONSE_BYTES)
        elif encoding not in ("", "identity"):
            raise ResponseError(
                meta, ValueError(f"unsupported response content encoding {json.dumps(encoding)}")
            )
        self.pending = True

    def take_decoded(self, data: bytes) -> None:
        """Accept a body the HTTP transport already read and decoded."""
        self.pending = False
        self._gzip = None
        self._identity = bytearray()
        self._append(data)

    def feed(self, chunk: bytes) -> None:
        if self._clock.monotonic() > self._deadline:
            raise ResponseError(self._meta, ValueError("read response: timed out"))
        if self._gzip is not None:
            try:
                self._gzip.feed(chunk)
            except ValueError as exc:
                raise ResponseError(self._meta, exc) from exc
        else:
            self._append(chunk)

    def read_failed(self, exc: BaseException) -> ResponseError:
        return ResponseError(self._meta, ValueError(f"read response: {exc}"))

    def finish(self) -> bytes:
        if self._gzip is not None:
            try:
                return self._gzip.finish()
            except ValueError as exc:
                raise ResponseError(self._meta, exc) from exc
        return bytes(self._identity)

    def _append(self, data: bytes) -> None:
        self._identity += data
        if len(self._identity) > const.MAX_RESPONSE_BYTES:
            raise ResponseError(
                self._meta, ValueError(f"response exceeds {const.MAX_RESPONSE_BYTES} bytes")
            )


class Clock:
    """Time source and sleeper; replaced in tests so retries run instantly."""

    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    async def async_sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class RetryState:
    """Retry bookkeeping for one call (each fallback call gets a new one)."""

    def __init__(self, core: Core) -> None:
        self._core = core
        self._started = core.clock.monotonic()
        self._retries = 0
        self._busy = 0

    def next_delay(self, err: APIError) -> float | None:
        """Seconds to wait before retrying after ``err``, or ``None`` to raise
        it."""
        core = self._core
        policy = core.retry_policy
        if not err.retryable:
            return None
        if err.code == "solver_busy":
            # Capacity rejects are retried against a time budget, not
            # max_retries: slots free as solves finish or the fleet scales.
            self._busy += 1
            budget = core.solver_busy_retry_budget
            elapsed = core.clock.monotonic() - self._started
            delay = solver_busy_delay(self._busy, err.retry_after, budget - elapsed, core.jitter)
            if policy.max_retries == 0 or budget <= 0 or elapsed + delay > budget:
                return None
            return delay
        if self._retries >= policy.max_retries:
            return None
        # Internal errors are documented for one retry; do not turn a broken
        # solver release into three identical expensive attempts.
        if self._retries >= 1 and err.code in ("internal", "internal_error"):
            return None
        self._retries += 1
        return retry_delay(self._retries, err.retry_after, policy, core.jitter)


class Core:
    """Validated configuration plus the shared request/response pipeline."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        retry_policy: RetryPolicy | None,
        solver_busy_retry_budget: float,
        compression_threshold: int,
        user_agent: str | None,
        timeout: float,
        perimeterx_timeout: float,
    ) -> None:
        if not isinstance(api_key, str) or api_key == "":
            raise ValidationError("api_key", "must not be empty")
        if api_key.strip() != api_key or "\r" in api_key or "\n" in api_key:
            raise ValidationError("api_key", "must not contain surrounding or control whitespace")
        if not all(0x20 <= ord(ch) <= 0x7E for ch in api_key):
            raise ValidationError("api_key", "must be printable ASCII")
        if retry_policy is None:
            retry_policy = RetryPolicy()
        if not isinstance(retry_policy, RetryPolicy):
            raise ValidationError("retry_policy", "must be a RetryPolicy")
        if not isinstance(compression_threshold, int) or isinstance(compression_threshold, bool):
            raise ValidationError("compression_threshold", "must be an integer")
        agent = const.SDK_USER_AGENT
        if user_agent is not None:
            application = user_agent.strip() if isinstance(user_agent, str) else ""
            if not application or "\r" in application or "\n" in application:
                raise ValidationError("user_agent", "must be a non-empty single line")
            if not application.isascii():
                raise ValidationError("user_agent", "must be ASCII")
            agent += " " + application

        self.api_key = api_key
        self.base_url = normalize_base_url(base_url)
        self.retry_policy = retry_policy
        self.solver_busy_retry_budget = _check_seconds(
            "solver_busy_retry_budget", solver_busy_retry_budget, allow_zero=True
        )
        self.compression_threshold = compression_threshold
        self.user_agent = agent
        self.timeout = _check_seconds("timeout", timeout, allow_zero=False)
        self.perimeterx_timeout = _check_seconds(
            "perimeterx_timeout", perimeterx_timeout, allow_zero=False
        )
        self.clock = Clock()
        self.jitter: Jitter = full_jitter
        self.scripts = ScriptMemory(const.SCRIPT_MEMORY_CAPACITY, lambda: self.clock.monotonic())

    # -- hash-first script exchange ----------------------------------------------

    def first_wire(self, call: Call[Any]) -> tuple[dict[str, Any] | None, bool]:
        """The body of a call's first exchange and whether it carries the
        script. A script-bearing call is hash-first unless the API answered a
        miss for that digest moments ago; a script the API could never accept
        is refused before any round trip."""
        if call.script_wire is None:
            return call.wire, False
        if call.has_script and call.script_bytes > call.limit:
            raise ValidationError(
                "script", f"encoded script exceeds the {call.limit // const.MIB} MiB API limit"
            )
        with_script = call.has_script and self.scripts.recently_missed(call.script_key)
        return call.script_wire(with_script), with_script

    def script_retry(
        self, call: Call[Any], err: APIError, with_script: bool
    ) -> dict[str, Any] | None:
        """The body to resend after ``err``: the same call with the script,
        when the API has no script for the digest it was sent."""
        if (
            call.script_wire is None
            or not call.has_script
            or with_script
            or err.code not in SCRIPT_CACHE_CODES
        ):
            return None
        self.scripts.mark_missed(call.script_key)
        return call.script_wire(True)

    def script_registered(self, call: Call[Any], with_script: bool) -> None:
        """A script-bearing exchange succeeded: the API holds the script now."""
        if with_script:
            self.scripts.mark_registered(call.script_key)

    # -- request side -------------------------------------------------------

    def compress(self, payload: bytes, limit: int) -> tuple[bytes, str]:
        threshold = self.compression_threshold
        if not payload or threshold < 0 or len(payload) < threshold:
            return payload, ""
        compressed = gzip.compress(payload, compresslevel=1, mtime=0)
        # Only worth it when it saves bytes; the edge applies the same cap to
        # wire and decoded bodies.
        if len(compressed) >= len(payload) or len(compressed) > limit:
            return payload, ""
        return compressed, "gzip"

    def prepare(self, call: Call[Any], wire: dict[str, Any] | None) -> Prepared:
        """Encode, size-check and compress a body; raises ValidationError
        before any network I/O."""
        content: bytes | None = None
        encoding = ""
        if wire is not None:
            payload = encode_json(wire)
            if len(payload) > call.limit:
                raise ValidationError(
                    "request",
                    f"encoded JSON exceeds the {call.limit // const.MIB} MiB API limit",
                )
            content, encoding = self.compress(payload, call.limit)
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": self.user_agent,
        }
        if content is not None:
            headers["Content-Type"] = "application/json"
            if encoding:
                headers["Content-Encoding"] = encoding
        return Prepared(
            method=call.method,
            url=self.base_url + call.path,
            operation=f"{call.method} {call.path}",
            headers=headers,
            content=content,
            timeout=call.timeout,
        )

    def http_request(self, prepared: Prepared) -> httpx.Request:
        # Built directly (not via client.build_request) so no client cookies,
        # default headers or base URL leak into the exchange. A bytes body
        # always carries Content-Length: the edge rejects chunked bodies.
        return httpx.Request(
            prepared.method,
            prepared.url,
            headers=prepared.headers,
            content=prepared.content,
            extensions={"timeout": httpx.Timeout(prepared.timeout).as_dict()},
        )

    # -- response side ------------------------------------------------------

    def open_body(
        self, response: httpx.Response, attempt: int, started: float, timeout: float
    ) -> tuple[ResponseMeta, BodyReader]:
        headers = response.headers
        meta = ResponseMeta(
            status_code=response.status_code,
            request_id=_first_header(headers, "X-Request-ID"),
            edge=_first_header(headers, "X-BypassFast-Edge"),
            server_timing=_first_header(headers, "Server-Timing"),
            attempts=attempt,
        )
        encoding = _first_header(headers, "Content-Encoding").strip().lower()
        reader = BodyReader(meta, encoding, started + timeout, self.clock)
        if response.is_stream_consumed:
            # A transport that pre-read the body has already decoded it.
            try:
                data = response.content
            except httpx.ResponseNotRead as exc:  # pragma: no cover - defensive
                raise reader.read_failed(exc) from exc
            reader.take_decoded(data)
        return meta, reader

    def complete(
        self, response: httpx.Response, meta: ResponseMeta, payload: bytes
    ) -> tuple[ResponseMeta, bytes]:
        """Return a 2xx payload; raise :class:`APIError` for anything else."""
        if 200 <= response.status_code < 300:
            return meta, payload
        envelope = decode_error_envelope(payload)
        retry_after = envelope.retry_after
        if retry_after <= 0:
            # retry_after_ms is the millisecond form of Retry-After; older
            # servers send only the header.
            header = _first_header(response.headers, "Retry-After")
            retry_after = parse_retry_after(header, self.clock.time())
        raise APIError(
            meta,
            envelope.code,
            message=envelope.message,
            stage=envelope.stage,
            reason=envelope.reason,
            retry_after=retry_after,
        )

    def finish(self, call: Call[T], meta: ResponseMeta, payload: bytes) -> T:
        data: Any = {}
        if payload:
            try:
                data = json.loads(payload.decode("utf-8", "replace"))
            except ValueError as exc:
                raise ResponseError(meta, exc) from exc
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ResponseError(meta, ValueError("expected a JSON object"))
        try:
            return call.finish(data, meta)
        except _FieldTypeError as exc:
            raise ResponseError(meta, exc) from exc

    def retry_state(self) -> RetryState:
        return RetryState(self)


def merge_fallback_attempts(err: BaseException, prior: int) -> None:
    """Count the first exchange's attempts on a failed fallback's error."""
    if isinstance(err, (APIError, ResponseError)):
        err.response.attempts += prior


TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPError, httpx.InvalidURL, OSError)
