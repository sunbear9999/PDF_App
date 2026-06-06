# core/services/ocr_app_service.py
import os
import shutil
from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
from core.events.domains.tool_events import OCRIntent, OCRPayload, OCRStatus, OCRStatusPayload
from core.events.domains.document_events import DocumentIntent, DocumentPayload
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload

class OCRWorker(QThread):
    progress_updated = Signal(int, int)
    
    def __init__(self, file_path, ui_mode, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.ui_mode = ui_mode
        self.result_text = ""
        self.save_path = None

    def run(self):
        engine_mode = "text"
        if self.ui_mode == "save_new":
            engine_mode = "pdf"
            base, ext = os.path.splitext(self.file_path)
            self.save_path = f"{base}_ocr{ext}"
        elif self.ui_mode == "replace":
            engine_mode = "pdf"
            self.save_path = self.file_path + ".tmp" 

        from core.ocr_engine import run_ocr_on_pdf
        self.result_text = run_ocr_on_pdf(
            self.file_path, 
            mode=engine_mode, 
            save_path=self.save_path, 
            progress_callback=lambda cur, tot: self.progress_updated.emit(cur, tot)
        )

class OCRAppService(QObject):
    def __init__(self, project_manager):
        super().__init__()
        self.pm = project_manager
        self.bus = EventBus.get_instance()
        self.worker = None
        self.bus.ocr_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent: OCRIntent, payload: OCRPayload):
        if intent == OCRIntent.RUN:
            self._start_ocr(payload)

    def _start_ocr(self, payload: OCRPayload):
        file_path = payload.get("file_path")
        mode = payload.get("mode")
        
        if not file_path:
            self.bus.ocr_status_updated.emit(
                OCRStatus.ERROR,
                OCRStatusPayload(status=OCRStatus.ERROR, msg="No document loaded."),
            )
            return
            
        self.worker = OCRWorker(file_path, mode)
        self.worker.progress_updated.connect(
            lambda cur, tot: self.bus.ocr_status_updated.emit(
                OCRStatus.RUNNING,
                OCRStatusPayload(
                    status=OCRStatus.RUNNING,
                    msg=f"Processing Page {cur}/{tot}...",
                    progress=cur,
                    total=tot,
                ),
            )
        )
        self.worker.finished.connect(lambda: self._finalize_ocr(file_path, mode))
        self.worker.start()

    def _finalize_ocr(self, original_path, ui_mode):
        text = self.worker.result_text
        save_path = self.worker.save_path
        
        if text.startswith("OCR Engine Error"):
            if ui_mode == "replace" and save_path and os.path.exists(save_path):
                try: os.remove(save_path)
                except: pass
            self.bus.ocr_status_updated.emit(
                OCRStatus.ERROR,
                OCRStatusPayload(status=OCRStatus.ERROR, msg=text, text=text),
            )
            return

        msg = "OCR Complete!"
        
        if ui_mode == "replace":
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
            
            msg += f" Replaced {os.path.basename(original_path)}"
            
            # Emit document intent to automatically reload it in the UI
            self.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=original_path))
            
        elif ui_mode == "save_new":
            msg += f" Saved to {os.path.basename(save_path)}"
            self.pm.add_pdf(save_path)
            self.bus.project_loaded.emit(ProjectEvent.LOADED, ProjectEventPayload())
            self.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=save_path))
            
        self.bus.ocr_status_updated.emit(
            OCRStatus.COMPLETE,
            OCRStatusPayload(status=OCRStatus.COMPLETE, msg=msg, text=text),
        )
