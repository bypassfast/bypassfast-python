# Changelog

All notable changes to the `bypassfast` Python SDK are recorded here. The
project follows [Semantic Versioning](https://semver.org/).

## 0.2.0 - 2026-10-27

- Every script-bearing call travels hash-first. `akamai.sensor`, `akamai.sbsd`,
  `kasada.sensor`, `kasada.cd`, `incapsula.reese84` and `incapsula.utmvc` send
  the script's SHA-256 as `script_id` and no script; the API keeps every
  script it has been sent, so the body is uploaded only when nobody has sent
  that script yet (409 `script_cache_miss`, not billed), in one automatic
  resend. An Akamai sensor request goes from hundreds of KB to about 1 KB on
  the wire, a Kasada one from about 700 KB. After a miss the client sends the
  script directly for a minute, so a burst of solves for a new script uploads
  it once. `bypassfast.script_id()` computes the digest; `akamai_script_id()` remains.
- Kasada and Incapsula requests accept `script_id` instead of `script`, and
  every script-bearing response returns `script_id`.
- Every solve returns `client_hints`, the UA client hints of the device the
  payload describes. Send `sec-ch-ua`, `sec-ch-ua-mobile` and
  `sec-ch-ua-platform` on every request, and the others only to sites whose
  `Accept-CH` header asks for them.
- Akamai: `telemetry=True` on `akamai.sensor` returns the `Akamai-BM-Telemetry`
  header value in `AkamaiSensorResponse.telemetry`, for sites that protect
  their API calls with that header instead of a sensor post.
  `AkamaiSensorResponse.paced_ms` reports how long a paced response was held.
- A script the API could never accept (over 1 MiB encoded) is refused locally
  as a `ValidationError` on `script` before any round trip.
- PerimeterX: `PerimeterxResponse.accept_language` is the Accept-Language the
  session runs as. Without `accept_language` in `init` the API draws a real
  Chrome value for the proxy exit's country; the SDK keeps your own value
  when you set one. Send every request of the session with `ua` and
  `accept_language`.
- PerimeterX: `platform="auto"` on `perimeterx.init` has the solver draw
  each session's platform (Windows or macOS) and, without `ua`, a current
  Chrome major; `PerimeterxResponse.platform` reports the draw.
- Akamai: `pace=True` on `akamai.sensor` asks the API to hold each
  response until a person's interval (2.5-4 s) has passed since the
  previous sensor.

## 0.1.0 - 2026-10-05

First release, matching the wire contract of the Bypass Fast Go SDK 0.2.1.
Licensed under the MIT License.

- `Client` (synchronous) and `AsyncClient` (asyncio) with identical methods,
  built on `httpx`.
- Akamai: `sensor` (automatic `script_id` reuse with cache-miss fallback),
  `sbsd`, `cpt`, `sec_cpt`; `akamai_script_id()` helper.
- Kasada: `sensor`, `cd`.
- Incapsula: `reese84`, `utmvc` (automatic script omission per mode and
  `script_url`, with cache-miss fallback).
- PerimeterX / HUMAN: `init`, `solve_hold`; 2 MiB request limit and a 155 s
  default timeout for this solver.
- `balance()` and the generic `solve()` escape hatch.
- Typed request `TypedDict`s and response dataclasses with `.response`
  metadata and `.raw` JSON.
- Errors: `ValidationError`, `RequestError`, `ResponseError`, `APIError`
  (with `retryable`), all under `BypassFastError`; `is_error_code()`.
- Retries for retryable API errors with full-jitter backoff, `Retry-After` /
  `retry_after_ms` support and a separate `solver_busy` time budget.
- Gzip request compression, 1 MiB local request limit, 4 MiB response cap,
  no redirect following.
