# core/services/ocr_app_service.py
import os
import shutil
from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
from core.events.domains.tool_events import OCRIntent, OCRPayload, OCRStatus, OCRStatusPayload
from core.events.domains.document_events import (
    DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload, SourceEvent,
)
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload
from core.ocr_engine import run_ocr_on_pdf
from core.services.document.pdf_ocr import CANCELLED_MESSAGE, ERROR_PREFIX

WORKER_STOP_TIMEOUT_MS = 5000
MAX_LISTED_PAGES = 10
# One Tesseract process plus its rendered page (measured ~60 MB on book.pdf; padded).
OCR_WORKER_BYTES = 200 * 1024 * 1024
BUSY_MESSAGE = "OCR is already running. Wait for it to finish or cancel it first."


class OCRWorker(QThread):
    progress_updated = Signal(int, int)

    def __init__(self, file_path, ui_mode, parent=None, memory_manager=None):
        super().__init__(parent)
        self.file_path = file_path
        self.ui_mode = ui_mode
        self.memory_manager = memory_manager
        self.job = None            # set by ProcessRegistry
        self.result_text = ""
        self.save_path = None
        self.report = {}

    def run(self):
        engine_mode = "text"
        if self.ui_mode == "save_new":
            engine_mode = "pdf"
            base, ext = os.path.splitext(self.file_path)
            self.save_path = f"{base}_ocr{ext}"
        elif self.ui_mode == "replace":
            engine_mode = "pdf"
            self.save_path = self.file_path + ".tmp"
        self.result_text = run_ocr_on_pdf(
            self.file_path,
            mode=engine_mode,
            save_path=self.save_path,
            progress_callback=lambda cur, tot: self.progress_updated.emit(cur, tot),
            cancel_check=self._cancelled,
            report=self.report,
            reserve_workers=self._reserve_workers if self.memory_manager is not None else None,
        )

    def _cancelled(self) -> bool:
        # Cancel from the OCR prompt (interruption) or the process monitor (job abort).
        return self.isInterruptionRequested() or bool(self.job and self.job.abort_event.is_set())

    def _reserve_workers(self, maximum: int) -> int:
        """Free memory for OCR and learn how many Tesseract processes fit."""
        return self.memory_manager.reserve(
            0, "OCR", per_worker=OCR_WORKER_BYTES, max_workers=maximum).workers


class OCRAppService(QObject):
    def __init__(self, project_manager, memory_manager=None, process_registry=None):
        super().__init__()
        self.pm = project_manager
        self.memory_manager = memory_manager
        self.process_registry = process_registry
        self.bus = EventBus.get_instance()
        self.worker = None
        self._request = None
        self.bus.ocr_action_requested.connect(self._handle_intent)
        self.bus.project_clearing_started.connect(self._on_project_closing)

    def is_running(self) -> bool:
        return self.worker is not None

    def _handle_intent(self, intent: OCRIntent, payload: OCRPayload):
        if intent == OCRIntent.RUN:
            self._start_ocr(payload)
        elif intent == OCRIntent.CANCEL:
            self.cancel()

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.requestInterruption()

    def _on_project_closing(self, *_args) -> None:
        # The worker may be writing a file for this project; stop it cleanly.
        if self.worker is not None:
            self.worker.requestInterruption()
            self.worker.wait(WORKER_STOP_TIMEOUT_MS)

    def _status(self, status, msg, **fields):
        request = self._request or {}
        self.bus.ocr_status_updated.emit(status, OCRStatusPayload(
            status=status, msg=msg, file_path=request.get("file_path"),
            source_id=request.get("source_id"), mode=request.get("mode"), **fields,
        ))

    def _start_ocr(self, payload: OCRPayload):
        file_path = payload.get("file_path")
        mode = payload.get("mode") or "replace"
        if self.worker is not None:
            self.bus.ocr_status_updated.emit(OCRStatus.ERROR, OCRStatusPayload(
                status=OCRStatus.ERROR, msg=BUSY_MESSAGE, file_path=file_path,
                source_id=payload.get("source_id"), mode=mode))
            return
        self._request = {"file_path": file_path, "mode": mode, "source_id": payload.get("source_id")}
        if not file_path:
            self._status(OCRStatus.ERROR, "No document loaded.")
            self._request = None
            return

        self.worker = OCRWorker(file_path, mode, self, memory_manager=self.memory_manager)
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.finished.connect(self._finalize_ocr)
        self._status(OCRStatus.RUNNING, "Starting OCR…", progress=0, total=0)
        if self.process_registry is not None:
            # Shown in the process monitor (with cancel) and admitted by the memory manager.
            self.process_registry.enqueue_runner(
                self.worker, f"OCR: {os.path.basename(file_path)}", job_type="OCR", is_express=True,
                metadata={"source_id": payload.get("source_id"), "source_path": file_path, "can_cancel": True})
        else:
            self.worker.start()

    def _on_progress(self, current: int, total: int) -> None:
        message = f"Processing Page {current}/{total}..."
        self._status(OCRStatus.RUNNING, message, progress=current, total=total)
        job = getattr(self.worker, "job", None)
        if self.process_registry is not None and job is not None:
            self.process_registry.update_job_progress(job.id, current, total, message)

    def _finalize_ocr(self):
        worker, self.worker = self.worker, None
        if worker is None:
            return
        text, save_path = worker.result_text, worker.save_path
        original_path, ui_mode = worker.file_path, worker.ui_mode
        if self.process_registry is not None and worker.job is not None:
            self.process_registry.complete_job(worker.job.id)
        worker.deleteLater()
        try:
            if text.startswith(ERROR_PREFIX):
                self._discard_temp(ui_mode, save_path)
                status = OCRStatus.CANCELLED if text == CANCELLED_MESSAGE else OCRStatus.ERROR
                self._status(status, text[len(ERROR_PREFIX):] if status == OCRStatus.CANCELLED else text)
                return
            msg = "OCR Complete!"
            failed = worker.report.get("failed_pages") or []
            if failed:
                msg += (f" {len(failed)} page(s) could not be read and have no text: "
                        f"{', '.join(str(page) for page in failed[:MAX_LISTED_PAGES])}"
                        f"{'…' if len(failed) > MAX_LISTED_PAGES else ''}.")
            if ui_mode == "replace":
                self._replace_original(original_path, save_path)
                msg += f" Replaced {os.path.basename(original_path)}"
            elif ui_mode == "save_new":
                self._add_ocr_copy(save_path)
                msg += f" Saved to {os.path.basename(save_path)}"
            # Only the text-mode preview carries recognised text (§2.14 small signals).
            self._status(OCRStatus.COMPLETE, msg, text=text if ui_mode == "text" else "",
                         output_path=save_path if ui_mode == "save_new" else original_path)
        finally:
            self._request = None

    @staticmethod
    def _discard_temp(ui_mode, save_path):
        if ui_mode == "replace" and save_path and os.path.exists(save_path):
            try:
                os.remove(save_path)
            except OSError:
                pass

    def _replace_original(self, original_path, save_path):
        # Safely swap files in the background without UI locks interfering
        if original_path in self.pm.open_docs:
            if not self.pm.open_docs[original_path].is_closed:
                self.pm.open_docs[original_path].close()
            del self.pm.open_docs[original_path]
        try:
            os.replace(save_path, original_path)
        except OSError:
            shutil.copy2(save_path, original_path)
            os.remove(save_path)
        # Emit document intent to automatically reload it in the UI
        self.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=original_path))

    def _add_ocr_copy(self, save_path):
        if self.pm.add_pdf(save_path):
            source = self.pm.get_source_record_by_path(save_path) or {}
            payload = DocumentEventPayload(path=save_path, source_id=source.get("id"), source_type="pdf")
            self.bus.source_added.emit(SourceEvent.ADDED, payload)
            self.bus.document_added.emit(DocumentEvent.DOCUMENT_ADDED, payload)
        self.bus.project_loaded.emit(ProjectEvent.LOADED, ProjectEventPayload())
        self.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=save_path))
