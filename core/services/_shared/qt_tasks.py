"""Reusable Qt background-task primitives.

Domain services own their result events; this module only standardizes callable
execution and exception reporting so each service does not reinvent thread
lifecycle plumbing.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal


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
