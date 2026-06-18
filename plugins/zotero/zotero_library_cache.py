from __future__ import annotations

import threading
from typing import Dict, List


class ZoteroLibraryCache:
    """In-memory plugin cache for items just written through PyZotero."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: Dict[str, dict] = {}

    def upsert(self, item: dict) -> None:
        key = item.get("key") or item.get("source_item_key") or item.get("item_id")
        if not key:
            return
        cached = dict(item)
        cached["key"] = str(key)
        cached.setdefault("item_id", str(key))
        cached.setdefault("doc_id", f"zotero:{key}")
        cached.setdefault("_source", "pyzotero_cache")
        with self._lock:
            self._items[str(key)] = cached

    def items(self) -> List[dict]:
        with self._lock:
            return [dict(item) for item in self._items.values()]

    def merge(self, items: List[dict]) -> List[dict]:
        merged: Dict[str, dict] = {}
        for item in items:
            key = item.get("key") or item.get("item_id") or item.get("doc_id")
            if key:
                merged[str(key)] = item
        for item in self.items():
            key = item.get("key") or item.get("item_id") or item.get("doc_id")
            if key:
                merged.setdefault(str(key), item)
        return list(merged.values())
