from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class EditLease:
    session_id: str
    device_name: str
    expires_at: float


class EditLeaseManager:
    def __init__(self, ttl_seconds: int = 45, on_change: Callable[[bool, str], None] | None = None):
        self.ttl_seconds = ttl_seconds
        self._on_change = on_change
        self._lock = threading.RLock()
        self._lease: EditLease | None = None

    def acquire(self, session_id: str, device_name: str) -> tuple[bool, dict]:
        with self._lock:
            self._expire_locked()
            if self._lease and self._lease.session_id != session_id:
                return False, self.status()
            changed = self._lease is None
            self._lease = EditLease(session_id, device_name, time.time() + self.ttl_seconds)
            if changed and self._on_change:
                self._on_change(True, device_name)
            return True, self.status()

    def heartbeat(self, session_id: str) -> bool:
        with self._lock:
            self._expire_locked()
            if not self._lease or self._lease.session_id != session_id:
                return False
            self._lease.expires_at = time.time() + self.ttl_seconds
            return True

    def release(self, session_id: str | None = None, *, force: bool = False) -> bool:
        with self._lock:
            if not self._lease or (not force and self._lease.session_id != session_id):
                return False
            self._lease = None
            if self._on_change:
                self._on_change(False, "")
            return True

    def owns(self, session_id: str) -> bool:
        with self._lock:
            self._expire_locked()
            return bool(self._lease and self._lease.session_id == session_id)

    def status(self) -> dict:
        with self._lock:
            self._expire_locked()
            return {
                "active": self._lease is not None,
                "device_name": self._lease.device_name if self._lease else None,
                "expires_at": self._lease.expires_at if self._lease else None,
            }

    def _expire_locked(self) -> None:
        if self._lease and self._lease.expires_at <= time.time():
            self._lease = None
            if self._on_change:
                self._on_change(False, "")
