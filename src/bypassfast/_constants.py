"""Wire-contract constants shared by every client (see the Go reference SDK)."""

from __future__ import annotations

from typing import Literal

from ._version import __version__

DEFAULT_BASE_URL = "https://api.bypass.fast"
DEFAULT_COMPRESSION_THRESHOLD = 1024

MIB = 1024 * 1024
MAX_REQUEST_BYTES = 1 * MIB
# PerimeterX holdcaptcha bodies carry the customer's HTML block page; the edge
# and the solver allow 2 MiB for that route only.
MAX_PERIMETERX_REQUEST_BYTES = 2 * MIB
MAX_RESPONSE_BYTES = 4 * MIB

MAX_CONFIGURED_RETRIES = 10
# Safety cap, in seconds, on every retry delay, hint and budget.
MAX_RETRY_DELAY = 3600.0

DEFAULT_MAX_RETRIES = 2
DEFAULT_BASE_DELAY = 0.5
DEFAULT_MAX_DELAY = 8.0

DEFAULT_SOLVER_BUSY_RETRY_BUDGET = 45.0
# Minimum wait when a solver_busy response carries no Retry-After or
# retry_after_ms hint.
SOLVER_BUSY_RETRY_FLOOR = 1.0
# Caps the jitter window of one solver_busy wait.
SOLVER_BUSY_RETRY_MAX_DELAY = 10.0

# Per-attempt HTTP timeouts in seconds. The edge allows 150 s for PerimeterX
# and about 60 s for every other solver.
DEFAULT_TIMEOUT = 65.0
DEFAULT_PERIMETERX_TIMEOUT = 155.0

SCRIPT_MEMORY_CAPACITY = 256

SDK_USER_AGENT = "bypassfast-python/" + __version__

Solver = Literal["akamai", "kasada", "incapsula", "perimeterx"]
SOLVERS: tuple[str, ...] = ("akamai", "kasada", "incapsula", "perimeterx")
