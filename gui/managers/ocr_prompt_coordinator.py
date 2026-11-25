"""Open the OCR prompt whenever intelligence is blocked on a missing text layer.

Listens to the scheduler's stage updates, so every entry point that runs
intelligence (adding a source, Run missing, analysis preparation, plugins)
gets the same prompt. One prompt per source at a time.
"""
from PySide6.QtCore import QObject, Qt

from core.models.intelligence_models import StageStatus
from gui.components.dialogs.ocr_required_dialog import OcrRequiredDialog

OCR_REASONS = frozenset({"needs_ocr", "needs_ocr_engine"})
DIALOG_KEY_PREFIX = "ocr_required:"


class OcrPromptCoordinator(QObject):
    def __init__(self, app_context, window, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self._window = window
        bus = getattr(app_context, "bus", None)
        if bus is not None:
            bus.intelligence_state_changed.connect(self._on_state, Qt.ConnectionType.QueuedConnection)

    def _on_state(self, _event, payload):
        if not isinstance(payload, dict) or payload.get("status") != StageStatus.BLOCKED.value:
            return
        if payload.get("reason") not in OCR_REASONS:
            return
        details = {**(payload.get("details") or {}), "reason": payload.get("reason"),
                   "source_id": payload.get("source_id") or ""}
        self.show_prompt(details)

    def show_prompt(self, details):
        manager = getattr(self._ctx, "dialog_manager", None)
        if manager is None:
            return None
        return manager.show(
            OcrRequiredDialog,
            key=DIALOG_KEY_PREFIX + str(details.get("source_id") or details.get("path") or ""),
            singleton=True,
            factory=lambda: OcrRequiredDialog(self._ctx, details, parent=self._window),
        )
