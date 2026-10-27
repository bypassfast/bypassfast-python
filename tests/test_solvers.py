"""Typed solver methods: wire contracts, script memory and fallbacks."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest

import bypassfast
from bypassfast import (
    APIError,
    PerimeterxCookie,
    RetryPolicy,
    ValidationError,
    akamai_script_id,
    script_id,
)
from bypassfast._memory import ScriptMemory

from .support import FakeAPI, Recorded, error, keys, make_client, reply

MIB = 1024 * 1024
SENSOR_OK = reply(
    200,
    {
        "cost": 0.002,
        "success": True,
        "sensor_data": "sensor",
        "ua": "ua",
        "session": "session",
        "script_id": "server-id",
    },
)
CACHE_MISS = reply(409, {"error_code": "script_cache_miss"})


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# -- Akamai sensor ---------------------------------------------------------------


def test_akamai_sensor_travels_hash_first_and_uploads_once() -> None:
    api = FakeAPI(CACHE_MISS, SENSOR_OK, SENSOR_OK)
    client = make_client(api, compression_threshold=-1)
    script = b"raw <sensor> javascript"
    request: dict[str, Any] = {
        "url": "https://www.example.com/",
        "script": script,
        "script_url": "https://www.example.com/a.js",
    }

    first = client.akamai.sensor(**request)
    second = client.akamai.sensor(**request)

    assert first.sensor_data == "sensor"
    assert first.script_id == "server-id"
    assert first.response.attempts == 2
    assert second.response.attempts == 1
    digest = hashlib.sha256(script).hexdigest()
    compact, upload, again = api.bodies
    assert "script" not in compact
    assert compact["script_id"] == digest
    assert (upload["script"], upload["script_id"]) == (b64(script), digest)
    assert "script" not in again
    assert again["script_id"] == digest
    assert all("config" not in body for body in api.bodies)
    assert keys(upload) == [
        "mode",
        "url",
        "ua",
        "abck",
        "bm_sz",
        "script",
        "script_id",
        "script_url",
    ]


def test_akamai_explicit_script_id_tries_the_remote_cache_first() -> None:
    script = b"raw script available for fallback"
    api = FakeAPI(CACHE_MISS, reply(200, {"success": True, "sensor_data": "sensor"}))
    client = make_client(api, compression_threshold=-1)

    result = client.akamai.sensor(script=script, script_id=akamai_script_id(script).upper())

    assert result.response.attempts == 2
    assert result.script_id == akamai_script_id(script)  # server omitted it
    compact, fallback = api.bodies
    assert "script" not in compact
    assert compact["script_id"] == akamai_script_id(script)
    assert fallback["script"] == b64(script)


def test_akamai_script_id_alone_returns_the_miss() -> None:
    api = FakeAPI(CACHE_MISS)
    client = make_client(api)
    with pytest.raises(APIError) as excinfo:
        client.akamai.sensor(script_id=script_id(b"not at hand"))
    assert excinfo.value.code == "script_cache_miss"
    assert len(api.calls) == 1


def test_akamai_failed_fallback_counts_both_exchanges() -> None:
    script = b"script"
    api = FakeAPI(CACHE_MISS, error("unsupported_script", 422))
    client = make_client(api)
    with pytest.raises(APIError) as excinfo:
        client.akamai.sensor(script=script, script_id=akamai_script_id(script))
    assert excinfo.value.code == "unsupported_script"
    assert excinfo.value.response.attempts == 2


def test_akamai_cache_unavailable_is_retried_before_the_fallback() -> None:
    script = b"script"
    unavailable = reply(503, {"error_code": "script_cache_unavailable"})
    api = FakeAPI(unavailable, unavailable, reply(200, {"success": True}))
    client = make_client(api, retry_policy=RetryPolicy(max_retries=1))
    result = client.akamai.sensor(script=script, script_id=akamai_script_id(script))
    assert result.response.attempts == 3
    assert ["script" in body for body in api.bodies] == [False, False, True]


def test_akamai_script_id_validation() -> None:
    api = FakeAPI()
    client = make_client(api)
    with pytest.raises(ValidationError) as excinfo:
        client.akamai.sensor(script=b"script", script_id="0" * 64)
    assert (excinfo.value.field, excinfo.value.message) == (
        "script_id",
        "does not match the script bytes",
    )
    for bad in ("abc", "g" * 64, "0" * 63, " " + "0" * 63):
        with pytest.raises(ValidationError, match="64-character SHA-256 hex digest"):
            client.akamai.sensor(script_id=bad)
    assert api.calls == []


def test_akamai_session_drops_script_and_script_id() -> None:
    api = FakeAPI(SENSOR_OK, SENSOR_OK)
    client = make_client(api)
    client.akamai.sensor(session="sess", script=b"script", script_id="not-even-hex")
    client.akamai.sensor(session="sess", script=b"script")
    for body in api.bodies:
        assert "script" not in body
        assert "script_id" not in body
        assert body["session"] == "sess"
    assert len(client._core.scripts) == 0


def test_akamai_text_script_is_utf8_and_matches_the_helper() -> None:
    api = FakeAPI(CACHE_MISS, SENSOR_OK)
    make_client(api).akamai.sensor(script="var é = 1;")
    assert api.bodies[0]["script_id"] == script_id("var é = 1;")
    assert api.bodies[1]["script"] == b64("var é = 1;".encode())
    assert akamai_script_id("var é = 1;") == script_id("var é = 1;".encode())
    assert script_id(b"") == hashlib.sha256(b"").hexdigest()


def test_akamai_sensor_full_wire_order() -> None:
    api = FakeAPI(SENSOR_OK)
    make_client(api).akamai.sensor(
        url="u",
        ua="a",
        abck="c",
        bm_sz="b",
        script=b"s",
        script_url="su",
        accept_language="en",
        language="en-US",
        timezone="UTC",
        telemetry=True,
    )
    assert keys(api.bodies[0]) == [
        "mode",
        "url",
        "ua",
        "abck",
        "bm_sz",
        "script_id",
        "script_url",
        "accept_language",
        "language",
        "timezone",
        "telemetry",
    ]


def test_akamai_sensor_sends_pace_only_when_set() -> None:
    api = FakeAPI(SENSOR_OK, SENSOR_OK)
    sensor = make_client(api).akamai.sensor
    sensor(url="u", ua="a", abck="c", bm_sz="b", script_url="su", session="prior")
    sensor(url="u", ua="a", abck="c", bm_sz="b", script_url="su", session="prior", pace=True)
    assert "pace" not in api.bodies[0]
    assert api.bodies[1]["pace"] is True


def test_akamai_telemetry_and_client_hints_are_read() -> None:
    api = FakeAPI(
        reply(
            200,
            {
                "success": True,
                "sensor_data": "sensor",
                "telemetry": "a=&&&e=bm&&&sensor_data=c2Vuc29y",
                "client_hints": {"sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"'},
                "paced_ms": 1200,
            },
        )
    )
    result = make_client(api).akamai.sensor(session="s", telemetry=True)
    assert api.bodies[0]["telemetry"] is True
    assert result.telemetry == "a=&&&e=bm&&&sensor_data=c2Vuc29y"
    assert result.client_hints == {"sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"'}
    assert result.paced_ms == 1200
    plain = make_client(FakeAPI(SENSOR_OK)).akamai.sensor(session="s")
    assert (plain.telemetry, plain.client_hints, plain.paced_ms) == ("", {}, 0)


# -- Akamai SBSD, CPT, SecCPT ------------------------------------------------------


def test_akamai_sbsd_wire() -> None:
    api = FakeAPI(
        CACHE_MISS,
        reply(
            200,
            {
                "success": True,
                "body": "encrypted",
                "sbsd_post": "telemetry",
                "ind": 1,
                "wait_ms": 750,
                "session": "s1",
                "script_id": "id",
                "client_hints": {"sec-ch-ua-mobile": "?0"},
            },
        ),
    )
    client = make_client(api)
    result = client.akamai.sbsd(
        script=b"sbsd",
        sbsd_o="cookie",
        pace=True,
        sbsd_post="telemetry",
        resource_urls=["https://www.example.com/r.js"],
    )
    compact, upload = api.bodies
    assert keys(compact) == [
        "mode",
        "url",
        "ua",
        "script_id",
        "sbsd_post",
        "pace",
        "script_url",
        "sbsd_o",
        "resource_urls",
    ]
    assert compact["mode"] == "sbsd"
    assert compact["script_id"] == script_id(b"sbsd")
    assert (compact["url"], compact["ua"], compact["script_url"]) == ("", "", "")
    assert upload["script"] == b64(b"sbsd")
    assert keys(upload)[3:5] == ["script", "script_id"]
    assert result.response.attempts == 2
    assert (result.body, result.sbsd_post, result.ind, result.wait_ms) == (
        "encrypted",
        "telemetry",
        1,
        750,
    )
    assert result.client_hints == {"sec-ch-ua-mobile": "?0"}


def test_akamai_sbsd_accepts_script_id_alone_and_requires_one() -> None:
    api = FakeAPI(reply(200, {"success": True}))
    client = make_client(api)
    cached = script_id(b"earlier")
    client.akamai.sbsd(script_id="  " + cached.upper() + "  ", session="s")
    assert "script" not in api.bodies[0]
    assert api.bodies[0]["script_id"] == cached
    with pytest.raises(ValidationError) as excinfo:
        client.akamai.sbsd(script_id="   ")
    assert excinfo.value.field == "script"
    with pytest.raises(ValidationError, match="64-character"):
        client.akamai.sbsd(script_id="cached")
    with pytest.raises(ValidationError, match="does not match"):
        client.akamai.sbsd(script=b"sbsd", script_id=cached)
    assert len(api.calls) == 1


def test_akamai_cpt_and_sec_cpt_send_zero_values() -> None:
    api = FakeAPI(reply(200, {"success": True, "answers": ["0.8"], "body": "{}"}))
    client = make_client(api)
    cpt = client.akamai.cpt()
    sec = client.akamai.sec_cpt(
        token="token",
        sec_cpt="prefix~1~rest",
        timestamp=1783387618,
        nonce="nonce",
        difficulty=10000,
        count=1,
    )
    assert api.bodies[0] == {"mode": "cpt", "token": "", "difficulty": 0}
    assert api.bodies[1] == {
        "mode": "sec_cpt",
        "token": "token",
        "sec_cpt": "prefix~1~rest",
        "timestamp": 1783387618,
        "nonce": "nonce",
        "difficulty": 10000,
        "count": 1,
    }
    assert cpt.answers == ["0.8"]
    assert sec.body == "{}"


# -- Kasada ------------------------------------------------------------------------


def test_kasada_sensor_wire_and_response() -> None:
    api = FakeAPI(
        reply(
            200,
            {
                "cost": 0.002,
                "payload": "p",
                "headers": {"x-kpsdk-ct": "ct", "x-kpsdk-v": "v"},
                "client_hints": {"sec-ch-ua": '"Chromium";v="155"'},
                "warnings": ["w"],
                "device_id": "d",
                "session": "s",
                "user_agent": "ua-out",
                "script_id": "srv",
                "cache_hit": True,
                "duration_ms": 41,
            },
        )
    )
    result = make_client(api).kasada.sensor(
        script=b"p.js source",
        ua="ua",
        page_origin="https://www.example.com",
        ancestor_origins=[],
        seed=2**64 - 1,
        force_pool_variation=False,
        now_ms=0,
        headers={"accept-language": "en-US"},
    )
    body = api.bodies[0]
    assert keys(body) == ["mode", "script_id", "ua", "page_origin", "seed", "headers"]
    assert body["script_id"] == script_id(b"p.js source")
    assert b'"seed":18446744073709551615' in api.calls[0].raw
    assert result.headers == {"x-kpsdk-ct": "ct", "x-kpsdk-v": "v"}
    assert result.client_hints == {"sec-ch-ua": '"Chromium";v="155"'}
    assert result.script_id == "srv"
    assert (result.cache_hit, result.duration_ms, result.warnings) == (True, 41, ["w"])


def test_kasada_sensor_uploads_once_and_cd_reuses_the_registration() -> None:
    api = FakeAPI(reply(409, {"error": "script_cache_miss"}), reply(200, {"payload": "p"}))
    client = make_client(api)
    source = "raw p.js"
    sensor = client.kasada.sensor(script=source, ua="ua")
    cd = client.kasada.cd(script=source, work_time=250)
    assert sensor.response.attempts == 2
    assert sensor.script_id == script_id(source)
    assert cd.response.attempts == 1
    assert cd.script_id == script_id(source)
    assert [body.get("script") for body in api.bodies] == [None, source, None]
    assert {body["script_id"] for body in api.bodies} == {script_id(source)}
    assert api.bodies[2]["mode"] == "cd"


@pytest.mark.parametrize("seed", [-1, 2**64, True, 1.5, "7"])
def test_kasada_seed_must_be_uint64(seed: Any) -> None:
    api = FakeAPI()
    with pytest.raises(ValidationError) as excinfo:
        make_client(api).kasada.sensor(script="s", ua="u", seed=seed)
    assert excinfo.value.field == "seed"
    assert api.calls == []


def test_kasada_cd_always_fields() -> None:
    api = FakeAPI(reply(200, {"payload": '{"answers":[1]}', "duration_ms": 9}))
    client = make_client(api)
    client.kasada.cd()
    client.kasada.cd(work_time=250, subchallenge_count=2, is_mobile=True, duration=1.25)
    assert api.bodies[0] == {
        "mode": "cd",
        "st": 0,
        "ct": "",
        "domain": "",
        "work_time": 0,
        "rst": 0,
        "d": 0,
        "id": "",
        "duration": 0,
        "fc": "",
    }
    second = api.bodies[1]
    assert (second["work_time"], second["subchallenge_count"]) == (250, 2)
    assert (second["is_mobile"], second["duration"]) == (True, 1.25)
    assert keys(second)[-2:] == ["subchallenge_count", "is_mobile"]


def test_unknown_keyword_arguments_are_rejected() -> None:
    client = make_client(FakeAPI())
    with pytest.raises(TypeError, match="unexpected keyword argument 'user_agent'"):
        client.kasada.sensor(script="s", user_agent="ua")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        client.incapsula.reese84(session_ids=["s"])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        client.incapsula.utmvc(pow="p")  # type: ignore[call-arg]


# -- Incapsula ---------------------------------------------------------------------


def test_incapsula_hash_first_and_fallback() -> None:
    ok = reply(200, {"payload": "payload", "backend": "replay", "duration_ms": 2})
    api = FakeAPI(reply(409, {"error": "script_cache_miss"}), ok, ok)
    client = make_client(api, compression_threshold=-1)
    request: dict[str, Any] = {
        "script": "raw script",
        "script_url": "https://www.example.com/loader",
        "url": "https://www.example.com/",
        "ua": "ua",
        "document_html": '<script async src="/static/build"></script>',
        "document_script_source_groups": [["https://www.example.com/static/build"]],
    }

    first = client.incapsula.reese84(**request)
    result = client.incapsula.reese84(**request)

    assert first.response.attempts == 2
    assert first.script_id == script_id("raw script")
    assert result.response.attempts == 1
    assert [body.get("script") for body in api.bodies] == [None, "raw script", None]
    for body in api.bodies:
        assert body["mode"] == "reese84"
        assert body["script_id"] == script_id("raw script")
        assert body["document_script_source_groups"] == [["https://www.example.com/static/build"]]
        assert body["document_html"]


def test_incapsula_recent_miss_sends_the_script_directly() -> None:
    api = FakeAPI(reply(409, {"error": "script_cache_miss"}), reply(200, {"payload": "p"}))
    client = make_client(api)
    base: dict[str, Any] = {"script_url": "https://www.example.com/a", "ua": "ua"}
    client.incapsula.reese84(script="one", **base)  # miss, then upload
    client.incapsula.reese84(script="one", **base)  # registered: compact again
    client.incapsula.utmvc(script="one", **base)  # other mode: compact
    client.incapsula.reese84(script="two", **base)  # other content: compact
    sent = ["script" in body for body in api.bodies]
    assert sent == [False, True, False, False, False]
    assert len(client._core.scripts) == 0


def test_incapsula_misses_without_a_script_are_returned() -> None:
    api = FakeAPI(reply(409, {"error": "script_cache_miss"}))
    client = make_client(api)
    with pytest.raises(APIError) as excinfo:
        client.incapsula.reese84(script="s", script_url="https://www.example.com/x")
    assert excinfo.value.response.attempts == 2  # hash-first, then the upload
    assert len(api.calls) == 2
    with pytest.raises(APIError) as excinfo:
        client.incapsula.reese84(script_id=script_id("s"), script_url="https://www.example.com/x")
    assert excinfo.value.response.attempts == 1
    assert len(api.calls) == 3
    assert client._core.scripts.recently_missed("incapsula:reese84:" + script_id("s"))


def test_incapsula_utmvc_wire() -> None:
    api = FakeAPI(
        reply(
            200,
            {
                "payload": "cookie",
                "cookie": "c",
                "cookie_name": "___utmvc",
                "submit_path": "/_Incapsula_Resource?SWKMTFSR=1",
                "script_id": "srv",
                "client_hints": {"sec-ch-ua-platform": '"macOS"'},
            },
        )
    )
    result = make_client(api).incapsula.utmvc(
        script=b"utmvc js",
        ua="ua",
        script_url="su",
        url="u",
        session_ids=["s1"],
        headers={"cookie": "visid=1"},
    )
    body = api.bodies[0]
    assert keys(body) == ["mode", "script_id", "ua", "script_url", "url", "headers", "session_ids"]
    assert body["session_ids"] == ["s1"]
    assert (result.cookie_name, result.submit_path) == (
        "___utmvc",
        "/_Incapsula_Resource?SWKMTFSR=1",
    )
    assert (result.script_id, result.client_hints) == ("srv", {"sec-ch-ua-platform": '"macOS"'})


# -- PerimeterX --------------------------------------------------------------------

PROXY = "socks5h://user:pass@proxy.example:1080"


def test_perimeterx_init_and_hold_wire() -> None:
    def respond(call: Recorded) -> Any:
        if call.body["mode"] == "init":
            return reply(
                200,
                {
                    "success": True,
                    "session": "sess-1",
                    "cost": 0.004,
                    "cookies": [
                        {
                            "name": "_pxvid",
                            "value": "v",
                            "domain": ".example.com",
                            "path": "/",
                            "expires": 1790000000,
                            "secure": True,
                        }
                    ],
                },
            )
        return reply(200, {"success": False, "cookies": [], "session": "sess-2", "cost": 0.004})

    api = FakeAPI(respond)
    client = make_client(api, compression_threshold=-1)
    init = client.perimeterx.init(
        url="https://www.example.com/en/booking",
        proxy=PROXY,
        ua="Mozilla/5.0 Chrome/153.0.0.0",
        platform="chrome-mac",
        cookies=[{"name": "ak_bmsc", "value": "x", "domain": ".example.com", "path": "/"}],
        headers={"x-custom": "value"},
    )
    hold = client.perimeterx.solve_hold(
        session=init.session,
        proxy=PROXY,
        blocked={
            "url": "https://www.example.com/api/v1/availability",
            "method": "POST",
            "status": 428,
            "headers": {"content-type": "application/json"},
            "body": "eyJhcHBJZCI6IlBYYWJjIn0=",
            "body_base64": True,
        },
        cookies=[
            {"name": "_abck", "value": "a", "domain": ".example.com", "path": "/"},
            PerimeterxCookie(name="_pxhd", value="h"),
        ],
    )

    assert (init.success, init.session, init.cost) == (True, "sess-1", 0.004)
    assert init.cookies[0] == PerimeterxCookie(
        name="_pxvid", value="v", domain=".example.com", path="/", expires=1790000000, secure=True
    )
    assert not init.rejected
    assert (hold.success, hold.rejected, hold.change_exit, hold.retry) == (False, True, False, None)

    init_wire, hold_wire = api.bodies
    assert keys(init_wire) == ["mode", "url", "proxy", "ua", "platform", "headers", "cookies"]
    assert init_wire["cookies"] == [
        {"name": "ak_bmsc", "value": "x", "domain": ".example.com", "path": "/"}
    ]
    assert keys(hold_wire) == ["mode", "proxy", "cookies", "session", "blocked"]
    # The cookies held at the block travel with the hold, HUMAN's included.
    assert hold_wire["cookies"] == [
        {"name": "_abck", "value": "a", "domain": ".example.com", "path": "/"},
        {"name": "_pxhd", "value": "h"},
    ]
    assert hold_wire["blocked"] == {
        "url": "https://www.example.com/api/v1/availability",
        "method": "POST",
        "status": 428,
        "headers": {"content-type": "application/json"},
        "body": "eyJhcHBJZCI6IlBYYWJjIn0=",
        "body_encoding": "base64",
    }


def test_perimeterx_init_without_ua_adopts_the_solver_draw() -> None:
    api = FakeAPI(
        reply(
            200,
            {
                "success": True,
                "cookies": [],
                "session": "s3",
                "ua": "Mozilla/5.0 Chrome/152.0.0.0",
                "cost": 0.004,
            },
        )
    )
    result = make_client(api).perimeterx.init(url="https://www.example.com/", proxy=PROXY)
    assert "ua" not in api.bodies[0]
    assert result.ua == "Mozilla/5.0 Chrome/152.0.0.0"


def test_perimeterx_init_adopts_the_session_accept_language() -> None:
    drawn = "en-GB,en;q=0.9"
    api = FakeAPI(
        reply(200, {"success": True, "cookies": [], "session": "s", "accept_language": drawn}),
        reply(200, {"success": True, "cookies": [], "session": "s"}),
    )
    client = make_client(api)
    # None set: not sent, and the result carries the value the API drew.
    result = client.perimeterx.init(url="https://www.example.com/", proxy=PROXY)
    assert "accept_language" not in api.bodies[0]
    assert result.accept_language == drawn
    # Set by the caller: sent, and kept when the API does not echo it.
    own = client.perimeterx.init(
        url="https://www.example.com/", proxy=PROXY, accept_language="tr-TR,tr;q=0.9"
    )
    assert api.bodies[1]["accept_language"] == "tr-TR,tr;q=0.9"
    assert own.accept_language == "tr-TR,tr;q=0.9"


def test_perimeterx_init_platform_auto_returns_the_drawn_platform() -> None:
    api = FakeAPI(
        reply(
            200,
            {
                "success": True,
                "cookies": [],
                "session": "s4",
                "ua": "Mozilla/5.0 (Macintosh) Chrome/155.0.0.0",
                "platform": "chrome-mac",
                "cost": 0.004,
            },
        )
    )
    result = make_client(api).perimeterx.init(
        url="https://www.example.com/", proxy=PROXY, platform="auto"
    )
    assert api.bodies[0]["platform"] == "auto"
    assert "ua" not in api.bodies[0]
    assert result.platform == "chrome-mac"
    assert result.ua == "Mozilla/5.0 (Macintosh) Chrome/155.0.0.0"


def test_perimeterx_cookies_from_a_response_can_be_sent_back() -> None:
    api = FakeAPI(reply(200, {"success": True}))
    cookie = PerimeterxCookie(name="_px3", value="v", http_only=True)
    make_client(api).perimeterx.init(url="https://www.example.com/", proxy=PROXY, cookies=[cookie])
    assert api.bodies[0]["cookies"] == [{"name": "_px3", "value": "v", "http_only": True}]


def test_perimeterx_bytes_body_is_base64_encoded() -> None:
    api = FakeAPI(reply(200, {"success": True}))
    make_client(api).perimeterx.solve_hold(
        session="s",
        proxy=PROXY,
        blocked={"url": "https://www.example.com/", "status": 403, "body": b"\xff<html>"},
    )
    blocked = api.bodies[0]["blocked"]
    assert blocked["body"] == b64(b"\xff<html>")
    assert blocked["body_encoding"] == "base64"


def test_perimeterx_hold_url_fallback_and_status_zero() -> None:
    api = FakeAPI(reply(200, {"success": True}))
    make_client(api).perimeterx.solve_hold(
        session="s", proxy=PROXY, url="https://www.example.com/page", blocked={}
    )
    assert api.bodies[0]["url"] == "https://www.example.com/page"
    assert api.bodies[0]["blocked"] == {"url": "", "status": 0, "body": ""}
    # No cookies given: no cookies key, and the solver refreshes the page itself.
    assert "cookies" not in api.bodies[0]


def test_perimeterx_retry_advice() -> None:
    api = FakeAPI(reply(200, {"success": False, "retry": {"change_exit": True, "reason": "ip"}}))
    result = make_client(api).perimeterx.init(url="https://www.example.com/", proxy=PROXY)
    assert result.rejected
    assert result.change_exit
    assert result.retry == bypassfast.PerimeterxRetryAdvice(change_exit=True, reason="ip")


@pytest.mark.parametrize(
    ("method", "kwargs", "field"),
    [
        ("init", {"proxy": "p"}, "url"),
        ("init", {"url": "https://e", "proxy": ""}, "proxy"),
        ("solve_hold", {"proxy": "p", "blocked": {"url": "https://e"}}, "session"),
        ("solve_hold", {"session": "s", "blocked": {"url": "https://e"}}, "proxy"),
        ("solve_hold", {"session": "s", "proxy": "p"}, "blocked"),
        ("solve_hold", {"session": "s", "proxy": "p", "blocked": {"status": 428}}, "blocked.url"),
    ],
)
def test_perimeterx_validation(method: str, kwargs: dict[str, Any], field: str) -> None:
    api = FakeAPI()
    with pytest.raises(ValidationError) as excinfo:
        getattr(make_client(api).perimeterx, method)(**kwargs)
    assert excinfo.value.field == field
    assert api.calls == []


def test_perimeterx_allows_two_mib_block_pages() -> None:
    api = FakeAPI(reply(200, {"success": True, "cookies": [], "session": "s"}))
    client = make_client(api, compression_threshold=-1)
    page = "<div id='px-captcha'></div>" * ((MIB + 512 * 1024) // 28)
    result = client.perimeterx.solve_hold(
        session="s", proxy="p", blocked={"url": "https://e", "status": 403, "body": page}
    )
    assert result.success
    assert MIB < len(api.calls[0].raw) <= 2 * MIB

    with pytest.raises(ValidationError, match="2 MiB"):
        client.perimeterx.solve_hold(
            session="s", proxy="p", blocked={"url": "https://e", "body": "x" * (2 * MIB)}
        )
    assert len(api.calls) == 1


# -- script memory -------------------------------------------------------------------


def test_script_memory_remembers_misses_briefly() -> None:
    now = 1000.0
    memory = ScriptMemory(2, lambda: now, ttl=60.0)
    memory.mark_missed("a")
    memory.mark_missed("b")
    assert memory.recently_missed("a")
    assert memory.recently_missed("b")
    memory.mark_missed("c")  # evicts the oldest miss, "a"
    assert "a" not in memory
    assert len(memory) == 2
    memory.mark_registered("b")
    assert not memory.recently_missed("b")
    now += 60.0
    assert not memory.recently_missed("c")
    assert len(memory) == 0
    memory.mark_missed("")
    memory.mark_registered("")
    assert not memory.recently_missed("")
    assert len(memory) == 0


def test_client_forgets_misses_after_256_scripts_and_after_a_minute() -> None:
    api = FakeAPI(reply(409, {"error_code": "script_cache_miss"}))
    client = make_client(api)
    clock = client._core.clock
    for index in range(257):
        with pytest.raises(APIError):
            client.akamai.sensor(script=f"script-{index}".encode())
    assert len(client._core.scripts) == 256
    before = len(api.calls)
    with pytest.raises(APIError):
        client.akamai.sensor(script=b"script-256")  # remembered: the script goes directly
    # A direct upload that still misses is not repeated.
    assert "script" in api.bodies[before]
    assert len(api.calls) == before + 1
    before = len(api.calls)
    with pytest.raises(APIError):
        client.akamai.sensor(script=b"script-0")  # evicted: hash-first again
    assert "script" not in api.bodies[before]
    clock.sleep(60.0)
    before = len(api.calls)
    with pytest.raises(APIError):
        client.akamai.sensor(script=b"script-256")  # expired: hash-first again
    assert "script" not in api.bodies[before]
