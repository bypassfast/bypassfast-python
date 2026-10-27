"""Request and response models.

Requests are ``TypedDict`` s: every solver method takes them as keyword
arguments (``client.kasada.sensor(script=..., ua=...)``) or as an unpacked
mapping (``client.kasada.sensor(**request)``). Attribute names are the API's
wire names.

Responses are dataclasses. Unknown response fields are ignored by the typed
attributes and kept in ``raw``; ``response`` carries the call's
:class:`ResponseMeta`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, TypedDict, Union

from ._errors import ResponseMeta

__all__ = [
    "AkamaiCPTRequest",
    "AkamaiCPTResponse",
    "AkamaiSBSDRequest",
    "AkamaiSBSDResponse",
    "AkamaiSecCPTRequest",
    "AkamaiSecCPTResponse",
    "AkamaiSensorRequest",
    "AkamaiSensorResponse",
    "Balance",
    "IncapsulaReese84Request",
    "IncapsulaResponse",
    "IncapsulaUTMVCRequest",
    "KasadaCDRequest",
    "KasadaCDResponse",
    "KasadaSensorRequest",
    "KasadaSensorResponse",
    "PerimeterxBlockedResponse",
    "PerimeterxCookie",
    "PerimeterxCookieParam",
    "PerimeterxHoldRequest",
    "PerimeterxInitRequest",
    "PerimeterxPlatform",
    "PerimeterxPlatformRequest",
    "PerimeterxResponse",
    "PerimeterxRetryAdvice",
    "RawResponse",
]

PerimeterxPlatform = Literal["chrome-windows", "chrome-mac"]
# What ``init`` accepts for ``platform``: a persona family, or ``"auto"`` to
# have the solver draw Windows or macOS and (without ``ua``) a current Chrome
# major per session. Send ``"auto"`` only through exits that fit both Windows
# and macOS clients; pin the platform on an exit of one OS class.
PerimeterxPlatformRequest = Literal["chrome-windows", "chrome-mac", "auto"]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class AkamaiSensorRequest(TypedDict, total=False):
    """Akamai Bot Manager ``sensor_data`` generation (mode ``sensor``).

    ``script`` is the raw JavaScript (bytes, or text encoded as UTF-8); the SDK
    base64-encodes it, sends only its SHA-256 as ``script_id`` and uploads the
    bytes once, only when the API has never seen that script. ``script_id``
    may be given instead of ``script`` when the bytes are not at hand. With
    ``session`` set, neither is sent. Set ``pace`` on the calls after the
    first to have each response held until a person's interval (2.5-4 s) has
    passed since the previous sensor: a page posts seconds apart, and sites
    score sessions whose sensors arrive a fraction of a second apart. Set
    ``telemetry`` when the site protects its API calls with an
    ``Akamai-BM-Telemetry`` header instead of a sensor post: the response then
    carries the ready-to-send header value.
    """

    url: str
    ua: str
    abck: str
    bm_sz: str
    script: Union[bytes, str]
    script_id: str
    script_url: str
    accept_language: str
    language: str
    timezone: str
    session: str
    pace: bool
    telemetry: bool


class AkamaiSBSDRequest(TypedDict, total=False):
    """Akamai SBSD side-band payload (mode ``sbsd``).

    ``script`` travels as its SHA-256 and is uploaded once on a cache miss, as
    for the sensor. Keep the ``session`` from the first response and send it
    on every later SBSD (or sensor) call for that page. Later calls may send
    ``script_id`` (also returned) instead of ``script``. ``sbsd_post="telemetry"`` asks for
    the page's next telemetry post; wait ``wait_ms`` before posting it, or set
    ``pace`` to have the response held until then.
    """

    url: str
    ua: str
    script: Union[bytes, str]
    script_id: str
    session: str
    sbsd_post: str
    pace: bool
    script_url: str
    sbsd_o: str
    accept_language: str
    language: str
    timezone: str
    uuid: str
    resource_urls: Sequence[str]
    dom_resource_urls: Sequence[str]


class AkamaiCPTRequest(TypedDict, total=False):
    """Akamai Sec-CPT proof of work (mode ``cpt``)."""

    token: str
    difficulty: int


class AkamaiSecCPTRequest(TypedDict, total=False):
    """Akamai sec-cpt challenge page (mode ``sec_cpt``).

    ``token``, ``timestamp``, ``nonce``, ``difficulty`` and ``count`` come from
    the challenge iframe or a chained ``/_sec/verify`` response; ``sec_cpt`` is
    the sec_cpt cookie value.
    """

    token: str
    sec_cpt: str
    timestamp: int
    nonce: str
    difficulty: int
    count: int


class KasadaSensorRequest(TypedDict, total=False):
    """Kasada sensor (mode ``sensor``). ``script`` is the raw ``p.js`` source;
    unlike Akamai it is not base64-encoded. It travels as its SHA-256
    (``script_id``) and is uploaded once, only when the API has never seen
    it; ``script_id`` may be given instead when the source is not at hand."""

    script: Union[str, bytes]
    script_id: str
    ua: str
    session: str
    script_url: str
    url: str
    page_origin: str
    href: str
    window_url: str
    referrer: str
    ancestor_origins: Sequence[str]
    runtime_overrides: Mapping[str, Any]
    accept_language: str
    timezone: str
    ip: str
    challenge_token: str
    script_name: str
    seed: int
    force_pool_variation: bool
    now_ms: int
    dt: Mapping[str, Any]
    headers: Mapping[str, str]


class KasadaCDRequest(TypedDict, total=False):
    """Kasada CD proof of work (mode ``cd``). ``script`` is the same ``p.js``
    as the sensor call and travels as its ``script_id``."""

    script: Union[str, bytes]
    script_id: str
    st: int
    ct: str
    domain: str
    work_time: int
    rst: int
    d: int
    id: str
    duration: float
    fc: str
    difficulty: int
    subchallenge_count: int
    seed_suffix: str
    extra_token: str
    is_mobile: bool
    seed: int
    now_ms: int


class IncapsulaReese84Request(TypedDict, total=False):
    """Incapsula Reese84 sensor (mode ``reese84``).

    ``script`` is raw JavaScript. It travels as its SHA-256 (``script_id``)
    and is uploaded once, only when the API has never seen it; ``script_id``
    may be given instead when the source is not at hand. ``document_html`` is
    the challenged page body; ``document_script_source_groups`` is its
    pre-extracted form.
    """

    script: Union[str, bytes]
    script_id: str
    ua: str
    script_url: str
    url: str
    accept_language: str
    ip: str
    pow: str
    old_token: str
    session: str
    headers: Mapping[str, str]
    document_html: str
    document_script_source_groups: Sequence[Sequence[str]]


class IncapsulaUTMVCRequest(TypedDict, total=False):
    """Incapsula ``___utmvc`` cookie workflow (mode ``utmvc``); the script
    travels as its ``script_id`` as for :class:`IncapsulaReese84Request`."""

    script: Union[str, bytes]
    script_id: str
    ua: str
    script_url: str
    url: str
    accept_language: str
    ip: str
    session: str
    headers: Mapping[str, str]
    session_ids: Sequence[str]


class PerimeterxCookieParam(TypedDict, total=False):
    """A cookie to install before the page fetch. ``expires`` is a Unix time
    in seconds (omit for a session cookie). A :class:`PerimeterxCookie` from
    an earlier response is accepted too."""

    name: str
    value: str
    domain: str
    path: str
    expires: int
    secure: bool
    http_only: bool


class _PerimeterxInitRequired(TypedDict):
    url: str
    proxy: str


class PerimeterxInitRequest(_PerimeterxInitRequired, total=False):
    """PerimeterX / HUMAN page-sensor session (mode ``init``).

    ``url`` is the page a user would open and ``proxy`` the exit the whole flow
    must share (``http(s)://`` or ``socks5(h)://`` with credentials). Without
    ``ua`` the solver draws a desktop Chrome build for ``platform`` and returns
    the user agent it used. Without ``accept_language`` it draws a real Chrome
    Accept-Language for the exit's country and returns it as
    ``accept_language``. ``timezone`` is ignored: the session's zone follows
    where the exit is.
    ``platform="auto"`` has the solver draw the platform too (Windows or
    macOS, and a current Chrome major); read the draw from the response's
    ``platform`` and ``ua``.
    """

    ua: str
    accept_language: str
    timezone: str
    platform: PerimeterxPlatformRequest
    referer: str
    headers: Mapping[str, str]
    cookies: Sequence[Union[PerimeterxCookieParam, PerimeterxCookie]]
    app_id: str


class PerimeterxBlockedResponse(TypedDict, total=False):
    """The enforcement response exactly as received.

    ``headers`` use lower-case names, comma-joined when repeated. A ``str``
    body is sent as text (set ``body_base64`` when it already holds base64);
    a ``bytes`` body is base64-encoded by the SDK.
    """

    url: str
    method: str
    status: int
    headers: Mapping[str, str]
    body: Union[str, bytes]
    body_base64: bool


class _PerimeterxHoldRequired(TypedDict):
    session: str
    proxy: str
    blocked: PerimeterxBlockedResponse


class PerimeterxHoldRequest(_PerimeterxHoldRequired, total=False):
    """PerimeterX press-and-hold solve (mode ``holdcaptcha``) inside the
    session from :meth:`init`. ``url`` is used only when ``blocked["url"]`` is
    empty; ``ua`` defaults to the session's user agent. ``cookies`` are the
    cookies you hold for the site at the block (the set the blocked request
    carried, HUMAN's included): the solver lays them over its session jar so
    the hold runs with the site's current session and other protection
    layers, then returns only HUMAN's cookies and the ones the hold set or
    changed. Without them the solver refreshes the page itself before the
    hold."""

    url: str
    ua: str
    headers: Mapping[str, str]
    cookies: Sequence[Union[PerimeterxCookieParam, PerimeterxCookie]]


# ---------------------------------------------------------------------------
# Response decoding helpers
# ---------------------------------------------------------------------------


class _FieldTypeError(ValueError):
    def __init__(self, key: str, expected: str) -> None:
        super().__init__(f'field "{key}": expected {expected}')


def _str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    raise _FieldTypeError(key, "a string")


def _bool(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise _FieldTypeError(key, "a boolean")


def _float(data: Mapping[str, Any], key: str) -> float:
    value = data.get(key)
    if value is None:
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
        except OverflowError:
            number = math.inf
        if math.isfinite(number):
            return number
    raise _FieldTypeError(key, "a number")


def _int(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if value is None:
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise _FieldTypeError(key, "an integer")


def _str_list(data: Mapping[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise _FieldTypeError(key, "an array of strings")


def _str_map(data: Mapping[str, Any], key: str) -> dict[str, str]:
    value = data.get(key)
    if value is None:
        return {}
    if isinstance(value, dict) and all(isinstance(item, str) for item in value.values()):
        return dict(value)
    raise _FieldTypeError(key, "an object of strings")


def _object(data: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    raise _FieldTypeError(key, "an object")


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


@dataclass
class AkamaiSensorResponse:
    """``sensor_data`` plus the exact client identity (``ua``, ``language``)
    to use for the target exchange. ``client_hints`` are the UA client hints
    of the device: send ``sec-ch-ua``, ``sec-ch-ua-mobile`` and
    ``sec-ch-ua-platform`` on every request, and the others only to sites
    whose ``Accept-CH`` header asks for them. ``telemetry`` is the
    ``Akamai-BM-Telemetry`` header value when the request asked for it;
    ``paced_ms`` how long a paced response was held."""

    cost: float = 0.0
    success: bool = False
    sensor_data: str = ""
    ua: str = ""
    session: str = ""
    language: str = ""
    script_id: str = ""
    client_hints: dict[str, str] = field(default_factory=dict)
    telemetry: str = ""
    paced_ms: int = 0
    response: ResponseMeta = field(default_factory=ResponseMeta)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> AkamaiSensorResponse:
        return cls(
            cost=_float(data, "cost"),
            success=_bool(data, "success"),
            sensor_data=_str(data, "sensor_data"),
            ua=_str(data, "ua"),
            session=_str(data, "session"),
            language=_str(data, "language"),
            script_id=_str(data, "script_id"),
            client_hints=_str_map(data, "client_hints"),
            telemetry=_str(data, "telemetry"),
            paced_ms=_int(data, "paced_ms"),
            response=meta,
            raw=data,
        )


@dataclass
class AkamaiSBSDResponse:
    """The encrypted body to submit upstream, the ``session`` pinning this
    page's device, and the ``script_id`` later calls may send instead of the
    script. On a telemetry response ``sbsd_post`` is ``"telemetry"``, ``ind``
    says which post it is and ``wait_ms`` how long to wait before posting."""

    cost: float = 0.0
    success: bool = False
    body: str = ""
    session: str = ""
    script_id: str = ""
    client_hints: dict[str, str] = field(default_factory=dict)
    sbsd_post: str = ""
    ind: int = 0
    wait_ms: int = 0
    response: ResponseMeta = field(default_factory=ResponseMeta)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> AkamaiSBSDResponse:
        return cls(
            cost=_float(data, "cost"),
            success=_bool(data, "success"),
            body=_str(data, "body"),
            session=_str(data, "session"),
            script_id=_str(data, "script_id"),
            client_hints=_str_map(data, "client_hints"),
            sbsd_post=_str(data, "sbsd_post"),
            ind=_int(data, "ind"),
            wait_ms=_int(data, "wait_ms"),
            response=meta,
            raw=data,
        )


@dataclass
class AkamaiCPTResponse:
    """The Sec-CPT proof answers."""

    cost: float = 0.0
    success: bool = False
    answers: list[str] = field(default_factory=list)
    response: ResponseMeta = field(default_factory=ResponseMeta)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> AkamaiCPTResponse:
        return cls(
            cost=_float(data, "cost"),
            success=_bool(data, "success"),
            answers=_str_list(data, "answers"),
            response=meta,
            raw=data,
        )


@dataclass
class AkamaiSecCPTResponse:
    """``count`` proof answers and ``body``, the exact request body to POST to
    ``/_sec/verify?provider=<provider>``."""

    cost: float = 0.0
    success: bool = False
    answers: list[str] = field(default_factory=list)
    body: str = ""
    response: ResponseMeta = field(default_factory=ResponseMeta)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> AkamaiSecCPTResponse:
        return cls(
            cost=_float(data, "cost"),
            success=_bool(data, "success"),
            answers=_str_list(data, "answers"),
            body=_str(data, "body"),
            response=meta,
            raw=data,
        )


@dataclass
class KasadaSensorResponse:
    """The encrypted ``payload`` for ``/tms`` plus the ``headers`` and
    ``user_agent`` to send with it. Attach every returned header; send the
    low-entropy ``client_hints`` (``sec-ch-ua``, ``sec-ch-ua-mobile``,
    ``sec-ch-ua-platform``) on every request and the others only to sites
    whose ``Accept-CH`` header asks for them. ``script_id`` is the digest of
    the ``p.js`` the solve used."""

    cost: float = 0.0
    payload: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    client_hints: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    device_id: str = ""
    session: str = ""
    user_agent: str = ""
    script_id: str = ""
    cache_hit: bool = False
    duration_ms: int = 0
    response: ResponseMeta = field(default_factory=ResponseMeta)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> KasadaSensorResponse:
        return cls(
            cost=_float(data, "cost"),
            payload=_str(data, "payload"),
            headers=_str_map(data, "headers"),
            client_hints=_str_map(data, "client_hints"),
            warnings=_str_list(data, "warnings"),
            device_id=_str(data, "device_id"),
            session=_str(data, "session"),
            user_agent=_str(data, "user_agent"),
            script_id=_str(data, "script_id"),
            cache_hit=_bool(data, "cache_hit"),
            duration_ms=_int(data, "duration_ms"),
            response=meta,
            raw=data,
        )


@dataclass
class KasadaCDResponse:
    """``payload`` is the JSON string to send as ``x-kpsdk-cd``; ``script_id``
    the digest of the ``p.js`` the proof used."""

    cost: float = 0.0
    payload: str = ""
    script_id: str = ""
    duration_ms: int = 0
    response: ResponseMeta = field(default_factory=ResponseMeta)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> KasadaCDResponse:
        return cls(
            cost=_float(data, "cost"),
            payload=_str(data, "payload"),
            script_id=_str(data, "script_id"),
            duration_ms=_int(data, "duration_ms"),
            response=meta,
            raw=data,
        )


@dataclass
class IncapsulaResponse:
    """Shared by Reese84 and UTMVC. UTMVC fills ``cookie``, ``cookie_name`` and
    ``submit_path``; Reese84 fills ``device_id`` and ``session``. ``script_id``
    is the digest of the script the solve used; ``client_hints`` the device's
    UA client hints (send the low-entropy ones on every request, the others
    only to sites whose ``Accept-CH`` header asks for them)."""

    cost: float = 0.0
    payload: str = ""
    backend: str = ""
    device_id: str = ""
    session: str = ""
    script_id: str = ""
    client_hints: dict[str, str] = field(default_factory=dict)
    duration_ms: int = 0
    cookie: str = ""
    cookie_name: str = ""
    submit_path: str = ""
    response: ResponseMeta = field(default_factory=ResponseMeta)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> IncapsulaResponse:
        return cls(
            cost=_float(data, "cost"),
            payload=_str(data, "payload"),
            backend=_str(data, "backend"),
            device_id=_str(data, "device_id"),
            session=_str(data, "session"),
            script_id=_str(data, "script_id"),
            client_hints=_str_map(data, "client_hints"),
            duration_ms=_int(data, "duration_ms"),
            cookie=_str(data, "cookie"),
            cookie_name=_str(data, "cookie_name"),
            submit_path=_str(data, "submit_path"),
            response=meta,
            raw=data,
        )


@dataclass
class PerimeterxCookie:
    """A cookie the solver holds. ``expires`` is a Unix time in seconds, 0 for
    a session cookie. Instances can be passed back in ``init(cookies=...)``."""

    name: str = ""
    value: str = ""
    domain: str = ""
    path: str = ""
    expires: int = 0
    secure: bool = False
    http_only: bool = False

    @classmethod
    def _from_wire(cls, data: Mapping[str, Any]) -> PerimeterxCookie:
        return cls(
            name=_str(data, "name"),
            value=_str(data, "value"),
            domain=_str(data, "domain"),
            path=_str(data, "path"),
            expires=_int(data, "expires"),
            secure=_bool(data, "secure"),
            http_only=_bool(data, "http_only"),
        )


@dataclass
class PerimeterxRetryAdvice:
    """How to proceed after a rejected hold."""

    change_exit: bool = False
    reason: str = ""


@dataclass
class PerimeterxResponse:
    """Result of :meth:`init` or :meth:`solve_hold`.

    ``success`` true: install every cookie and send (or retry once) your
    protected request. ``success`` false: the hold was rejected and ``cookies``
    are the pre-hold cookies. A rejected hold is a billed 2xx, not an error;
    do not retry it unless ``retry`` explicitly supplies a safe action.
    ``ua`` is the user agent the session runs as; send every request for the
    session with exactly this value. ``accept_language`` is the
    Accept-Language the session runs as, likewise: your own when you sent
    one, otherwise the value the API drew for the proxy exit's country.
    ``platform`` is the platform it runs as (``chrome-windows`` or
    ``chrome-mac``); your client hints and TLS stack must match it.
    """

    success: bool = False
    cookies: list[PerimeterxCookie] = field(default_factory=list)
    session: str = ""
    retry: Optional[PerimeterxRetryAdvice] = None
    ua: str = ""
    accept_language: str = ""
    platform: str = ""
    client_hints: dict[str, str] = field(default_factory=dict)
    cost: float = 0.0
    response: ResponseMeta = field(default_factory=ResponseMeta)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def rejected(self) -> bool:
        """Whether the hold was rejected (``success`` is false)."""
        return not self.success

    @property
    def change_exit(self) -> bool:
        """Whether to move to another exit and start again from ``init``."""
        return not self.success and self.retry is not None and self.retry.change_exit

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> PerimeterxResponse:
        raw_cookies = data.get("cookies")
        if raw_cookies is None:
            raw_cookies = []
        if not isinstance(raw_cookies, list) or not all(isinstance(c, dict) for c in raw_cookies):
            raise _FieldTypeError("cookies", "an array of objects")
        retry_data = _object(data, "retry")
        retry = None
        if retry_data is not None:
            retry = PerimeterxRetryAdvice(
                change_exit=_bool(retry_data, "change_exit"), reason=_str(retry_data, "reason")
            )
        return cls(
            success=_bool(data, "success"),
            cookies=[PerimeterxCookie._from_wire(cookie) for cookie in raw_cookies],
            session=_str(data, "session"),
            retry=retry,
            ua=_str(data, "ua"),
            accept_language=_str(data, "accept_language"),
            platform=_str(data, "platform"),
            client_hints=_str_map(data, "client_hints"),
            cost=_float(data, "cost"),
            response=meta,
            raw=data,
        )


@dataclass
class Balance:
    """The prepaid account balance. ``error_id`` is the wire's ``errorId``."""

    error_id: int = 0
    org_id: str = ""
    balance: float = 0.0
    balance_cents: int = 0
    currency: str = ""
    response: ResponseMeta = field(default_factory=ResponseMeta)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> Balance:
        return cls(
            error_id=_int(data, "errorId"),
            org_id=_str(data, "org_id"),
            balance=_float(data, "balance"),
            balance_cents=_int(data, "balance_cents"),
            currency=_str(data, "currency"),
            response=meta,
            raw=data,
        )


@dataclass
class RawResponse:
    """Result of the generic :meth:`solve` escape hatch: the decoded JSON
    object and the call's metadata."""

    raw: dict[str, Any] = field(default_factory=dict)
    response: ResponseMeta = field(default_factory=ResponseMeta)

    @classmethod
    def _from_wire(cls, data: dict[str, Any], meta: ResponseMeta) -> RawResponse:
        return cls(raw=data, response=meta)
