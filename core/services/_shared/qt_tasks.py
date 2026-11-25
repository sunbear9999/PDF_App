"""Reusable Qt background-task primitives.

Domain services own their result events; this module only standardizes callable
execution and exception reporting so each service does not reinvent thread
lifecycle plumbing.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from collections import deque
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal


class CallableWorker(QThread):
    """Execute a no-argument callable on a QThread."""

    finished = Signal()
    error = Signal(str)

    def __init__(self, operation: Callable[[], None], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._operation = operation

    def run(self) -> None:
        try:
            self._operation()
            self.finished.emit()
        except Exception as exc:
            self.error.emit(f"{exc}\n{traceback.format_exc()}")


PACE_MS = 15


class MainThreadCaller(QObject):
    """Run callables on the thread this object lives in (create it on the Qt
    thread to hand results from worker threads back to the UI)."""

    _call = Signal(object)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._paced = None
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def call(self, operation: Callable[[], None]) -> None:
        self._call.emit(operation)

    def call_paced(self, operation: Callable[[], None]) -> None:
        """Like ``call``, but queued calls run one per timer tick, so a burst (100 sources
        registered at once) leaves the event loop free to paint between them."""
        self._call.emit(lambda: self._enqueue(operation))

    def _enqueue(self, operation: Callable[[], None]) -> None:
        if self._paced is None:
            from collections import deque
            from PySide6.QtCore import QTimer
            self._paced = deque()
            self._pacer = QTimer(self)
            self._pacer.setInterval(PACE_MS)
            self._pacer.timeout.connect(self._run_next)
        self._paced.append(operation)
        if not self._pacer.isActive():
            self._pacer.start()

    def _run_next(self) -> None:
        if self._paced:
            self._run(self._paced.popleft())
        if not self._paced:
            self._pacer.stop()

    def _run(self, operation: Callable[[], None]) -> None:
        try:
            operation()
        except RuntimeError:
            pass   # a widget the operation touched was deleted meanwhile


class WorkerSet:
    """Retain active QThreads until either terminal signal fires."""

    def __init__(self) -> None:
        self._workers: set[QThread] = set()

    def start(self, worker: QThread, on_success, on_error) -> QThread:
        def success() -> None:
            try:
                on_success()
            finally:
                self._workers.discard(worker)

        def error(message: str) -> None:
            try:
                on_error(message)
            finally:
                self._workers.discard(worker)

        worker.finished.connect(success)
        worker.error.connect(error)
        self._workers.add(worker)
        worker.start()
        return worker

    def discard(self, worker: QThread) -> None:
        self._workers.discard(worker)

    def __len__(self) -> int:
        return len(self._workers)


class SerialTaskQueue(QObject):
    """Run short service operations on QThreads, one at a time.

    ``submit`` is signal-backed, so callers may safely enqueue follow-up work
    from another worker without touching Qt objects from that worker thread.
    """

    _submitted = Signal(object)
    _clear_requested = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pending = deque()
        self._worker = None
        self._submitted.connect(self._enqueue, Qt.ConnectionType.QueuedConnection)
        self._clear_requested.connect(self._clear, Qt.ConnectionType.QueuedConnection)

    def submit(self, operation: Callable[[], None]) -> None:
        self._submitted.emit(operation)

    def clear(self) -> None:
        self._clear_requested.emit()

    def _clear(self) -> None:
        self._pending.clear()
        if self._worker is not None:
            self._worker.requestInterruption()

    def _enqueue(self, operation: Callable[[], None]) -> None:
        self._pending.append(operation)
        if self._worker is None:
            self._start_next()

    def _start_next(self) -> None:
        if not self._pending:
            self._worker = None
            return
        worker = CallableWorker(self._pending.popleft(), self)
        self._worker = worker
        worker.finished.connect(lambda current=worker: self._advance(current))
        worker.error.connect(lambda message, current=worker: self._failed(current, message))
        worker.start()

    def _failed(self, worker: QThread, message: str) -> None:
        print(f"Background service task failed: {message}")
        self._advance(worker)

    def _advance(self, worker: QThread) -> None:
        if worker is not self._worker:
            return
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._start_next()
