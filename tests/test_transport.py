"""Request construction, body limits, response decoding and client options."""

from __future__ import annotations

import gzip
import json
import os
import threading
from typing import Any

import httpx
import pytest

import bypassfast
from bypassfast import _constants as const
from bypassfast._core import encode_json

from .support import (
    API_KEY,
    UA_PREFIX,
    FakeAPI,
    Recorded,
    keys,
    make_client,
    reply,
)

MIB = 1024 * 1024


# -- headers, compression and Content-Length ----------------------------------


def test_headers_user_agent_gzip_request_and_response_meta() -> None:
    api = FakeAPI(
        reply(
            200,
            {"cost": 0.002, "payload": "cipher", "user_agent": "ua-out", "duration_ms": 12},
            headers={
                "X-Request-ID": "req-123",
                "X-BypassFast-Edge": "edge-v1",
                "Server-Timing": "solve;dur=12",
            },
            gzipped=True,
        )
    )
    client = make_client(api, user_agent="  checkout/1.0 ")

    result = client.kasada.sensor(
        script="var challenge = 1;" * 200, ua="ua-in", accept_language="en-US"
    )

    (call,) = api.calls
    headers = call.headers
    assert call.request.method == "POST"
    assert str(call.request.url) == "https://api.test/v1/solve/kasada"
    assert headers["X-API-Key"] == API_KEY
    assert headers["Accept"] == "application/json"
    assert headers["Accept-Encoding"] == "gzip"
    assert headers["User-Agent"] == f"{UA_PREFIX} checkout/1.0"
    assert headers["Content-Type"] == "application/json"
    assert headers["Content-Encoding"] == "gzip"
    assert headers["Content-Length"] == str(len(call.raw))
    assert "Transfer-Encoding" not in headers
    assert "Idempotency-Key" not in headers
    assert keys(call.body)[0] == "mode"
    assert call.body["mode"] == "sensor"
    assert call.body["accept_language"] == "en-US"

    assert (result.payload, result.cost, result.user_agent) == ("cipher", 0.002, "ua-out")
    assert result.response == bypassfast.ResponseMeta(
        status_code=200,
        request_id="req-123",
        edge="edge-v1",
        server_timing="solve;dur=12",
        attempts=1,
    )


def test_default_user_agent_and_compact_utf8_json() -> None:
    api = FakeAPI(reply(200, {}))
    client = make_client(api)

    client.kasada.sensor(script="é", ua="ua")

    (call,) = api.calls
    assert call.headers["User-Agent"] == UA_PREFIX
    assert "Content-Encoding" not in call.headers
    assert call.raw == '{"mode":"sensor","script":"é","ua":"ua"}'.encode()


@pytest.mark.parametrize(
    ("threshold", "size", "compressed"),
    [
        (1024, 1023, False),  # below the default threshold
        (1024, 1024, True),  # at the threshold
        (0, 300, True),  # zero compresses every non-empty body
        (-1, 50_000, False),  # negative disables compression
    ],
)
def test_compression_threshold(threshold: int, size: int, compressed: bool) -> None:
    api = FakeAPI(reply(200, {}))
    client = make_client(api, compression_threshold=threshold)
    overhead = len(encode_json({"mode": "sensor", "script": "", "ua": ""}))

    client.kasada.sensor(script="a" * (size - overhead))

    (call,) = api.calls
    assert (call.headers.get("Content-Encoding") == "gzip") is compressed
    assert call.headers["Content-Length"] == str(len(call.raw))
    assert len(json.dumps(call.body, separators=(",", ":"))) == size


def test_gzip_only_used_when_it_saves_bytes() -> None:
    client = make_client(FakeAPI())
    core = client._core
    incompressible = os.urandom(4096)
    assert core.compress(incompressible, MIB) == (incompressible, "")
    compressible = b"a" * 4096
    body, encoding = core.compress(compressible, MIB)
    assert encoding == "gzip"
    assert gzip.decompress(body) == compressible
    # The compressed body must also fit the limit.
    assert core.compress(compressible, 10) == (compressible, "")


def test_one_mib_limit_is_local_and_exact() -> None:
    api = FakeAPI(reply(200, {}))
    client = make_client(api, compression_threshold=-1)
    overhead = len(encode_json({"mode": "sensor", "script": "", "ua": ""}))

    client.kasada.sensor(script="x" * (MIB - overhead))
    assert len(api.calls[0].raw) == MIB

    with pytest.raises(bypassfast.ValidationError) as excinfo:
        client.kasada.sensor(script="x" * (MIB - overhead + 1))
    assert excinfo.value.field == "request"
    assert (
        str(excinfo.value)
        == "bypassfast: invalid request: encoded JSON exceeds the 1 MiB API limit"
    )
    assert len(api.calls) == 1


def test_generic_solve_limits_per_solver() -> None:
    api = FakeAPI(reply(200, {"success": True}))
    client = make_client(api)
    big = {"mode": "init", "blob": "p" * (MIB + 10)}

    client.solve("perimeterx", big)
    with pytest.raises(bypassfast.ValidationError, match="1 MiB"):
        client.solve("akamai", big)
    with pytest.raises(bypassfast.ValidationError, match="2 MiB"):
        client.solve("perimeterx", {"blob": "p" * (2 * MIB)})
    assert len(api.calls) == 1


def test_unencodable_request_is_a_validation_error() -> None:
    client = make_client(FakeAPI())
    with pytest.raises(bypassfast.ValidationError) as excinfo:
        client.solve("kasada", {"mode": "sensor", "bad": object()})
    assert str(excinfo.value) == "bypassfast: invalid request: could not encode JSON"
    with pytest.raises(bypassfast.ValidationError):
        client.solve("kasada", {"mode": "sensor", "nan": float("nan")})


# -- responses ---------------------------------------------------------------


def test_pre_read_gzip_response_is_not_decoded_twice() -> None:
    # httpx decodes a response built with content= immediately; the SDK must
    # take those bytes as-is instead of gunzipping them again.
    def respond(_: Recorded) -> httpx.Response:
        return httpx.Response(
            200,
            content=gzip.compress(b'{"payload":"once"}'),
            headers={"Content-Encoding": "gzip"},
        )

    client = make_client(FakeAPI(respond))
    assert client.kasada.cd().payload == "once"


def test_four_mib_cap_applies_to_decoded_bytes() -> None:
    def body_of(size: int) -> bytes:
        prefix, suffix = b'{"payload":"', b'"}'
        return prefix + b"x" * (size - len(prefix) - len(suffix)) + suffix

    limit = 4 * MIB
    exact = make_client(FakeAPI(reply(200, body_of(limit), gzipped=True)))
    assert len(exact.kasada.cd().payload) > 4_000_000

    # A tiny gzip body that inflates past the cap is rejected.
    assert len(gzip.compress(body_of(limit + 1))) < 64 * 1024
    bomb = reply(200, body_of(limit + 1), gzipped=True)
    with pytest.raises(bypassfast.ResponseError, match="response exceeds 4194304 bytes"):
        make_client(FakeAPI(bomb)).kasada.cd()

    with pytest.raises(bypassfast.ResponseError, match="response exceeds 4194304 bytes"):
        make_client(FakeAPI(reply(200, body_of(limit + 1)))).kasada.cd()


def test_multi_member_gzip_response() -> None:
    raw = gzip.compress(b'{"payload":') + gzip.compress(b'"joined"}')
    client = make_client(FakeAPI(reply(200, raw, headers={"Content-Encoding": "gzip"})))
    assert client.kasada.cd().payload == "joined"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not gzip at all", "decode gzip response"),
        (gzip.compress(b'{"payload":"x"}')[:-6], "unexpected EOF"),
        (b"", "unexpected EOF"),
    ],
)
def test_corrupt_gzip_is_a_response_error(raw: bytes, message: str) -> None:
    client = make_client(FakeAPI(reply(200, raw, headers={"Content-Encoding": "gzip"})))
    with pytest.raises(bypassfast.ResponseError, match=message):
        client.kasada.cd()


def test_unsupported_encoding_is_a_response_error_and_not_retried() -> None:
    api = FakeAPI(reply(503, b"\x00", headers={"Content-Encoding": "br", "X-Request-ID": "r-9"}))
    client = make_client(api, retry_policy=bypassfast.RetryPolicy(max_retries=3))
    with pytest.raises(bypassfast.ResponseError) as excinfo:
        client.kasada.cd()
    assert str(excinfo.value) == (
        "bypassfast: invalid response (status 503, request r-9): "
        'unsupported response content encoding "br"'
    )
    assert excinfo.value.response.attempts == 1
    assert len(api.calls) == 1


def test_malformed_success_bodies_never_leak_into_errors() -> None:
    secret = "cookie-secret-must-not-appear"
    api = FakeAPI(reply(200, '{"cookie":"' + secret + '")', headers={"X-Request-ID": "req-m"}))
    with pytest.raises(bypassfast.ResponseError) as excinfo:
        make_client(api).kasada.cd()
    assert excinfo.value.response.request_id == "req-m"
    assert secret not in str(excinfo.value)
    assert str(excinfo.value).startswith(
        "bypassfast: invalid response (status 200, request req-m): "
    )


@pytest.mark.parametrize(
    "body",
    ['["not", "an", "object"]', '"text"', '{"payload": 42}', '{"duration_ms": "soon"}'],
)
def test_wrong_shapes_are_response_errors(body: str) -> None:
    with pytest.raises(bypassfast.ResponseError):
        make_client(FakeAPI(reply(200, body))).kasada.cd()


def test_empty_and_null_success_bodies_give_defaults_with_meta() -> None:
    for body in (b"", b"null"):
        result = make_client(FakeAPI(reply(204, body))).kasada.cd()
        assert (result.payload, result.cost, result.raw) == ("", 0.0, {})
        assert result.response.status_code == 204
        assert result.response.attempts == 1


def test_unknown_response_fields_are_ignored_but_kept_in_raw() -> None:
    api = FakeAPI(reply(200, {"payload": "p", "duration_ms": 3.0, "new_field": {"x": 1}}))
    result = make_client(api).kasada.cd()
    assert result.payload == "p"
    assert result.duration_ms == 3
    assert result.raw["new_field"] == {"x": 1}


# -- redirects, transport errors, balance and generic solve --------------------


@pytest.mark.parametrize("follow", [False, True])
def test_redirects_are_never_followed(follow: bool) -> None:
    redirected: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.test":
            redirected.append(request)
            return httpx.Response(200, json={"payload": "stolen"})
        return httpx.Response(307, headers={"Location": "https://elsewhere.test/collect"})

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=follow)
    client = make_client(FakeAPI(), http_client=http)
    with pytest.raises(bypassfast.APIError) as excinfo:
        client.kasada.cd()
    assert excinfo.value.response.status_code == 307
    assert excinfo.value.code == "http_error"
    assert redirected == []


def test_transport_errors_are_not_retried() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("connection reset", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = make_client(FakeAPI(), http_client=http, retry_policy=bypassfast.RetryPolicy(5))
    with pytest.raises(bypassfast.RequestError) as excinfo:
        client.kasada.cd()
    assert str(excinfo.value) == "bypassfast: POST /v1/solve/kasada: connection reset"
    assert excinfo.value.operation == "POST /v1/solve/kasada"
    assert isinstance(excinfo.value.cause, httpx.ConnectError)
    assert len(calls) == 1


def test_balance_is_a_bodyless_get() -> None:
    api = FakeAPI(
        reply(
            200,
            {
                "errorId": 0,
                "org_id": "org_1",
                "balance": 12.5,
                "balance_cents": 1250,
                "currency": "USD",
            },
        )
    )
    balance = make_client(api).balance()

    (call,) = api.calls
    assert call.request.method == "GET"
    assert str(call.request.url) == "https://api.test/balance"
    assert call.raw == b""
    assert "Content-Type" not in call.headers
    assert "Content-Length" not in call.headers
    assert (balance.error_id, balance.org_id, balance.balance) == (0, "org_1", 12.5)
    assert (balance.balance_cents, balance.currency) == (1250, "USD")


def test_generic_solve_sends_the_mapping_as_is() -> None:
    api = FakeAPI(reply(200, {"success": True, "answers": ["a"]}))
    result = make_client(api).solve("akamai", {"mode": "cpt", "future_field": [1, 2]})
    assert api.calls[0].request.url.path == "/v1/solve/akamai"
    assert api.calls[0].body == {"mode": "cpt", "future_field": [1, 2]}
    assert result.raw == {"success": True, "answers": ["a"]}
    assert result.response.attempts == 1


def test_generic_solve_validates_solver_and_request() -> None:
    client = make_client(FakeAPI())
    with pytest.raises(bypassfast.ValidationError) as excinfo:
        client.solve("../admin", {})
    assert str(excinfo.value) == (
        "bypassfast: invalid solver: must be akamai, kasada, incapsula, or perimeterx"
    )
    with pytest.raises(bypassfast.ValidationError, match="invalid request"):
        client.solve("kasada", None)  # type: ignore[arg-type]


# -- options -------------------------------------------------------------------


def _timeout_of(call: Recorded) -> Any:
    return call.request.extensions["timeout"]["read"]


def test_per_attempt_timeouts() -> None:
    api = FakeAPI(reply(200, {"success": True}))
    client = make_client(api)
    client.kasada.cd()
    client.perimeterx.init(url="https://www.example.com/", proxy="http://u:p@proxy:1")
    client.solve("perimeterx", {"mode": "init"})
    assert [_timeout_of(call) for call in api.calls] == [65.0, 155.0, 155.0]

    custom = make_client(api, timeout=5, perimeterx_timeout=7.5)
    custom.kasada.cd()
    custom.perimeterx.init(url="https://www.example.com/", proxy="http://u:p@proxy:1")
    assert [_timeout_of(call) for call in api.calls[3:]] == [5.0, 7.5]


def test_base_url_path_prefix_is_kept_and_trailing_slash_stripped() -> None:
    api = FakeAPI(reply(200, {}))
    make_client(api, base_url="https://gateway.test/bypass/").kasada.cd()
    assert str(api.calls[0].request.url) == "https://gateway.test/bypass/v1/solve/kasada"


@pytest.mark.parametrize(
    "url", ["http://localhost:8080", "http://LOCALHOST", "http://127.0.0.2:1", "http://[::1]:9"]
)
def test_plain_http_is_allowed_for_loopback(url: str) -> None:
    bypassfast.Client(API_KEY, base_url=url).close()


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"api_key": ""}, "api_key"),
        ({"api_key": " key "}, "api_key"),
        ({"api_key": "key\n"}, "api_key"),
        ({"api_key": "ke\ny"}, "api_key"),
        ({"base_url": "http://example.com"}, "base_url"),
        ({"base_url": "ftp://example.com"}, "base_url"),
        ({"base_url": "example.com"}, "base_url"),
        ({"base_url": "https://user:pw@example.com"}, "base_url"),
        ({"base_url": "https://example.com/?q=1"}, "base_url"),
        ({"base_url": "https://example.com/#f"}, "base_url"),
        ({"solver_busy_retry_budget": -1}, "solver_busy_retry_budget"),
        ({"solver_busy_retry_budget": 7200}, "solver_busy_retry_budget"),
        ({"user_agent": "  "}, "user_agent"),
        ({"user_agent": "app/1\r\nX-Evil: 1"}, "user_agent"),
        ({"timeout": 0}, "timeout"),
        ({"perimeterx_timeout": -5}, "perimeterx_timeout"),
        ({"compression_threshold": 1.5}, "compression_threshold"),
        ({"http_client": object()}, "http_client"),
    ],
)
def test_client_option_validation(kwargs: dict[str, Any], field: str) -> None:
    api_key = kwargs.pop("api_key", API_KEY)
    with pytest.raises(bypassfast.ValidationError) as excinfo:
        bypassfast.Client(api_key, **kwargs)
    assert excinfo.value.field == field
    assert isinstance(excinfo.value, ValueError)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"max_retries": -1}, "max_retries"),
        ({"max_retries": 11}, "max_retries"),
        ({"max_retries": True}, "max_retries"),
        ({"base_delay": -0.1}, "base_delay"),
        ({"max_delay": 3601}, "max_delay"),
        ({"base_delay": 1.0, "max_delay": 0}, "max_delay"),
        ({"base_delay": 2.0, "max_delay": 1.0}, "base_delay"),
        ({"base_delay": float("nan")}, "base_delay"),
    ],
)
def test_retry_policy_validation(kwargs: dict[str, Any], field: str) -> None:
    with pytest.raises(bypassfast.ValidationError) as excinfo:
        bypassfast.RetryPolicy(**kwargs)
    assert excinfo.value.field == field


def test_valid_retry_policy_edges() -> None:
    bypassfast.RetryPolicy(max_retries=0, base_delay=0, max_delay=0)
    bypassfast.RetryPolicy(max_retries=10, base_delay=3600, max_delay=3600)
    bypassfast.RetryPolicy(max_retries=1, base_delay=0, max_delay=5)


def test_close_only_closes_an_owned_http_client() -> None:
    with bypassfast.Client(API_KEY) as owned:
        http = owned._http
    assert http.is_closed

    supplied = httpx.Client()
    bypassfast.Client(API_KEY, http_client=supplied).close()
    assert not supplied.is_closed
    supplied.close()


def test_client_is_safe_for_concurrent_use() -> None:
    api = FakeAPI(reply(200, {"payload": "ok", "backend": "replay"}))
    client = make_client(api, compression_threshold=-1)
    failures: list[BaseException] = []

    def work() -> None:
        try:
            client.incapsula.reese84(
                script="shared script",
                script_url="https://www.example.com/loader.js",
                url="https://www.example.com/",
                ua="ua",
            )
        except BaseException as exc:  # pragma: no cover - reported below
            failures.append(exc)

    threads = [threading.Thread(target=work) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    assert len(api.calls) == 16


def test_constants_match_the_wire_contract() -> None:
    assert const.MAX_REQUEST_BYTES == 1_048_576
    assert const.MAX_PERIMETERX_REQUEST_BYTES == 2_097_152
    assert const.MAX_RESPONSE_BYTES == 4_194_304
    assert const.DEFAULT_BASE_URL == "https://api.bypass.fast"
    assert bypassfast.__version__ == "0.1.0"
