# Changelog

All notable changes to the `bypassfast` Python SDK are recorded here. The
project follows [Semantic Versioning](https://semver.org/).

## 0.1.0 (unreleased)

First release, matching the wire contract of the Bypass Fast Go SDK 0.2.0.

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
