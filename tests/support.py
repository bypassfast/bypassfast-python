"""Test helpers: a recording fake API, a fake clock and client factories."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Any, Callable, Union

import httpx

import bypassfast
from bypassfast._core import Clock

API_KEY = "test-key"
BASE_URL = "https://api.test"
UA_PREFIX = "bypassfast-python/" + bypassfast.__version__

Body = Union[dict[str, Any], list[Any], str, bytes, None]


def reply(
    status: int = 200,
    body: Body = None,
    *,
    headers: dict[str, str] | None = None,
    gzipped: bool = False,
) -> httpx.Response:
    """A network-like (not pre-read) response."""
    if body is None:
        raw = b""
    elif isinstance(body, bytes):
        raw = body
    elif isinstance(body, str):
        raw = body.encode("utf-8")
    else:
        raw = json.dumps(body).encode("utf-8")
    all_headers = dict(headers or {})
    if gzipped:
        raw = gzip.compress(raw)
        all_headers["Content-Encoding"] = "gzip"
    return httpx.Response(status, headers=all_headers, stream=httpx.ByteStream(raw))


@dataclass
class Recorded:
    request: httpx.Request
    raw: bytes

    @property
    def body(self) -> Any:
        """The decoded JSON body (gunzipped when needed)."""
        data = self.raw
        if self.request.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return json.loads(data) if data else None

    @property
    def headers(self) -> httpx.Headers:
        return self.request.headers


Responder = Union[httpx.Response, Callable[[Recorded], httpx.Response]]


class FakeAPI:
    """Records every request and answers from a script of responses.

    Each entry is a response or a callable taking the recorded request. The
    last entry repeats once the script is exhausted.
    """

    def __init__(self, *responses: Responder) -> None:
        self.responses: list[Responder] = list(responses) or [reply(200, {})]
        self.calls: list[Recorded] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        recorded = Recorded(request, request.read())
        self.calls.append(recorded)
        index = min(len(self.calls), len(self.responses)) - 1
        responder = self.responses[index]
        if callable(responder):
            return responder(recorded)
        # Responses are single-use streams; hand out a fresh copy each time.
        return _clone(responder)

    @property
    def bodies(self) -> list[Any]:
        return [call.body for call in self.calls]


def _clone(response: httpx.Response) -> httpx.Response:
    stream = response.stream
    assert isinstance(stream, httpx.ByteStream)
    raw = b"".join(stream)
    return httpx.Response(
        response.status_code, headers=response.headers, stream=httpx.ByteStream(raw)
    )


class FakeClock(Clock):
    """Monotonic time advances only when the client sleeps."""

    def __init__(self, wall: float = 1_790_000_000.0) -> None:
        self.now = 1000.0
        self.wall = wall
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.wall + self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    async def async_sleep(self, seconds: float) -> None:
        self.sleep(seconds)


def no_jitter(ceiling: float) -> float:
    return 0.0


def max_jitter(ceiling: float) -> float:
    return max(ceiling, 0.0)


def make_client(
    api: Callable[[httpx.Request], httpx.Response],
    *,
    clock: FakeClock | None = None,
    jitter: Callable[[float], float] = no_jitter,
    http_client: httpx.Client | None = None,
    **options: Any,
) -> bypassfast.Client:
    options.setdefault("retry_policy", bypassfast.RetryPolicy(max_retries=0))
    options.setdefault("base_url", BASE_URL)
    client = bypassfast.Client(
        API_KEY,
        http_client=http_client or httpx.Client(transport=httpx.MockTransport(api)),
        **options,
    )
    client._core.clock = clock or FakeClock()
    client._core.jitter = jitter
    return client


def make_async_client(
    api: Callable[[httpx.Request], httpx.Response],
    *,
    clock: FakeClock | None = None,
    jitter: Callable[[float], float] = no_jitter,
    **options: Any,
) -> bypassfast.AsyncClient:
    options.setdefault("retry_policy", bypassfast.RetryPolicy(max_retries=0))
    options.setdefault("base_url", BASE_URL)
    client = bypassfast.AsyncClient(
        API_KEY,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(api)),
        **options,
    )
    client._core.clock = clock or FakeClock()
    client._core.jitter = jitter
    return client


def error(code: str, status: int, **extra: Any) -> httpx.Response:
    return reply(status, {"error": code, **extra})


def keys(body: Any) -> list[str]:
    assert isinstance(body, dict)
    return list(body)
