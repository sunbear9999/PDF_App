"""
core/plugins/plugin_worker.py

Generic QThread wrapper for plugin background tasks.
Used by _PluginTaskNamespace (api.tasks.run_background).
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from PySide6.QtCore import QThread, Signal


class PluginWorker(QThread):
    """Runs a callable in a background thread, emits finished or error."""

    finished: Signal = Signal(object)  # result value
    error: Signal = Signal(str)        # error message string

    def __init__(
        self,
        func: Callable,
        args: Sequence[Any] = (),
        job_name: str = "",
        pass_cancel_check: bool = False,
    ) -> None:
        super().__init__()
        self._func = func
        self._args = tuple(args)
        self.job_name = job_name
        self._pass_cancel_check = pass_cancel_check
        self.job = None  # set by ProcessRegistry.enqueue_runner()

    def is_cancelled(self) -> bool:
        return bool(
            self.isInterruptionRequested()
            or (self.job and self.job.abort_event.is_set())
        )

    def run(self) -> None:
        try:
            if self._pass_cancel_check:
                result = self._func(*self._args, cancel_check=self.is_cancelled)
            else:
                result = self._func(*self._args)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
