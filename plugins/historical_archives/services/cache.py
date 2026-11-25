"""Bounded SQLite response cache for archive metadata requests."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from ..models import stable_json


class ResponseCache:
    def __init__(
        self, path: str, ttl_seconds: int = 86400,
        max_entries: int = 500, max_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self._path = Path(path)
        self._ttl = max(60, int(ttl_seconds))
        self._max_entries = max(20, int(max_entries))
        self._max_bytes = max(1024 * 1024, int(max_bytes))
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS responses "
            "(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL)"
        )

    def get(self, key: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, created_at FROM responses WHERE cache_key=?", (key,)
            ).fetchone()
            if not row:
                return None
            if time.time() - float(row[1]) > self._ttl:
                self._conn.execute("DELETE FROM responses WHERE cache_key=?", (key,))
                self._conn.commit()
                return None
            return json.loads(row[0])

    def set(self, key: str, payload) -> None:
        serialized = stable_json(payload)
        if len(serialized.encode("utf-8")) > self._max_bytes:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO responses(cache_key,payload,created_at) VALUES(?,?,?)",
                (key, serialized, time.time()),
            )
            self._conn.execute(
                "DELETE FROM responses WHERE cache_key IN "
                "(SELECT cache_key FROM responses ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (self._max_entries,),
            )
            self._trim_bytes()
            self._conn.commit()

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM responses")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _trim_bytes(self) -> None:
        total = self._conn.execute(
            "SELECT COALESCE(SUM(length(CAST(payload AS BLOB))), 0) FROM responses"
        ).fetchone()[0]
        while total > self._max_bytes:
            row = self._conn.execute(
                "SELECT cache_key, length(CAST(payload AS BLOB)) "
                "FROM responses ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                break
            self._conn.execute("DELETE FROM responses WHERE cache_key=?", (row[0],))
            total -= int(row[1] or 0)


class CachedNetworkClient:
    def __init__(self, network, cache: ResponseCache) -> None:
        self._network = network
        self._cache = cache

    def get_json(self, url, *, params=None, **kwargs):
        key = hashlib.sha256(
            stable_json({"url": url, "params": params or {}}).encode("utf-8")
        ).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        payload = self._network.get_json(url, params=params, **kwargs)
        self._cache.set(key, payload)
        return payload

    def get_bytes(self, *args, **kwargs):
        return self._network.get_bytes(*args, **kwargs)

    def download(self, *args, **kwargs):
        return self._network.download(*args, **kwargs)
