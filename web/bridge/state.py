from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any


class RevisionConflict(RuntimeError):
    def __init__(self, expected: int, actual: int):
        super().__init__(f"Expected project revision {expected}, current revision is {actual}")
        self.expected = expected
        self.actual = actual


class WebState:
    """Thread-safe revision journal shared by Qt and the ASGI thread."""
    def __init__(self, history_size: int = 1000):
        self._lock = threading.Condition(threading.RLock())
        self.project_instance_id = str(uuid.uuid4())
        self.revision = 0
        self.sequence = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=history_size)

    def reset_project(self) -> None:
        with self._lock:
            self.project_instance_id = str(uuid.uuid4())
            self.revision = 0
            self.sequence += 1
            self._append("project", "replaced", None, {})

    def check(self, expected: int | None) -> None:
        with self._lock:
            if expected is not None and expected != self.revision:
                raise RevisionConflict(expected, self.revision)

    def publish(self, domain: str, operation: str, resource_id: str | None, payload: Any, *, mutate: bool = True) -> dict[str, Any]:
        with self._lock:
            if mutate:
                self.revision += 1
            self.sequence += 1
            event = self._append(domain, operation, resource_id, payload)
            self._lock.notify_all()
            return event

    def _append(self, domain: str, operation: str, resource_id: str | None, payload: Any) -> dict[str, Any]:
        event = {
            "sequence": self.sequence,
            "revision": self.revision,
            "project_instance_id": self.project_instance_id,
            "domain": domain,
            "operation": operation,
            "resource_id": resource_id,
            "payload": payload,
            "timestamp": time.time(),
        }
        self._events.append(event)
        return event

    def wait_after(self, sequence: int, timeout: float = 20.0) -> list[dict[str, Any]]:
        with self._lock:
            found = [e for e in self._events if e["sequence"] > sequence]
            if not found:
                self._lock.wait(timeout)
                found = [e for e in self._events if e["sequence"] > sequence]
            return found

    def envelope(self, data: Any) -> dict[str, Any]:
        with self._lock:
            return {"project_instance_id": self.project_instance_id, "revision": self.revision, "data": data}
