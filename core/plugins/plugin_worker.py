"""
core/plugins/plugin_worker.py

Generic QThread wrapper for plugin background tasks.
Used by _PluginTaskNamespace (api.tasks.run_background).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from PySide6.QtCore import QThread, Signal


@dataclass
class PluginTaskContext:
    """Cancellation and progress surface passed to cooperative plugin tasks."""

    cancel_check: Callable[[], bool]
    progress_callback: Callable[[int, int, str], None]
    partial_callback: Callable[[Any], None]

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_check()

    def report(self, current: int, total: int = 0, message: str = "") -> None:
        self.progress_callback(int(current), int(total), str(message))

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise RuntimeError("Task cancelled.")

    def publish(self, value: Any) -> None:
        """Publish one small incremental result to the main thread."""
        self.partial_callback(value)


class PluginWorker(QThread):
    """Runs a callable in a background thread, emits finished or error."""

    finished: Signal = Signal(object)  # result value
    error: Signal = Signal(str)        # error message string
    progress: Signal = Signal(int, int, str)
    partial: Signal = Signal(object)

    def __init__(
        self,
        func: Callable,
        args: Sequence[Any] = (),
        job_name: str = "",
        pass_cancel_check: bool = False,
        pass_task_context: bool = False,
    ) -> None:
        super().__init__()
        self._func = func
        self._args = tuple(args)
        self.job_name = job_name
        self._pass_cancel_check = pass_cancel_check
        self._pass_task_context = pass_task_context
        self.job = None  # set by ProcessRegistry.enqueue_runner()

    def is_cancelled(self) -> bool:
        return bool(
            self.isInterruptionRequested()
            or (self.job and self.job.abort_event.is_set())
        )

    def run(self) -> None:
        try:
            if self._pass_task_context:
                context = PluginTaskContext(
                    cancel_check=self.is_cancelled,
                    progress_callback=self.progress.emit,
                    partial_callback=self.partial.emit,
                )
                result = self._func(*self._args, task_context=context)
            elif self._pass_cancel_check:
                result = self._func(*self._args, cancel_check=self.is_cancelled)
            else:
                result = self._func(*self._args)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
