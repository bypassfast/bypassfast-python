# Bypass Fast Python SDK

The official Python client for the [Bypass Fast](https://bypass.fast) antibot-solving
API: Akamai Bot Manager, Kasada, Imperva Incapsula and PerimeterX / HUMAN.

Bypass Fast computes the challenge artifacts (sensor data, payloads, cookies).
Your own HTTP client keeps making every request to the target site. This SDK
handles the API side: typed requests and responses, request compression,
script caching, retries and error decoding.

```sh
pip install bypassfast
```

- Python 3.9 or newer; one runtime dependency, [`httpx`](https://www.python-httpx.org/).
- A synchronous `Client` and an asyncio `AsyncClient` with the same methods.
- Fully typed (`py.typed`).
- API reference: <https://bypass.fast/docs>

Solves are billed per successful (2xx) solve; see <https://bypass.fast/pricing>.

## Quick start

```python
import os
import bypassfast

with bypassfast.Client(os.environ["BYPASS_FAST_API_KEY"]) as client:
    print(client.balance().balance)
```

Create one client and reuse it; it is safe to share between threads. Every
solver method takes keyword arguments named exactly like the API's JSON fields
and returns a dataclass whose `.response` holds the status, `X-Request-ID`,
edge version, `Server-Timing` and the number of HTTP attempts.

| Protection | Method | Returns |
| --- | --- | --- |
| Akamai Bot Manager | `client.akamai.sensor(...)` | `sensor_data`, `session`, exact `ua`/`language`, `script_id` |
| Akamai SBSD | `client.akamai.sbsd(...)` | encrypted SBSD `body`, `session`, `script_id` |
| Akamai Sec-CPT | `client.akamai.cpt(...)` | proof `answers` |
| Akamai sec-cpt page | `client.akamai.sec_cpt(...)` | `answers` and the exact `/_sec/verify` `body` |
| Kasada sensor | `client.kasada.sensor(...)` | `payload` for `tl`, `x-kpsdk-*` `headers`, `user_agent` |
| Kasada CD | `client.kasada.cd(...)` | `payload` used as `x-kpsdk-cd` |
| Incapsula Reese84 | `client.incapsula.reese84(...)` | sensor `payload`, `session` |
| Incapsula UTMVC | `client.incapsula.utmvc(...)` | `___utmvc` `cookie` and `submit_path` |
| PerimeterX init | `client.perimeterx.init(...)` | HUMAN `cookies`, `session`, `ua` |
| PerimeterX hold | `client.perimeterx.solve_hold(...)` | `success`, refreshed `cookies`, `retry` advice |

`client.balance()` returns the prepaid balance. `client.solve(solver, body)`
posts a raw mapping to `/v1/solve/{solver}` for fields this SDK does not model
yet and returns the decoded JSON in `.raw`; prefer the typed methods. Typed
responses also keep the decoded JSON in `.raw`.

The `examples/` directory of the source distribution has runnable scripts
for Kasada, Akamai and PerimeterX that read their inputs from environment
variables.

## Kasada

Fetch `p.js` with your own client, solve, POST the decoded payload to the
`tl` endpoint next to `p.js` with every returned header, then send the
protected request with the `x-kpsdk-ct` it returns.

```python
import base64

p_js = site.get(script_url).text  # your client, your session

solved = client.kasada.sensor(
    script=p_js,  # raw p.js text, not base64
    ua=user_agent,
    url="https://www.example.com/checkout",
    script_url=script_url,
)

tl = site.post(
    script_url.rsplit("/", 1)[0] + "/tl",
    content=base64.b64decode(solved.payload),
    headers={
        **solved.headers,
        "Content-Type": "application/octet-stream",
        "User-Agent": solved.user_agent,
    },
)
token = tl.headers["x-kpsdk-ct"]  # send on the protected request with solved.user_agent
```

Use `solved.user_agent` verbatim: the solver may adjust the minor version to
match the device it used. Pass `session=solved.session` on the next solve in
the same browser session. When the site issues a CD challenge,
`client.kasada.cd(...)` returns the `x-kpsdk-cd` value in `payload`.

## Akamai

```python
script = site.get(script_url).content  # raw bytes; the SDK base64-encodes

solved = client.akamai.sensor(
    url="https://www.example.com/",
    ua=user_agent,
    abck=site.cookies.get("_abck") or "0~-1~-1~-1~-1",
    bm_sz=site.cookies.get("bm_sz") or "",
    script=script,
    script_url=script_url,
)
site.post(
    script_url,
    content=json.dumps({"sensor_data": solved.sensor_data}),
    headers={"Content-Type": "text/plain;charset=UTF-8", "User-Agent": solved.ua},
)
```

Pass `session=solved.session` on follow-up posts for the same page; with a
session the script is not sent again.

Script handling is automatic. After a script-bearing solve succeeds, the
client remembers the script's SHA-256 (never the script) and later fresh
sessions send only `script_id`. If the API's cache entry expired
(`409 script_cache_miss`) or the cache is unavailable
(`503 script_cache_unavailable`, retried first), the client resends the full
script once; `response.attempts` counts both exchanges. In a new process, pass
`script_id=bypassfast.akamai_script_id(script)` together with `script` to try
the compact form first. Each client remembers up to 256 script hashes
(shared with Incapsula), evicting the least recently used.

`client.akamai.sbsd(...)` takes the SBSD script (or `script_id` from an
earlier response) and `sbsd_o`; keep the returned `session` for every later
SBSD or sensor call on that page. `client.akamai.cpt(token=..., difficulty=...)`
and `client.akamai.sec_cpt(...)` solve the proof-of-work challenges.

## Incapsula

```python
result = client.incapsula.reese84(
    script=loader_js,  # raw JavaScript
    script_url=loader_url,  # the exact URL it came from
    url="https://www.example.com/checkout",
    ua=user_agent,
    document_html=challenge_page_html,
)
site.post(sensor_url, content=result.payload, headers={"Content-Type": "text/plain; charset=utf-8"})
```

After a successful call the client omits `script` for the same content at the
same mode and `script_url`, and resends it automatically on
`409 script_cache_miss`. Changed bytes at the same URL are sent in full.
`client.incapsula.utmvc(..., session_ids=[...])` returns the `___utmvc`
`cookie` and the `submit_path` to request next.

## PerimeterX / HUMAN

PerimeterX runs through your proxy. Use the same proxy exit, Accept-Language
and the returned `ua` for every request in the session.

```python
init = client.perimeterx.init(url="https://www.example.com/", proxy=proxy_url)
for cookie in init.cookies:
    site.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path or "/")

response = site.get(protected_url)  # your client, same proxy, ua=init.ua
if is_human_block(response):  # 428 JSON block or a px-captcha page
    hold = client.perimeterx.solve_hold(
        session=init.session,
        proxy=proxy_url,
        blocked={
            "url": str(response.url),
            "method": "GET",
            "status": response.status_code,
            "headers": lower_case_headers,  # repeated headers comma-joined
            "body": response.content,  # bytes are sent base64-encoded
        },
    )
    if not hold.success:
        raise SystemExit("hold rejected")  # billed; do not loop
    # install hold.cookies and retry the request once
```

A rejected hold is `success=False` in a 2xx response, not an exception, and it
is billed. Stop after a rejection; `hold.change_exit` is true only when the
API says moving to another exit is the safe action. PerimeterX request bodies
may reach 2 MiB (the block page rides in `blocked["body"]`) and calls get a
155-second timeout by default.

## Errors and retries

Every exception derives from `bypassfast.BypassFastError`.

| Exception | When | Retried automatically |
| --- | --- | --- |
| `ValidationError` | Rejected locally before any request (also a `ValueError`) | no |
| `RequestError` | No response: connection failure or timeout | never |
| `ResponseError` | A response that cannot be read or decoded | no |
| `APIError` | Any non-2xx response (redirects included) | when `err.retryable` |

```python
try:
    solved = client.kasada.sensor(script=p_js, ua=user_agent)
except bypassfast.APIError as err:
    log.warning(
        "solve failed: %s (status %s, request %s)",
        err.code,
        err.response.status_code,
        err.response.request_id,
    )
    if bypassfast.is_error_code(err, "quota_exceeded"):
        ...
```

`APIError.code` is the stable machine code (`error`, or Akamai's legacy
`error_code`; `http_error` when the body has neither). `message`, `stage`,
`reason` (for PerimeterX `proxy_error`) and `retry_after` (seconds) are
attributes. `str(err)` contains only the code, status and request ID, never
the message or any body: solver outputs and cookies are bearer credentials.

The default `RetryPolicy(max_retries=2, base_delay=0.5, max_delay=8.0)` retries
retryable API errors with full-jitter exponential backoff and honors the
server's `retry_after_ms` body field (preferred) or `Retry-After` header.
`internal` and `internal_error` are retried at most once. Codes such as
`quota_exceeded`, `proxy_error`, `script_cache_miss` and `solve_failed` are
never retried. Transport errors are never retried, because the connection may
have failed after a solve completed.

`429 solver_busy` is retried against a time budget instead of `max_retries`:
for up to 45 seconds from the start of the call by default
(`solver_busy_retry_budget`). Each wait is at least the server's hint (one
second without one) plus jitter whose window doubles per retry, capped at 10
seconds and kept inside the budget. `max_retries=0` disables every automatic
retry, `solver_busy` included. Retries smooth bursts but do not add capacity,
so bound your own concurrency.

## Async

```python
import asyncio
import bypassfast


async def main() -> None:
    async with bypassfast.AsyncClient(api_key) as client:
        solved = await client.kasada.sensor(script=p_js, ua=user_agent)
        balance = await client.balance()


asyncio.run(main())
```

`AsyncClient` has the same methods and options; pass an `httpx.AsyncClient`
as `http_client` to customise transport. Cancelling the task (or
`asyncio.wait_for`) interrupts a retry wait immediately.

## Configuration

```python
client = bypassfast.Client(
    api_key,
    base_url="https://api.bypass.fast",
    http_client=None,  # an httpx.Client (proxies, TLS, transport)
    retry_policy=bypassfast.RetryPolicy(max_retries=2, base_delay=0.5, max_delay=8.0),
    solver_busy_retry_budget=45.0,  # seconds; 0 disables solver_busy retries
    compression_threshold=1024,  # bytes; negative disables, 0 = always try
    user_agent="checkout-service/2.4.0",  # appended to "bypassfast-python/0.1.0"
    timeout=65.0,  # seconds per HTTP attempt
    perimeterx_timeout=155.0,  # seconds per PerimeterX attempt
)
```

- Request bodies of at least 1 KiB are gzip-compressed when that saves bytes,
  and are always sent with `Content-Length`.
- Encoded JSON above 1 MiB (2 MiB for PerimeterX) is rejected locally with a
  `ValidationError`, before any request.
- Responses are gzip-decoded once by the SDK and capped at 4 MiB decoded.
- Redirects are never followed, even with a supplied `http_client`, so
  `X-API-Key` never reaches another origin.
- A non-loopback `base_url` must use HTTPS.
- The SDK does not close an `http_client` you pass in; `close()` /
  `aclose()` (or the context manager) closes the one it created.

Never log request objects or solver responses: they can contain proxy
credentials, device sessions, cookies and tokens.

## License

License: to be chosen by the maintainer before publishing.
