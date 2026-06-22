from __future__ import annotations

import threading
from concurrent.futures import Future
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot


class QtCommandDispatcher(QObject):
    """The only boundary through which ASGI requests may enter Papyrus."""
    requested = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.requested.connect(self._execute, Qt.ConnectionType.QueuedConnection)

    def register(self, name: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        self._handlers[name] = handler

    def invoke(self, name: str, payload: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
        request = (name, payload or {}, Future())
        if QThread.currentThread() is self.thread():
            self._execute(request)
        else:
            self.requested.emit(request)
        return request[2].result(timeout=timeout)

    @Slot(object)
    def _execute(self, request: tuple[str, dict[str, Any], Future]) -> None:
        name, payload, future = request
        if future.done():
            return
        try:
            handler = self._handlers.get(name)
            if handler is None:
                raise KeyError(f"Unknown web command: {name}")
            future.set_result(handler(payload))
        except BaseException as exc:
            future.set_exception(exc)
