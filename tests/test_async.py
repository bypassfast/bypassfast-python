"""AsyncClient parity: the same pipeline, awaited."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx
import pytest

import bypassfast
from bypassfast import APIError, RetryPolicy, ValidationError, akamai_script_id

from .support import (
    API_KEY,
    UA_PREFIX,
    FakeAPI,
    FakeClock,
    error,
    keys,
    make_async_client,
    max_jitter,
    reply,
)

OK = reply(200, {"payload": "ok", "duration_ms": 1})


async def test_kasada_sensor_headers_gzip_and_meta() -> None:
    api = FakeAPI(
        reply(
            200,
            {"payload": "cipher", "headers": {"x-kpsdk-ct": "ct"}},
            headers={"X-Request-ID": "req-a"},
            gzipped=True,
        )
    )
    async with make_async_client(api, user_agent="worker/2") as client:
        result = await client.kasada.sensor(
            script="var x = 1;", ua="ua", referrer="https://www.example.com/" + "p/" * 600
        )

    (call,) = api.calls
    assert call.headers["X-API-Key"] == API_KEY
    assert call.headers["User-Agent"] == f"{UA_PREFIX} worker/2"
    assert call.headers["Content-Encoding"] == "gzip"
    assert call.headers["Content-Length"] == str(len(call.raw))
    assert call.body["mode"] == "sensor"
    assert result.payload == "cipher"
    assert result.headers == {"x-kpsdk-ct": "ct"}
    assert (result.response.request_id, result.response.attempts) == ("req-a", 1)


async def test_akamai_hash_first_flow_and_fallback() -> None:
    ok = reply(200, {"success": True, "sensor_data": "sensor"})
    api = FakeAPI(reply(409, {"error_code": "script_cache_miss"}), ok, ok)
    client = make_async_client(api, compression_threshold=-1)
    script = b"raw script"

    first = await client.akamai.sensor(script=script)
    second = await client.akamai.sensor(script=script)

    assert first.response.attempts == 2
    assert second.response.attempts == 1
    assert second.script_id == hashlib.sha256(script).hexdigest()
    assert ["script" in body for body in api.bodies] == [False, True, False]


async def test_akamai_explicit_script_id_validation() -> None:
    client = make_async_client(FakeAPI())
    with pytest.raises(ValidationError):
        await client.akamai.sensor(script=b"a", script_id=akamai_script_id(b"b"))


async def test_incapsula_hash_first_flow_and_fallback() -> None:
    ok = reply(200, {"payload": "p", "backend": "replay"})
    api = FakeAPI(reply(409, {"error": "script_cache_miss"}), ok, ok)
    client = make_async_client(api)
    request: dict[str, Any] = {
        "script": "raw script",
        "script_url": "https://www.example.com/l",
        "ua": "ua",
    }

    first = await client.incapsula.reese84(**request)
    result = await client.incapsula.reese84(**request)

    assert first.response.attempts == 2
    assert result.response.attempts == 1
    assert [body.get("script") for body in api.bodies] == [None, "raw script", None]
    assert {body["script_id"] for body in api.bodies} == {hashlib.sha256(b"raw script").hexdigest()}


async def test_perimeterx_init_hold_and_validation() -> None:
    api = FakeAPI(
        reply(200, {"success": True, "session": "s1", "cookies": [{"name": "_px3", "value": "v"}]})
    )
    client = make_async_client(api)
    init = await client.perimeterx.init(url="https://www.example.com/", proxy="http://u:p@h:1")
    await client.perimeterx.solve_hold(
        session=init.session,
        proxy="http://u:p@h:1",
        blocked={"url": "https://www.example.com/", "status": 428},
    )
    assert init.cookies[0].name == "_px3"
    assert [body["mode"] for body in api.bodies] == ["init", "holdcaptcha"]
    assert api.calls[0].request.extensions["timeout"]["read"] == 155.0
    with pytest.raises(ValidationError):
        await client.perimeterx.solve_hold(session="s", proxy="p")  # type: ignore[call-arg]
    assert len(api.calls) == 2


async def test_balance_solve_and_other_services() -> None:
    api = FakeAPI(
        reply(
            200,
            {
                "balance": 3.5,
                "balance_cents": 350,
                "currency": "USD",
                "success": True,
                "answers": ["a"],
            },
        )
    )
    client = make_async_client(api)
    balance = await client.balance()
    raw = await client.solve("kasada", {"mode": "cd"})
    cpt = await client.akamai.cpt(token="t", difficulty=1)
    await client.akamai.sec_cpt(token="t")
    await client.akamai.sbsd(script=b"s")
    await client.kasada.cd(st=1)
    await client.incapsula.utmvc(script="u", session_ids=["a"])
    assert (balance.balance, balance.balance_cents) == (3.5, 350)
    assert raw.raw["success"] is True
    assert cpt.answers == ["a"]
    assert api.calls[0].request.method == "GET"
    assert [call.request.url.path for call in api.calls[1:]] == [
        "/v1/solve/kasada",
        "/v1/solve/akamai",
        "/v1/solve/akamai",
        "/v1/solve/akamai",
        "/v1/solve/kasada",
        "/v1/solve/incapsula",
    ]
    assert keys(api.bodies[2]) == ["mode", "token", "difficulty"]


async def test_retry_loop_uses_the_async_sleeper() -> None:
    busy = error("solver_busy", 429, retry_after_ms=250)
    api = FakeAPI(busy, error("solver_unavailable", 503), OK)
    clock = FakeClock()
    client = make_async_client(
        api, clock=clock, jitter=max_jitter, retry_policy=RetryPolicy(1, 0.5, 8)
    )
    result = await client.kasada.cd()
    assert result.response.attempts == 3
    assert clock.sleeps == [0.5, 0.5]  # busy: 250 ms floor, window 500 ms; then 0.5 s backoff


async def test_errors_redirects_and_transport_failures() -> None:
    api = FakeAPI(
        reply(
            403,
            {"error": "domain_not_allowed", "message": "secret-input"},
            headers={"X-Request-ID": "r1"},
        )
    )
    with pytest.raises(APIError) as excinfo:
        await make_async_client(api).kasada.cd()
    assert str(excinfo.value) == "bypassfast: domain_not_allowed (status 403, request r1)"

    def redirect(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.test"
        return httpx.Response(302, headers={"Location": "https://elsewhere.test/"})

    with pytest.raises(APIError) as excinfo:
        await make_async_client(redirect).kasada.cd()
    assert excinfo.value.response.status_code == 302

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(bypassfast.RequestError, match="POST /v1/solve/kasada: timed out"):
        await make_async_client(broken, retry_policy=RetryPolicy(3)).kasada.cd()


async def test_response_errors_and_limits() -> None:
    with pytest.raises(bypassfast.ResponseError):
        await make_async_client(FakeAPI(reply(200, "{broken"))).kasada.cd()
    with pytest.raises(ValidationError, match="1 MiB"):
        await make_async_client(FakeAPI()).kasada.sensor(script="x" * (1024 * 1024 + 1))


async def test_cancellation_interrupts_a_backoff() -> None:
    api = FakeAPI(error("solver_busy", 429, retry_after_ms=5000))
    client = bypassfast.AsyncClient(
        API_KEY,
        base_url="https://api.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(api)),
        retry_policy=RetryPolicy(max_retries=1),
    )
    task = asyncio.ensure_future(client.kasada.cd())
    while not api.calls:
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(api.calls) == 1


async def test_wait_for_timeout_interrupts_a_backoff() -> None:
    api = FakeAPI(error("edge_unavailable", 503, retry_after_ms=5000))
    client = bypassfast.AsyncClient(
        API_KEY,
        base_url="https://api.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(api)),
        retry_policy=RetryPolicy(max_retries=2),
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(client.kasada.cd(), timeout=0.05)
    assert len(api.calls) == 1


async def test_aclose_only_closes_an_owned_client() -> None:
    owned = bypassfast.AsyncClient(API_KEY)
    await owned.aclose()
    assert owned._http.is_closed
    supplied = httpx.AsyncClient()
    await bypassfast.AsyncClient(API_KEY, http_client=supplied).aclose()
    assert not supplied.is_closed
    await supplied.aclose()
    with pytest.raises(ValidationError):
        bypassfast.AsyncClient(API_KEY, http_client=httpx.Client())  # type: ignore[arg-type]
