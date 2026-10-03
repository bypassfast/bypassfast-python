"""Per-client script memory: a small thread-safe LRU of script hashes."""

from __future__ import annotations

import threading
from collections import OrderedDict


class ScriptMemory:
    """Remembers only SHA-256 digests keyed by ``akamai:<sha>`` or
    ``incapsula:<mode>:<script_url>``; never scripts, tokens, cookies or
    device data."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._values: OrderedDict[str, str] = OrderedDict()

    def matches(self, key: str, value: str) -> bool:
        """True when ``key`` holds ``value``; a hit becomes most recent."""
        with self._lock:
            stored = self._values.get(key)
            if stored is None or value == "" or stored != value:
                return False
            self._values.move_to_end(key)
            return True

    def put(self, key: str, value: str) -> None:
        """Store ``value`` as the most recent entry, evicting the least recent
        entry when full. Empty keys or values are ignored."""
        if key == "" or value == "":
            return
        with self._lock:
            if key in self._values:
                self._values[key] = value
                self._values.move_to_end(key)
                return
            if len(self._values) >= self._capacity:
                self._values.popitem(last=False)
            self._values[key] = value

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._values
