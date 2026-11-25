"""A small LRU mapping bounded by entry count and, optionally, by bytes.

Shared by caches that used to grow without limit (per-file hashes, source
revisions) and the reference hydrator, so each does not reimplement eviction.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable, Hashable, Iterator, Optional


class BoundedCache:
    def __init__(self, max_entries: int, max_bytes: int = 0,
                 sizeof: Optional[Callable[[Any], int]] = None) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._sizeof = sizeof
        self._items: "OrderedDict[Hashable, tuple[Any, int]]" = OrderedDict()
        self.bytes = 0
        self._lock = threading.Lock()

    def get(self, key: Hashable, default: Any = None) -> Any:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return default
            self._items.move_to_end(key)
            return entry[0]

    def set(self, key: Hashable, value: Any) -> bool:
        """Store ``value``; False when it alone exceeds the byte budget."""
        size = self._sizeof(value) if self._sizeof else 0
        with self._lock:
            self._pop(key)
            if self.max_bytes and size > self.max_bytes:
                return False
            self._items[key] = (value, size)
            self.bytes += size
            while len(self._items) > self.max_entries or (self.max_bytes and self.bytes > self.max_bytes):
                self._pop(next(iter(self._items)))
        return True

    def pop(self, key: Hashable) -> Any:
        with self._lock:
            entry = self._pop(key)
        return entry[0] if entry else None

    def clear(self) -> int:
        with self._lock:
            freed = self.bytes
            self._items.clear()
            self.bytes = 0
        return freed

    def _pop(self, key: Hashable):
        entry = self._items.pop(key, None)
        if entry:
            self.bytes -= entry[1]
        return entry

    def __contains__(self, key: Hashable) -> bool:
        return key in self._items

    def __getitem__(self, key: Hashable) -> Any:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __setitem__(self, key: Hashable, value: Any) -> None:
        self.set(key, value)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Hashable]:
        return iter(list(self._items))


_MISSING = object()
