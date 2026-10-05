"""Wire builders: one I/O-free :class:`Call` per SDK method.

Field order follows the Go reference SDK. A field spec pairs each wire name
with its zero value, sent even when the caller leaves the field out, or with
``_OMIT``: omitted when None, "", False, 0 or an empty collection.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping, Sized
from dataclasses import fields as dataclass_fields
from typing import Any

from . import _constants as const
from ._core import Call, Core
from ._errors import APIError, ResponseMeta, ValidationError
from ._models import (
    AkamaiCPTResponse,
    AkamaiSBSDResponse,
    AkamaiSecCPTResponse,
    AkamaiSensorResponse,
    Balance,
    IncapsulaResponse,
    KasadaCDResponse,
    KasadaSensorResponse,
    PerimeterxCookie,
    PerimeterxResponse,
    RawResponse,
)

AKAMAI_PATH = "/v1/solve/akamai"
KASADA_PATH = "/v1/solve/kasada"
INCAPSULA_PATH = "/v1/solve/incapsula"
PERIMETERX_PATH = "/v1/solve/perimeterx"

_SCRIPT_CACHE_CODES = ("script_cache_miss", "script_cache_unavailable")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

_OMIT: Any = object()  # field spec marker: omit when empty


def _empty(value: object) -> bool:
    if value is None or value is False:
        return True
    if value is True:
        return False
    if isinstance(value, (int, float)):
        return value == 0
    return isinstance(value, Sized) and len(value) == 0


_Spec = tuple[tuple[str, Any], ...]


def _emit(wire: dict[str, Any], request: Mapping[str, Any], spec: _Spec) -> None:
    for name, zero in spec:
        value = request.get(name)
        if zero is _OMIT:
            if not _empty(value):
                wire[name] = value
        else:
            wire[name] = zero if value is None else value


def _check_keys(request: Mapping[str, Any], spec: _Spec, method: str) -> None:
    allowed = {name for name, _ in spec}
    for key in request:
        if key not in allowed:
            raise TypeError(f"{method}() got an unexpected keyword argument {key!r}")


def _raw_bytes(value: object, field: str) -> bytes:
    if value is None:
        return b""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        try:
            return value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValidationError(field, "must be valid UTF-8 text") from None
    raise ValidationError(field, "must be bytes or str")


def _text(value: object, field: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            raise ValidationError(field, "must be valid UTF-8") from None
    raise ValidationError(field, "must be str or bytes")


def _check_uint64(request: Mapping[str, Any], field: str) -> None:
    value = request.get(field)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 2**64:
        raise ValidationError(field, "must be an unsigned 64-bit integer")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def akamai_script_id(raw: bytes | str) -> str:
    """The ``script_id`` of raw Akamai JavaScript: the lower-case hex SHA-256
    of its bytes (``str`` is encoded as UTF-8)."""
    return _sha256_hex(_raw_bytes(raw, "script"))


# ---------------------------------------------------------------------------
# Akamai
# ---------------------------------------------------------------------------

_AKAMAI_SENSOR = (
    ("url", ""),
    ("ua", ""),
    ("abck", ""),
    ("bm_sz", ""),
    ("script", _OMIT),
    ("script_id", _OMIT),
    ("script_url", ""),
    ("accept_language", _OMIT),
    ("language", _OMIT),
    ("timezone", _OMIT),
    ("session", _OMIT),
)


def akamai_sensor(core: Core, request: Mapping[str, Any]) -> Call[AkamaiSensorResponse]:
    _check_keys(request, _AKAMAI_SENSOR, "akamai.sensor")
    script = _raw_bytes(request.get("script"), "script")
    script_id = ""
    full_script = ""
    used_compact = False
    if not request.get("session"):
        # With a session the API pins the script itself; script and
        # script_id are not sent.
        script_id = (request.get("script_id") or "").lower()
        explicit = script_id != ""
        if explicit and not _SHA256_HEX.fullmatch(script_id):
            raise ValidationError("script_id", "must be a 64-character SHA-256 hex digest")
        if script:
            computed = _sha256_hex(script)
            if explicit and script_id != computed:
                raise ValidationError("script_id", "does not match script bytes")
            script_id = computed
            full_script = base64.b64encode(script).decode("ascii")
        used_compact = bool(script) and (
            explicit or core.scripts.matches("akamai:" + script_id, script_id)
        )
    def build(script_field: str) -> dict[str, Any]:
        values = {**request, "script": script_field, "script_id": script_id}
        wire: dict[str, Any] = {"mode": "sensor"}
        _emit(wire, values, _AKAMAI_SENSOR)
        return wire

    def fallback(err: APIError) -> dict[str, Any] | None:
        if err.code in _SCRIPT_CACHE_CODES:
            return build(full_script)
        return None

    def finish(data: dict[str, Any], meta: ResponseMeta) -> AkamaiSensorResponse:
        result = AkamaiSensorResponse._from_wire(data, meta)
        if not result.script_id:
            result.script_id = script_id
        if script_id:
            core.scripts.put("akamai:" + script_id, script_id)
        return result

    return Call(
        "POST",
        AKAMAI_PATH,
        build("" if used_compact else full_script),
        const.MAX_REQUEST_BYTES,
        core.timeout,
        finish,
        fallback if used_compact else None,
    )


_AKAMAI_SBSD = (
    ("url", ""),
    ("ua", ""),
    ("script", _OMIT),
    ("script_id", _OMIT),
    ("session", _OMIT),
    ("sbsd_post", _OMIT),
    ("pace", _OMIT),
    ("script_url", ""),
    ("sbsd_o", ""),
    ("accept_language", _OMIT),
    ("language", _OMIT),
    ("timezone", _OMIT),
    ("uuid", _OMIT),
    ("resource_urls", _OMIT),
    ("dom_resource_urls", _OMIT),
)


def akamai_sbsd(core: Core, request: Mapping[str, Any]) -> Call[AkamaiSBSDResponse]:
    _check_keys(request, _AKAMAI_SBSD, "akamai.sbsd")
    script = _raw_bytes(request.get("script"), "script")
    script_id = (request.get("script_id") or "").strip().lower()
    if not script and not script_id:
        raise ValidationError("script", "set script, or script_id from an earlier response")
    values = {
        **request,
        "script": base64.b64encode(script).decode("ascii"),
        "script_id": script_id,
    }
    wire: dict[str, Any] = {"mode": "sbsd"}
    _emit(wire, values, _AKAMAI_SBSD)
    return Call(
        "POST",
        AKAMAI_PATH,
        wire,
        const.MAX_REQUEST_BYTES,
        core.timeout,
        AkamaiSBSDResponse._from_wire,
    )


_AKAMAI_CPT = (("token", ""), ("difficulty", 0))


def akamai_cpt(core: Core, request: Mapping[str, Any]) -> Call[AkamaiCPTResponse]:
    _check_keys(request, _AKAMAI_CPT, "akamai.cpt")
    wire: dict[str, Any] = {"mode": "cpt"}
    _emit(wire, request, _AKAMAI_CPT)
    return Call(
        "POST",
        AKAMAI_PATH,
        wire,
        const.MAX_REQUEST_BYTES,
        core.timeout,
        AkamaiCPTResponse._from_wire,
    )


_AKAMAI_SEC_CPT = (
    ("token", ""),
    ("sec_cpt", ""),
    ("timestamp", 0),
    ("nonce", ""),
    ("difficulty", 0),
    ("count", 0),
)


def akamai_sec_cpt(core: Core, request: Mapping[str, Any]) -> Call[AkamaiSecCPTResponse]:
    _check_keys(request, _AKAMAI_SEC_CPT, "akamai.sec_cpt")
    wire: dict[str, Any] = {"mode": "sec_cpt"}
    _emit(wire, request, _AKAMAI_SEC_CPT)
    return Call(
        "POST",
        AKAMAI_PATH,
        wire,
        const.MAX_REQUEST_BYTES,
        core.timeout,
        AkamaiSecCPTResponse._from_wire,
    )


# ---------------------------------------------------------------------------
# Kasada
# ---------------------------------------------------------------------------

_KASADA_SENSOR = (
    ("script", ""),
    ("ua", ""),
    ("session", _OMIT),
    ("script_url", _OMIT),
    ("url", _OMIT),
    ("page_origin", _OMIT),
    ("href", _OMIT),
    ("window_url", _OMIT),
    ("referrer", _OMIT),
    ("ancestor_origins", _OMIT),
    ("runtime_overrides", _OMIT),
    ("accept_language", _OMIT),
    ("timezone", _OMIT),
    ("ip", _OMIT),
    ("challenge_token", _OMIT),
    ("script_name", _OMIT),
    ("seed", _OMIT),
    ("force_pool_variation", _OMIT),
    ("now_ms", _OMIT),
    ("dt", _OMIT),
    ("headers", _OMIT),
)


def kasada_sensor(core: Core, request: Mapping[str, Any]) -> Call[KasadaSensorResponse]:
    _check_keys(request, _KASADA_SENSOR, "kasada.sensor")
    _check_uint64(request, "seed")
    values = {**request, "script": _text(request.get("script"), "script")}
    wire: dict[str, Any] = {"mode": "sensor"}
    _emit(wire, values, _KASADA_SENSOR)
    return Call(
        "POST",
        KASADA_PATH,
        wire,
        const.MAX_REQUEST_BYTES,
        core.timeout,
        KasadaSensorResponse._from_wire,
    )


_KASADA_CD = (
    ("script", ""),
    ("st", 0),
    ("ct", ""),
    ("domain", ""),
    ("work_time", 0),
    ("rst", 0),
    ("d", 0),
    ("id", ""),
    ("duration", 0),
    ("fc", ""),
    ("difficulty", _OMIT),
    ("subchallenge_count", _OMIT),
    ("seed_suffix", _OMIT),
    ("extra_token", _OMIT),
    ("is_mobile", _OMIT),
    ("seed", _OMIT),
    ("now_ms", _OMIT),
)


def kasada_cd(core: Core, request: Mapping[str, Any]) -> Call[KasadaCDResponse]:
    _check_keys(request, _KASADA_CD, "kasada.cd")
    _check_uint64(request, "seed")
    values = {**request, "script": _text(request.get("script"), "script")}
    wire: dict[str, Any] = {"mode": "cd"}
    _emit(wire, values, _KASADA_CD)
    return Call(
        "POST",
        KASADA_PATH,
        wire,
        const.MAX_REQUEST_BYTES,
        core.timeout,
        KasadaCDResponse._from_wire,
    )


# ---------------------------------------------------------------------------
# Incapsula
# ---------------------------------------------------------------------------

_INCAPSULA_WIRE = (
    ("script", _OMIT),
    ("ua", ""),
    ("script_url", ""),
    ("url", ""),
    ("accept_language", _OMIT),
    ("ip", _OMIT),
    ("pow", _OMIT),
    ("old_token", _OMIT),
    ("session", _OMIT),
    ("headers", _OMIT),
    ("session_ids", _OMIT),
    ("document_html", _OMIT),
    ("document_script_source_groups", _OMIT),
)
_REESE84_FIELDS = tuple(f for f in _INCAPSULA_WIRE if f[0] != "session_ids")
_UTMVC_FIELDS = tuple(
    f
    for f in _INCAPSULA_WIRE
    if f[0] not in ("pow", "old_token", "document_html", "document_script_source_groups")
)


def incapsula_reese84(core: Core, request: Mapping[str, Any]) -> Call[IncapsulaResponse]:
    _check_keys(request, _REESE84_FIELDS, "incapsula.reese84")
    return _incapsula(core, "reese84", request)


def incapsula_utmvc(core: Core, request: Mapping[str, Any]) -> Call[IncapsulaResponse]:
    _check_keys(request, _UTMVC_FIELDS, "incapsula.utmvc")
    return _incapsula(core, "utmvc", request)


def _incapsula(core: Core, mode: str, request: Mapping[str, Any]) -> Call[IncapsulaResponse]:
    script = _text(request.get("script"), "script")
    script_url = request.get("script_url") or ""
    cache_key = f"incapsula:{mode}:{script_url}"
    script_hash = _sha256_hex(script.encode("utf-8")) if script else ""
    used_compact = bool(script_hash) and core.scripts.matches(cache_key, script_hash)

    def build(include_script: bool) -> dict[str, Any]:
        values = {**request, "script": script if include_script else ""}
        wire: dict[str, Any] = {"mode": mode}
        _emit(wire, values, _INCAPSULA_WIRE)
        return wire

    def fallback(err: APIError) -> dict[str, Any] | None:
        if err.code in _SCRIPT_CACHE_CODES:
            return build(True)
        return None

    def finish(data: dict[str, Any], meta: ResponseMeta) -> IncapsulaResponse:
        result = IncapsulaResponse._from_wire(data, meta)
        if script_hash:
            core.scripts.put(cache_key, script_hash)
        return result

    return Call(
        "POST",
        INCAPSULA_PATH,
        build(not used_compact),
        const.MAX_REQUEST_BYTES,
        core.timeout,
        finish,
        fallback if used_compact else None,
    )


# ---------------------------------------------------------------------------
# PerimeterX / HUMAN
# ---------------------------------------------------------------------------

_PERIMETERX_WIRE = (
    ("url", _OMIT),
    ("proxy", ""),
    ("ua", _OMIT),
    ("accept_language", _OMIT),
    ("timezone", _OMIT),
    ("platform", _OMIT),
    ("referer", _OMIT),
    ("headers", _OMIT),
    ("cookies", _OMIT),
    ("app_id", _OMIT),
    ("session", _OMIT),
    ("blocked", _OMIT),
)
_PX_INIT_FIELDS = tuple(f for f in _PERIMETERX_WIRE if f[0] not in ("session", "blocked"))
_PX_HOLD_FIELDS = tuple(
    f for f in _PERIMETERX_WIRE if f[0] in ("url", "proxy", "ua", "headers", "session", "blocked")
)
_PX_BLOCKED_FIELDS = (
    ("url", ""),
    ("method", _OMIT),
    ("status", 0),
    ("headers", _OMIT),
    ("body", ""),
    ("body_encoding", _OMIT),
)
_PX_BLOCKED_KEYS = frozenset({"url", "method", "status", "headers", "body", "body_base64"})
_COOKIE_FIELDS = (
    ("name", ""),
    ("value", ""),
    ("domain", _OMIT),
    ("path", _OMIT),
    ("expires", _OMIT),
    ("secure", _OMIT),
    ("http_only", _OMIT),
)
_COOKIE_NAMES = tuple(f.name for f in dataclass_fields(PerimeterxCookie))


def _cookie_wire(cookie: object) -> dict[str, Any]:
    if isinstance(cookie, PerimeterxCookie):
        values: Mapping[str, Any] = {name: getattr(cookie, name) for name in _COOKIE_NAMES}
    elif isinstance(cookie, Mapping):
        values = cookie
    else:
        raise ValidationError("cookies", "must contain mappings or PerimeterxCookie values")
    wire: dict[str, Any] = {}
    _emit(wire, values, _COOKIE_FIELDS)
    return wire


def _perimeterx_call(core: Core, wire: dict[str, Any]) -> Call[PerimeterxResponse]:
    return Call(
        "POST",
        PERIMETERX_PATH,
        wire,
        const.MAX_PERIMETERX_REQUEST_BYTES,
        core.perimeterx_timeout,
        PerimeterxResponse._from_wire,
    )


def perimeterx_init(core: Core, request: Mapping[str, Any]) -> Call[PerimeterxResponse]:
    _check_keys(request, _PX_INIT_FIELDS, "perimeterx.init")
    if not request.get("url"):
        raise ValidationError("url", "must not be empty")
    if not request.get("proxy"):
        raise ValidationError("proxy", "must not be empty")
    cookies = [_cookie_wire(cookie) for cookie in request.get("cookies") or ()]
    wire: dict[str, Any] = {"mode": "init"}
    _emit(wire, {**request, "cookies": cookies}, _PERIMETERX_WIRE)
    return _perimeterx_call(core, wire)


def perimeterx_hold(core: Core, request: Mapping[str, Any]) -> Call[PerimeterxResponse]:
    _check_keys(request, _PX_HOLD_FIELDS, "perimeterx.solve_hold")
    if not request.get("session"):
        raise ValidationError("session", "must not be empty")
    if not request.get("proxy"):
        raise ValidationError("proxy", "must not be empty")
    blocked = request.get("blocked")
    if blocked is None:
        raise ValidationError("blocked", "must not be None")
    if not isinstance(blocked, Mapping):
        raise ValidationError("blocked", "must be a mapping")
    for key in blocked:
        if key not in _PX_BLOCKED_KEYS:
            raise TypeError(f"perimeterx.solve_hold() got an unexpected blocked key {key!r}")
    if not blocked.get("url") and not request.get("url"):
        raise ValidationError("blocked.url", "must not be empty when url is empty")
    body = blocked.get("body")
    encoding = "base64" if blocked.get("body_base64") else ""
    if isinstance(body, (bytes, bytearray, memoryview)):
        body = base64.b64encode(bytes(body)).decode("ascii")
        encoding = "base64"
    blocked_wire: dict[str, Any] = {}
    _emit(
        blocked_wire,
        {**blocked, "body": body, "body_encoding": encoding},
        _PX_BLOCKED_FIELDS,
    )
    wire: dict[str, Any] = {"mode": "holdcaptcha"}
    _emit(wire, {**request, "blocked": blocked_wire}, _PERIMETERX_WIRE)
    return _perimeterx_call(core, wire)


# ---------------------------------------------------------------------------
# Account and generic solve
# ---------------------------------------------------------------------------


def balance(core: Core) -> Call[Balance]:
    return Call("GET", "/balance", None, const.MAX_REQUEST_BYTES, core.timeout, Balance._from_wire)


def generic_solve(core: Core, solver: object, request: object) -> Call[RawResponse]:
    if not isinstance(solver, str) or solver not in const.SOLVERS:
        raise ValidationError("solver", "must be akamai, kasada, incapsula, or perimeterx")
    if not isinstance(request, Mapping):
        raise ValidationError("request", "must be a mapping")
    perimeterx = solver == "perimeterx"
    return Call(
        "POST",
        f"/v1/solve/{solver}",
        dict(request),
        const.MAX_PERIMETERX_REQUEST_BYTES if perimeterx else const.MAX_REQUEST_BYTES,
        core.perimeterx_timeout if perimeterx else core.timeout,
        RawResponse._from_wire,
    )
