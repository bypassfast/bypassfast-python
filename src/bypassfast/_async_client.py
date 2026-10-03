"""Asynchronous (asyncio) client. Same surface as :class:`bypassfast.Client`."""

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


class AsyncClient:
    """Asynchronous Bypass Fast client for asyncio. Takes the same options as
    :class:`bypassfast.Client`, with ``http_client`` an ``httpx.AsyncClient``.

    Cancelling a call (``asyncio.CancelledError``, ``asyncio.wait_for``)
    interrupts a retry backoff immediately.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = const.DEFAULT_BASE_URL,
        http_client: httpx.AsyncClient | None = None,
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
        if http_client is not None and not isinstance(http_client, httpx.AsyncClient):
            raise ValidationError("http_client", "must be an httpx.AsyncClient")
        self._owns_http = http_client is None
        self._http = (
            http_client if http_client is not None else httpx.AsyncClient(follow_redirects=False)
        )
        self.akamai = AsyncAkamaiService(self)
        self.kasada = AsyncKasadaService(self)
        self.incapsula = AsyncIncapsulaService(self)
        self.perimeterx = AsyncPerimeterxService(self)

    async def balance(self) -> Balance:
        """Return the current prepaid account balance."""
        return await self._run(calls.balance(self._core))

    async def solve(self, solver: const.Solver | str, request: Mapping[str, Any]) -> RawResponse:
        """Send ``request`` as-is to ``POST /v1/solve/{solver}`` (escape hatch)."""
        return await self._run(calls.generic_solve(self._core, solver, request))

    async def aclose(self) -> None:
        """Close the underlying HTTP client if this client created it."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # -- pipeline -------------------------------------------------------------

    async def _run(self, call: Call[T]) -> T:
        core = self._core
        try:
            meta, payload = await self._exchange(core.prepare(call, call.wire))
        except APIError as err:
            wire = call.fallback(err) if call.fallback is not None else None
            if wire is None:
                raise
            prior = err.response.attempts
            try:
                meta, payload = await self._exchange(core.prepare(call, wire))
            except (APIError, ResponseError) as fallback_err:
                merge_fallback_attempts(fallback_err, prior)
                raise
            meta.attempts += prior
        return core.finish(call, meta, payload)

    async def _exchange(self, prepared: Prepared) -> tuple[ResponseMeta, bytes]:
        state = self._core.retry_state()
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._attempt(prepared, attempt)
            except APIError as err:
                delay = state.next_delay(err)
                if delay is None:
                    raise
            if delay > 0:
                await self._core.clock.async_sleep(delay)

    async def _attempt(self, prepared: Prepared, attempt: int) -> tuple[ResponseMeta, bytes]:
        core = self._core
        started = core.clock.monotonic()
        try:
            response = await self._http.send(
                core.http_request(prepared), stream=True, follow_redirects=False
            )
        except TRANSPORT_ERRORS as exc:
            raise RequestError(prepared.operation, exc) from exc
        try:
            meta, reader = core.open_body(response, attempt, started, prepared.timeout)
            if reader.pending:
                try:
                    async for chunk in response.aiter_raw():
                        reader.feed(chunk)
                except httpx.HTTPError as exc:
                    raise reader.read_failed(exc) from exc
            payload = reader.finish()
        finally:
            await response.aclose()
        return core.complete(response, meta, payload)


class _AsyncService:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client


class AsyncAkamaiService(_AsyncService):
    """Akamai Bot Manager: ``sensor``, ``sbsd``, ``cpt`` and ``sec_cpt``."""

    async def sensor(self, **request: Unpack[AkamaiSensorRequest]) -> AkamaiSensorResponse:
        """Generate Bot Manager ``sensor_data`` (see :class:`AkamaiSensorRequest`)."""
        return await self._client._run(calls.akamai_sensor(self._client._core, request))

    async def sbsd(self, **request: Unpack[AkamaiSBSDRequest]) -> AkamaiSBSDResponse:
        """Generate an encrypted SBSD body (see :class:`AkamaiSBSDRequest`)."""
        return await self._client._run(calls.akamai_sbsd(self._client._core, request))

    async def cpt(self, **request: Unpack[AkamaiCPTRequest]) -> AkamaiCPTResponse:
        """Solve a Sec-CPT proof of work."""
        return await self._client._run(calls.akamai_cpt(self._client._core, request))

    async def sec_cpt(self, **request: Unpack[AkamaiSecCPTRequest]) -> AkamaiSecCPTResponse:
        """Solve a sec-cpt challenge page; ``body`` is the exact verify body."""
        return await self._client._run(calls.akamai_sec_cpt(self._client._core, request))


class AsyncKasadaService(_AsyncService):
    """Kasada: ``sensor`` and ``cd``."""

    async def sensor(self, **request: Unpack[KasadaSensorRequest]) -> KasadaSensorResponse:
        """Generate the ``/tms`` payload and ``x-kpsdk-*`` headers from ``p.js``."""
        return await self._client._run(calls.kasada_sensor(self._client._core, request))

    async def cd(self, **request: Unpack[KasadaCDRequest]) -> KasadaCDResponse:
        """Solve the CD proof of work; ``payload`` is the ``x-kpsdk-cd`` value."""
        return await self._client._run(calls.kasada_cd(self._client._core, request))


class AsyncIncapsulaService(_AsyncService):
    """Imperva / Incapsula: ``reese84`` and ``utmvc``."""

    async def reese84(self, **request: Unpack[IncapsulaReese84Request]) -> IncapsulaResponse:
        """Generate a Reese84 sensor payload."""
        return await self._client._run(calls.incapsula_reese84(self._client._core, request))

    async def utmvc(self, **request: Unpack[IncapsulaUTMVCRequest]) -> IncapsulaResponse:
        """Generate the ``___utmvc`` cookie and submission path."""
        return await self._client._run(calls.incapsula_utmvc(self._client._core, request))


class AsyncPerimeterxService(_AsyncService):
    """PerimeterX / HUMAN: ``init`` and ``solve_hold``."""

    async def init(self, **request: Unpack[PerimeterxInitRequest]) -> PerimeterxResponse:
        """Run the page-sensor session; returns HUMAN cookies and a session."""
        return await self._client._run(calls.perimeterx_init(self._client._core, request))

    async def solve_hold(self, **request: Unpack[PerimeterxHoldRequest]) -> PerimeterxResponse:
        """Solve the press-and-hold challenge behind a blocked response."""
        return await self._client._run(calls.perimeterx_hold(self._client._core, request))
