"""Error envelope decoding, error formatting, retryability and retry hints."""

from __future__ import annotations

import pickle
from email.utils import formatdate

import pytest

import bypassfast
from bypassfast import APIError, ResponseError, ResponseMeta, ValidationError, is_error_code
from bypassfast._core import decode_error_envelope, parse_retry_after, parse_retry_after_ms

from .support import FakeAPI, FakeClock, make_client, reply

NOW = 1_790_000_000.0


# -- envelope ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code", "stage"),
    [
        (
            '{"error":"domain_not_allowed","message":"target is not allowed"}',
            "domain_not_allowed",
            "",
        ),
        (
            '{"success":false,"error_code":"unsupported_script","message":"fresh","stage":"extract"}',
            "unsupported_script",
            "extract",
        ),
        ('{"error":"","error_code":"legacy"}', "legacy", ""),
        ('{"error":"wins","error_code":"loses"}', "wins", ""),
    ],
)
def test_both_envelopes_decode(body: str, code: str, stage: str) -> None:
    api = FakeAPI(reply(422, body, headers={"X-Request-ID": "req-error"}))
    with pytest.raises(APIError) as excinfo:
        make_client(api).kasada.cd()
    err = excinfo.value
    assert (err.code, err.stage, err.response.request_id) == (code, stage, "req-error")
    assert err.response.status_code == 422
    assert is_error_code(err, code)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"[]",
        b"null",
        b'"solver_busy"',
        b"<html>Bad gateway</html>",
        b'{"error":123}',
        b'{"error":"solver_busy","message":5}',
        b'{"error":"solver_busy","reason":["x"]}',
        b"\xff\xfe not utf-8",
    ],
)
def test_malformed_envelopes_become_http_error(body: bytes) -> None:
    envelope = decode_error_envelope(body)
    assert envelope == ("http_error", "", "", "", 0.0)


def test_null_string_fields_are_empty() -> None:
    envelope = decode_error_envelope(b'{"error":"x","message":null,"stage":null}')
    assert (envelope.code, envelope.message, envelope.stage) == ("x", "", "")


def test_proxy_error_carries_reason_and_is_not_retried() -> None:
    body = (
        '{"error":"proxy_error","message":"your proxy rejected the credentials (407)",'
        '"stage":"prelude","reason":"proxy_auth_failed"}'
    )
    api = FakeAPI(reply(424, body))
    client = make_client(api, retry_policy=bypassfast.RetryPolicy(max_retries=3))
    with pytest.raises(APIError) as excinfo:
        client.perimeterx.init(url="https://www.example.com/", proxy="http://u:p@h:1")
    err = excinfo.value
    assert (err.code, err.reason, err.stage) == ("proxy_error", "proxy_auth_failed", "prelude")
    assert not err.retryable
    assert len(api.calls) == 1


# -- formatting ------------------------------------------------------------------


def test_error_strings() -> None:
    with_id = ResponseMeta(status_code=429, request_id="req-1")
    without_id = ResponseMeta(status_code=502)
    secret = "message-echoing-input"

    assert str(APIError(with_id, "solver_busy", message=secret)) == (
        "bypassfast: solver_busy (status 429, request req-1)"
    )
    assert str(APIError(without_id, "", message=secret)) == "bypassfast: http_error (status 502)"
    assert secret not in repr(APIError(with_id, "x", message=secret))

    cause = ValueError("boom")
    assert str(ResponseError(with_id, cause)) == (
        "bypassfast: invalid response (status 429, request req-1): boom"
    )
    assert (
        str(ResponseError(without_id, cause)) == "bypassfast: invalid response (status 502): boom"
    )
    assert str(ValidationError("script_id", "must be hex")) == (
        "bypassfast: invalid script_id: must be hex"
    )
    assert str(ValidationError("", "plain message")) == "bypassfast: plain message"
    assert str(bypassfast.RequestError("POST /v1/solve/akamai", OSError("reset"))) == (
        "bypassfast: POST /v1/solve/akamai: reset"
    )


def test_errors_share_a_base_class_and_pickle() -> None:
    err = APIError(
        ResponseMeta(status_code=503, attempts=2), "edge_unavailable", "m", "s", "r", 1.5
    )
    for exc in (err, ValidationError("f", "m"), ResponseError(ResponseMeta(), ValueError("x"))):
        assert isinstance(exc, bypassfast.BypassFastError)
    clone = pickle.loads(pickle.dumps(err))
    assert (clone.code, clone.retry_after, clone.response.attempts) == ("edge_unavailable", 1.5, 2)


def test_is_error_code_walks_the_cause_chain() -> None:
    api_error = APIError(ResponseMeta(status_code=409), "script_cache_miss")
    outer = RuntimeError("wrapped")
    outer.__cause__ = api_error
    assert is_error_code(api_error, "script_cache_miss")
    assert is_error_code(outer, "script_cache_miss")
    assert not is_error_code(outer, "solver_busy")
    loop = RuntimeError("self-caused")
    loop.__cause__ = loop
    assert not is_error_code(loop, "x")
    assert not is_error_code(ValueError("x"), "x")
    assert not is_error_code(None, "x")


# -- retryable table -------------------------------------------------------------

NOT_RETRYABLE = [
    "proxy_error",
    "quota_exceeded",
    "billing_disabled",
    "hard_block",
    "unsupported_challenge",
    "unsupported_script",
    "solve_failed",
    "not_verified",
    "solve_timeout",
    "script_cache_miss",
]
RETRYABLE = [
    "solver_busy",
    "rate_limited",
    "quota_unavailable",
    "replay_unavailable",
    "edge_unavailable",
    "solver_unavailable",
    "api_key_store_unavailable",
    "org_status_unavailable",
    "cf_allowlist_unavailable",
    "request_cancelled",
    "internal",
    "internal_error",
    "no_devices_available",
    "device_unavailable",
    "script_cache_unavailable",
    "captcha_builder_unavailable",
    "target_error",
]


@pytest.mark.parametrize("code", NOT_RETRYABLE)
@pytest.mark.parametrize("status", [400, 424, 429, 500, 503])
def test_never_retryable_codes(code: str, status: int) -> None:
    assert not APIError(ResponseMeta(status_code=status), code).retryable


@pytest.mark.parametrize("code", RETRYABLE)
@pytest.mark.parametrize("status", [400, 424, 429, 503])
def test_always_retryable_codes(code: str, status: int) -> None:
    assert APIError(ResponseMeta(status_code=status), code).retryable


@pytest.mark.parametrize(
    ("status", "retryable"),
    [
        (307, False),
        (400, False),
        (404, False),
        (499, False),
        (500, True),
        (504, True),
        (599, True),
        (600, False),
    ],
)
def test_other_codes_follow_the_status(status: int, retryable: bool) -> None:
    assert APIError(ResponseMeta(status_code=status), "http_error").retryable is retryable
    assert APIError(ResponseMeta(status_code=status), "brand_new_code").retryable is retryable


# -- retry hints -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "seconds"),
    [
        ('{"error":"solver_busy","retry_after_ms":1500}', 1.5),
        ('{"error":"solver_busy","retry_after_ms":2.5}', 0.0025),
        ('{"error":"solver_busy"}', 0.0),
        ('{"error":"solver_busy","retry_after_ms":null}', 0.0),
        ('{"error":"solver_busy","retry_after_ms":0}', 0.0),
        ('{"error":"solver_busy","retry_after_ms":-5}', 0.0),
        ('{"error":"solver_busy","retry_after_ms":"1500"}', 0.0),
        ('{"error":"solver_busy","retry_after_ms":{}}', 0.0),
        ('{"error":"solver_busy","retry_after_ms":true}', 0.0),
        ('{"error":"solver_busy","retry_after_ms":1e400}', 0.0),
        ('{"error":"solver_busy","retry_after_ms":1e12}', 3600.0),
        ('{"error":"solver_busy","retry_after_ms":3600000}', 3600.0),
        ('{"success":false,"error_code":"solver_busy","retry_after_ms":750}', 0.75),
    ],
)
def test_retry_after_ms_never_discards_the_code(body: str, seconds: float) -> None:
    envelope = decode_error_envelope(body.encode())
    assert envelope.code == "solver_busy"
    assert envelope.retry_after == pytest.approx(seconds)


def test_retry_after_ms_huge_integer_is_capped() -> None:
    assert parse_retry_after_ms(10**30) == 3600.0
    assert parse_retry_after_ms(10**400) == 0.0  # overflows a float, like Go's decoder


@pytest.mark.parametrize(
    ("header", "seconds"),
    [
        ("2", 2.0),
        ("  7 ", 7.0),
        ("+3", 3.0),
        ("0", 0.0),
        ("-5", 0.0),
        ("3599", 3599.0),
        ("3600", 3600.0),
        ("9223372036854775807", 3600.0),
        ("99999999999999999999", 0.0),  # beyond int64 and not a date
        ("", 0.0),
        ("soon", 0.0),
        ("1.5", 0.0),
        (formatdate(NOW + 3, usegmt=True), 3.0),
        (formatdate(NOW - 30, usegmt=True), 0.0),
        (formatdate(NOW, usegmt=True), 0.0),
        (formatdate(NOW + 7200, usegmt=True), 3600.0),
    ],
)
def test_retry_after_header(header: str, seconds: float) -> None:
    assert parse_retry_after(header, NOW) == pytest.approx(seconds)


def test_retry_after_obsolete_date_formats() -> None:
    now = 784111770.0  # Sun, 06 Nov 1994 08:49:30 GMT
    assert parse_retry_after("Sunday, 06-Nov-94 08:49:37 GMT", now) == pytest.approx(7.0)
    assert parse_retry_after("Sun Nov  6 08:49:37 1994", now) == pytest.approx(7.0)


@pytest.mark.parametrize(
    ("body", "seconds"),
    [
        ('{"error":"solver_busy"}', 2.0),
        ('{"error":"solver_busy","retry_after_ms":300}', 0.3),
        ('{"error":"solver_busy","retry_after_ms":"soon"}', 2.0),
    ],
)
def test_body_hint_wins_over_the_header(body: str, seconds: float) -> None:
    api = FakeAPI(reply(429, body, headers={"Retry-After": "2"}))
    with pytest.raises(APIError) as excinfo:
        make_client(api).kasada.cd()
    assert excinfo.value.retry_after == pytest.approx(seconds)


def test_http_date_header_uses_the_client_clock() -> None:
    clock = FakeClock()
    header = formatdate(clock.time() + 4, usegmt=True)
    api = FakeAPI(reply(503, '{"error":"edge_unavailable"}', headers={"Retry-After": header}))
    with pytest.raises(APIError) as excinfo:
        make_client(api, clock=clock).kasada.cd()
    assert excinfo.value.retry_after == pytest.approx(4.0)
