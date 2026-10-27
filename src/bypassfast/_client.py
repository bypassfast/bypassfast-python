"""Synchronous client."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Any, TypeVar

import httpx

from . import _calls as calls
from . import _constants as const
from ._core import TRANSPORT_ERRORS, Call, Core, Prepared, RetryPolicy, merge_fallback_attempts
from ._errors import APIError, RequestError, ResponseError, ResponseMeta, ValidationError
from ._models import (
    AkamaiCPTRequest,
    AkamaiCPTResponse,
    AkamaiSBSDRequest,
    AkamaiSBSDResponse,
    AkamaiSecCPTRequest,
    AkamaiSecCPTResponse,
    AkamaiSensorRequest,
    AkamaiSensorResponse,
    Balance,
    IncapsulaReese84Request,
    IncapsulaResponse,
    IncapsulaUTMVCRequest,
    KasadaCDRequest,
    KasadaCDResponse,
    KasadaSensorRequest,
    KasadaSensorResponse,
    PerimeterxHoldRequest,
    PerimeterxInitRequest,
    PerimeterxResponse,
    RawResponse,
)

if sys.version_info >= (3, 11):
    from typing import Unpack
elif TYPE_CHECKING:
    from typing_extensions import Unpack

T = TypeVar("T")


class Client:
    """Synchronous Bypass Fast client. Safe to share between threads.

    :param api_key: your API key, sent as ``X-API-Key``.
    :param base_url: API origin; HTTPS is required except for loopback hosts.
    :param http_client: an ``httpx.Client`` to send requests with (proxies,
        TLS settings, transports). The SDK never follows redirects and does not
        close a client it did not create.
    :param retry_policy: retry behaviour for retryable API errors.
    :param solver_busy_retry_budget: seconds, measured from the start of a
        call, during which ``429 solver_busy`` is retried (0 disables).
    :param compression_threshold: gzip request bodies of at least this many
        bytes when it saves space; negative disables, 0 compresses every body.
    :param user_agent: application identifier appended to the SDK user agent.
    :param timeout: per-attempt HTTP timeout in seconds.
    :param perimeterx_timeout: per-attempt HTTP timeout for PerimeterX solves.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = const.DEFAULT_BASE_URL,
        http_client: httpx.Client | None = None,
        retry_policy: RetryPolicy | None = None,
        solver_busy_retry_budget: float = const.DEFAULT_SOLVER_BUSY_RETRY_BUDGET,
        compression_threshold: int = const.DEFAULT_COMPRESSION_THRESHOLD,
        user_agent: str | None = None,
        timeout: float = const.DEFAULT_TIMEOUT,
        perimeterx_timeout: float = const.DEFAULT_PERIMETERX_TIMEOUT,
    ) -> None:
        self._core = Core(
            api_key,
            base_url=base_url,
            retry_policy=retry_policy,
            solver_busy_retry_budget=solver_busy_retry_budget,
            compression_threshold=compression_threshold,
            user_agent=user_agent,
            timeout=timeout,
            perimeterx_timeout=perimeterx_timeout,
        )
        if http_client is not None and not isinstance(http_client, httpx.Client):
            raise ValidationError("http_client", "must be an httpx.Client")
        self._owns_http = http_client is None
        self._http = (
            http_client if http_client is not None else httpx.Client(follow_redirects=False)
        )
        self.akamai = AkamaiService(self)
        self.kasada = KasadaService(self)
        self.incapsula = IncapsulaService(self)
        self.perimeterx = PerimeterxService(self)

    def balance(self) -> Balance:
        """Return the current prepaid account balance."""
        return self._run(calls.balance(self._core))

    def solve(self, solver: const.Solver | str, request: Mapping[str, Any]) -> RawResponse:
        """Send ``request`` as-is to ``POST /v1/solve/{solver}``.

        An escape hatch for fields this SDK does not model yet; prefer the
        typed service methods. Retries apply; script caching does not.
        """
        return self._run(calls.generic_solve(self._core, solver, request))

    def close(self) -> None:
        """Close the underlying HTTP client if this client created it."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- pipeline -------------------------------------------------------------

    def _run(self, call: Call[T]) -> T:
        core = self._core
        wire, with_script = core.first_wire(call)
        try:
            meta, payload = self._exchange(core.prepare(call, wire))
        except APIError as err:
            wire = core.script_retry(call, err, with_script)
            if wire is None:
                raise
            # The remote script cache missed: resend with the full script in
            # a fresh retry loop and report the attempts of both exchanges.
            prior = err.response.attempts
            try:
                meta, payload = self._exchange(core.prepare(call, wire))
            except (APIError, ResponseError) as fallback_err:
                merge_fallback_attempts(fallback_err, prior)
                raise
            meta.attempts += prior
            with_script = True
        core.script_registered(call, with_script)
        return core.finish(call, meta, payload)

    def _exchange(self, prepared: Prepared) -> tuple[ResponseMeta, bytes]:
        state = self._core.retry_state()
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._attempt(prepared, attempt)
            except APIError as err:
                delay = state.next_delay(err)
                if delay is None:
                    raise
            if delay > 0:
                self._core.clock.sleep(delay)

    def _attempt(self, prepared: Prepared, attempt: int) -> tuple[ResponseMeta, bytes]:
        core = self._core
        started = core.clock.monotonic()
        try:
            response = self._http.send(
                core.http_request(prepared), stream=True, follow_redirects=False
            )
        except TRANSPORT_ERRORS as exc:
            raise RequestError(prepared.operation, exc) from exc
        try:
            meta, reader = core.open_body(response, attempt, started, prepared.timeout)
            if reader.pending:
                try:
                    for chunk in response.iter_raw():
                        reader.feed(chunk)
                except httpx.HTTPError as exc:
                    raise reader.read_failed(exc) from exc
            payload = reader.finish()
        finally:
            response.close()
        return core.complete(response, meta, payload)


class _Service:
    def __init__(self, client: Client) -> None:
        self._client = client


class AkamaiService(_Service):
    """Akamai Bot Manager: ``sensor``, ``sbsd``, ``cpt`` and ``sec_cpt``."""

    def sensor(self, **request: Unpack[AkamaiSensorRequest]) -> AkamaiSensorResponse:
        """Generate Bot Manager ``sensor_data`` (see :class:`AkamaiSensorRequest`)."""
        return self._client._run(calls.akamai_sensor(self._client._core, request))

    def sbsd(self, **request: Unpack[AkamaiSBSDRequest]) -> AkamaiSBSDResponse:
        """Generate an encrypted SBSD body (see :class:`AkamaiSBSDRequest`)."""
        return self._client._run(calls.akamai_sbsd(self._client._core, request))

    def cpt(self, **request: Unpack[AkamaiCPTRequest]) -> AkamaiCPTResponse:
        """Solve a Sec-CPT proof of work."""
        return self._client._run(calls.akamai_cpt(self._client._core, request))

    def sec_cpt(self, **request: Unpack[AkamaiSecCPTRequest]) -> AkamaiSecCPTResponse:
        """Solve a sec-cpt challenge page; ``body`` is the exact verify body."""
        return self._client._run(calls.akamai_sec_cpt(self._client._core, request))


class KasadaService(_Service):
    """Kasada: ``sensor`` and ``cd``."""

    def sensor(self, **request: Unpack[KasadaSensorRequest]) -> KasadaSensorResponse:
        """Generate the ``/tms`` payload and ``x-kpsdk-*`` headers from ``p.js``."""
        return self._client._run(calls.kasada_sensor(self._client._core, request))

    def cd(self, **request: Unpack[KasadaCDRequest]) -> KasadaCDResponse:
        """Solve the CD proof of work; ``payload`` is the ``x-kpsdk-cd`` value."""
        return self._client._run(calls.kasada_cd(self._client._core, request))


class IncapsulaService(_Service):
    """Imperva / Incapsula: ``reese84`` and ``utmvc``."""

    def reese84(self, **request: Unpack[IncapsulaReese84Request]) -> IncapsulaResponse:
        """Generate a Reese84 sensor payload."""
        return self._client._run(calls.incapsula_reese84(self._client._core, request))

    def utmvc(self, **request: Unpack[IncapsulaUTMVCRequest]) -> IncapsulaResponse:
        """Generate the ``___utmvc`` cookie and submission path."""
        return self._client._run(calls.incapsula_utmvc(self._client._core, request))


class PerimeterxService(_Service):
    """PerimeterX / HUMAN: ``init`` and ``solve_hold``."""

    def init(self, **request: Unpack[PerimeterxInitRequest]) -> PerimeterxResponse:
        """Run the page-sensor session; returns HUMAN cookies and a session."""
        return self._client._run(calls.perimeterx_init(self._client._core, request))

    def solve_hold(self, **request: Unpack[PerimeterxHoldRequest]) -> PerimeterxResponse:
        """Solve the press-and-hold challenge behind a blocked response."""
        return self._client._run(calls.perimeterx_hold(self._client._core, request))
