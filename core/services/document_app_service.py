# core/services/document_app_service.py
import os
from PySide6.QtCore import QObject
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload
class DocumentAppService(QObject):
    """Handles PDF file ingestion and OCR checking, completely blind to the UI viewer."""
    def __init__(self, project_manager):
        super().__init__()
        self.pm = project_manager
        self.bus = EventBus.get_instance()
        self.bus.document_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent: DocumentIntent, payload: DocumentPayload):
        if intent == DocumentIntent.ADD_FILES:
            self._add_pdfs(payload.paths or [])
        elif intent == DocumentIntent.OPEN:
            self._open_pdf(payload.path)

    def _add_pdfs(self, paths: list):
        if not self.pm.project_filepath or not paths:
            return
            
        added = False
        for path in paths:
            if self.pm.add_pdf(path): added = True
            
        if added:
            self.bus.project_loaded.emit(ProjectEvent.LOADED, ProjectEventPayload())
            self._open_pdf(paths[-1])

    def _open_pdf(self, path: str):
        if not path or not os.path.exists(path): return
        
        self.pm.set_active_file(path)
        doc = self.pm.get_doc(path)
        
        if doc:
            needs_ocr = self._check_needs_ocr(doc)
            # We emit the raw doc object to the bus. The PDFViewer will catch this and render it.
            self.bus.document_opened.emit(
                DocumentEvent.DOCUMENT_OPENED,
                DocumentEventPayload(path=path, doc=doc, needs_ocr=needs_ocr),
            )
            self.bus.pdf_switched.emit(DocumentEvent.PDF_SWITCHED, DocumentEventPayload(path=path))

    def _check_needs_ocr(self, doc) -> bool:
        """Determines if the document requires OCR without touching the UI."""
        try:
            pages_to_check = min(3, len(doc))
            total_text = "".join([doc.load_page(i).get_text() for i in range(pages_to_check)])
            return len(total_text.strip()) < 50
        except Exception:
            return False
