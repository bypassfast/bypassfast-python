"""Per-client script memory: which script digests the API recently answered
with ``script_cache_miss``.

Every script-bearing call travels hash-first. After one miss the client sends
the script body straight away for the same digest, so a burst of solves for a
brand-new script costs one extra round trip rather than one per call; a
successful script-bearing call registers the script and forgets the miss.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable

from . import _constants as const


class ScriptMemory:
    """A small thread-safe record of recent misses. It remembers only digests
    and timestamps, never scripts, tokens, cookies or device data."""

    def __init__(
        self,
        capacity: int,
        now: Callable[[], float],
        ttl: float = const.SCRIPT_MISS_MEMORY,
    ) -> None:
        self._capacity = capacity
        self._now = now
        self._ttl = ttl
        self._lock = threading.Lock()
        self._missed: OrderedDict[str, float] = OrderedDict()

    def recently_missed(self, key: str) -> bool:
        """True when the API answered ``script_cache_miss`` for ``key`` within
        the memory window."""
        if not key:
            return False
        with self._lock:
            missed_at = self._missed.get(key)
            if missed_at is None:
                return False
            if self._now() - missed_at >= self._ttl:
                del self._missed[key]
                return False
            return True

    def mark_missed(self, key: str) -> None:
        """Record that the API has no script for ``key``."""
        if not key:
            return
        with self._lock:
            if key in self._missed:
                self._missed.move_to_end(key)
            elif len(self._missed) >= self._capacity:
                self._missed.popitem(last=False)
            self._missed[key] = self._now()

    def mark_registered(self, key: str) -> None:
        """Forget a miss: a script-bearing call for ``key`` succeeded."""
        if not key:
            return
        with self._lock:
            self._missed.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._missed)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._missed
