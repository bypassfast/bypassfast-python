# Changelog

All notable changes to the `bypassfast` Python SDK are recorded here. The
project follows [Semantic Versioning](https://semver.org/).

## Unreleased

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
