"""Qt adapter that runs one headless intelligence scheduler stage."""
from __future__ import annotations

import uuid

from PySide6.QtCore import QThread, Signal


class IntelligenceStageWorker(QThread):
    """ProcessRegistry-compatible adapter; all stage logic stays headless."""

    # Deliberately not named "finished": a Signal on a QThread subclass shadows
    # QThread.finished in the meta-object, and ProcessRegistry needs the real
    # thread-exit signal to release its keep-alive reference to the runner.
    stage_finished = Signal(object)
    error = Signal(str)
    progress = Signal(object)

    def __init__(self, scheduler, source_id: str, stage: str):
        super().__init__()
        self.scheduler = scheduler
        self.source_id = source_id
        self.stage = stage
        self.job = None
        self.run_id = f"stage_run:{uuid.uuid4()}"

    def run(self) -> None:
        try:
            result = self.scheduler.run_stage_now(
                self.source_id,
                self.stage,
                cancel_check=lambda: bool(
                    self.job and self.job.abort_event.is_set()
                ),
                worker=self,
            )
            self.stage_finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
