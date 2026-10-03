"""Exception types. Error text never contains request or response bodies:
solver outputs, cookies and tokens are bearer credentials."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "APIError",
    "BypassFastError",
    "RequestError",
    "ResponseError",
    "ResponseMeta",
    "ValidationError",
    "is_error_code",
]


@dataclass
class ResponseMeta:
    """Value-free diagnostics for one SDK call.

    ``attempts`` counts every HTTP attempt the call made, including automatic
    retries and the script-cache fallback request.
    """

    status_code: int = 0
    request_id: str = ""
    edge: str = ""
    server_timing: str = ""
    attempts: int = 0


class BypassFastError(Exception):
    """Base class for every error raised by this SDK."""


class ValidationError(BypassFastError, ValueError):
    """A request or option rejected locally, before any API call."""

    def __init__(self, field: str, message: str, cause: BaseException | None = None) -> None:
        super().__init__(field, message, cause)
        self.field = field
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        if not self.field:
            return f"bypassfast: {self.message}"
        return f"bypassfast: invalid {self.field}: {self.message}"


class RequestError(BypassFastError):
    """The HTTP request could not be sent or no response arrived (including
    timeouts).

    Never retried automatically: the connection may have failed after a
    non-idempotent solve completed.
    """

    def __init__(self, operation: str, cause: BaseException) -> None:
        super().__init__(operation, cause)
        self.operation = operation
        self.cause = cause

    def __str__(self) -> str:
        return f"bypassfast: {self.operation}: {self.cause}"


class ResponseError(BypassFastError):
    """A response arrived but could not be read or decoded."""

    def __init__(self, response: ResponseMeta, cause: BaseException) -> None:
        super().__init__(response, cause)
        self.response = response
        self.cause = cause

    def __str__(self) -> str:
        meta = self.response
        if meta.request_id:
            return (
                f"bypassfast: invalid response (status {meta.status_code}, "
                f"request {meta.request_id}): {self.cause}"
            )
        return f"bypassfast: invalid response (status {meta.status_code}): {self.cause}"


_NOT_RETRYABLE = frozenset(
    {
        "quota_exceeded",
        "billing_disabled",
        "hard_block",
        "unsupported_challenge",
        "unsupported_script",
        "solve_failed",
        "not_verified",
        "solve_timeout",
        "script_cache_miss",
    }
)

_RETRYABLE = frozenset(
    {
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
        # The site or HUMAN failed to answer (424); a later attempt can
        # succeed on the same exit.
        "target_error",
    }
)


class APIError(BypassFastError):
    """A non-2xx response from Bypass Fast (3xx included: redirects are never
    followed).

    ``code`` is the stable machine contract, read from ``error`` or Akamai's
    legacy ``error_code``; ``"http_error"`` when the body has neither.
    ``message`` is human-readable and may echo request input, so it is kept out
    of ``str(err)``. ``retry_after`` is the server's retry hint in seconds
    (the ``retry_after_ms`` body field when present, otherwise the
    ``Retry-After`` header); ``0.0`` means none.
    """

    def __init__(
        self,
        response: ResponseMeta,
        code: str,
        message: str = "",
        stage: str = "",
        reason: str = "",
        retry_after: float = 0.0,
    ) -> None:
        super().__init__(response, code, message, stage, reason, retry_after)
        self.response = response
        self.code = code
        self.message = message
        self.stage = stage
        # For PerimeterX proxy_error: how the proxy failed (proxy_auth_failed,
        # proxy_no_exit, proxy_refused, proxy_unreachable, proxy_timeout,
        # proxy_connection_dropped, proxy_tls_intercepted).
        self.reason = reason
        self.retry_after = retry_after

    def __str__(self) -> str:
        code = self.code or "http_error"
        meta = self.response
        if meta.request_id:
            return f"bypassfast: {code} (status {meta.status_code}, request {meta.request_id})"
        return f"bypassfast: {code} (status {meta.status_code})"

    def __repr__(self) -> str:
        return (
            f"APIError(code={self.code!r}, status_code={self.response.status_code!r}, "
            f"request_id={self.response.request_id!r})"
        )

    @property
    def retryable(self) -> bool:
        """Whether retrying this explicit API response is safe and recommended.

        Says nothing about transport errors, which are never retried.
        """
        if self.code == "proxy_error":
            # The customer's proxy or exit failed; the same request through
            # the same exit fails again. Switch exits instead.
            return False
        if self.code in _NOT_RETRYABLE:
            return False
        if self.code in _RETRYABLE:
            return True
        return 500 <= self.response.status_code <= 599


def is_error_code(err: BaseException | None, code: str) -> bool:
    """Report whether ``err``, or an exception in its ``__cause__`` chain, is
    an :class:`APIError` with the given machine code."""
    seen: set[int] = set()
    current = err
    while current is not None and id(current) not in seen:
        if isinstance(current, APIError):
            return current.code == code
        seen.add(id(current))
        current = current.__cause__
    return False
