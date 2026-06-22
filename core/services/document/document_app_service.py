# core/services/document_app_service.py
import os
import uuid
from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
from core.events.domains.document_events import (
    DocumentEvent,
    DocumentEventPayload,
    DocumentIntent,
    DocumentPayload,
    SourceEvent,
    SourceIntent,
    SourcePayload,
)
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload


class ExtractPagesWorker(QThread):
    extraction_finished = Signal(str, int)
    extraction_failed = Signal(str)

    def __init__(self, source_path: str, save_path: str, page_range: str, parent=None):
        super().__init__(parent)
        self.source_path = source_path
        self.save_path = save_path
        self.page_range = page_range

    def run(self):
        try:
            import fitz

            with fitz.open(self.source_path) as src_doc:
                pages = self._parse_page_string(self.page_range, len(src_doc))
                if not pages:
                    self.extraction_failed.emit(
                        f"Could not parse pages. Ensure they are between 1 and {len(src_doc)}."
                    )
                    return

                dest_doc = fitz.open()
                try:
                    for page_num in pages:
                        dest_doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num)
                    dest_doc.save(self.save_path)
                finally:
                    dest_doc.close()

            self.extraction_finished.emit(self.save_path, len(pages))
        except Exception as exc:
            self.extraction_failed.emit(str(exc))

    def _parse_page_string(self, page_str, max_pages):
        pages = set()
        try:
            for part in page_str.split(","):
                part = part.strip()
                if not part:
                    continue

                if "-" in part:
                    start, end = part.split("-", 1)
                    start_idx = int(start.strip()) - 1
                    end_idx = int(end.strip()) - 1
                    if end_idx < start_idx:
                        return None
                    pages.update(range(start_idx, end_idx + 1))
                else:
                    pages.add(int(part.strip()) - 1)

            return sorted(page for page in pages if 0 <= page < max_pages)
        except Exception:
            return None


class DocumentAppService(QObject):
    """Handles PDF file ingestion and OCR checking, completely blind to the UI viewer."""
    def __init__(self, project_manager):
        super().__init__()
        self.pm = project_manager
        self.bus = EventBus.get_instance()
        self.extract_workers = []
        self.bus.document_action_requested.connect(self._handle_intent)
        self.bus.source_action_requested.connect(self._handle_source_intent)

    def _handle_intent(self, intent: DocumentIntent, payload: DocumentPayload):
        if intent == DocumentIntent.ADD_FILES:
            self._add_sources(payload.paths or [])
        elif intent == DocumentIntent.OPEN:
            self._open_source(payload.path, source_id=payload.source_id, timestamp=payload.timestamp, context=payload.context)
        elif intent == DocumentIntent.TRANSCRIBE:
            self.bus.source_action_requested.emit(
                SourceIntent.TRANSCRIBE,
                SourcePayload(path=payload.path, source_id=payload.source_id, source_type=payload.source_type),
            )
        elif intent == DocumentIntent.EXTRACT_PAGES:
            self._extract_pages(payload)
        elif intent == DocumentIntent.CREATE_HIGHLIGHT:
            self._create_highlight(payload)
        elif intent == DocumentIntent.CREATE_HIGHLIGHT_FROM_TEXT:
            self._create_highlight_from_text(payload)
        elif intent == DocumentIntent.UPDATE_HIGHLIGHT_NOTE:
            self._update_highlight_note(payload)
        elif intent == DocumentIntent.UPDATE_HIGHLIGHT_COLOR:
            self._update_highlight_color(payload)
        elif intent == DocumentIntent.DELETE_HIGHLIGHT:
            self._delete_highlight(payload)

    def _handle_source_intent(self, intent: SourceIntent, payload: SourcePayload):
        if intent == SourceIntent.ADD_FILES:
            self._add_sources(payload.paths or [])
        elif intent == SourceIntent.OPEN:
            self._open_source(payload.path, source_id=payload.source_id, timestamp=payload.timestamp, context=payload.context)
        elif intent == SourceIntent.JUMP_TO_LOCATION:
            self._open_source(payload.path, source_id=payload.source_id, timestamp=payload.timestamp, context=payload.context)

    def _add_sources(self, paths: list):
        if not self.pm.project_filepath or not paths:
            return
            
        added = False
        last_source_id = None
        last_path = None
        for path in paths:
            source_type = self.pm.detect_source_type(path) if hasattr(self.pm, "detect_source_type") else "pdf"
            if self.pm.add_source(path, source_type):
                added = True
                source = self.pm.get_source_entity_by_path(path) if hasattr(self.pm, "get_source_entity_by_path") else None
                last_source_id = source.id if source else last_source_id
                last_path = path
                
                payload = DocumentEventPayload(path=path, source_id=last_source_id, source_type=source_type)
                self.bus.source_added.emit(SourceEvent.ADDED, payload)
                self.bus.document_added.emit(
                    DocumentEvent.DOCUMENT_ADDED,
                    payload
                )
            
        if added:
            self._open_source(last_path or paths[-1], source_id=last_source_id)

    def _open_pdf(self, path: str = None, source_id: str = None):
        self._open_source(path, source_id=source_id)

    def _open_source(self, path: str = None, source_id: str = None, timestamp: float = None, context: dict = None):
        if source_id and hasattr(self.pm, "get_source_path"):
            path = self.pm.get_source_path(source_id) or path
        elif path and hasattr(self.pm, "get_source_entity_by_path"):
            source = self.pm.get_source_entity_by_path(path)
            source_id = source.id if source else None

        if not path or not os.path.exists(path): return
        source_type = self.pm.get_source_type(path) if hasattr(self.pm, "get_source_type") else "pdf"
        
        self.pm.set_active_file(path)
        if source_type == "video":
            open_context = dict(context or {})
            open_context["transcript"] = self.pm.get_video_transcript(source_id) if source_id and hasattr(self.pm, "get_video_transcript") else {}
            payload = DocumentEventPayload(
                path=path,
                source_id=source_id,
                source_type="video",
                timestamp=timestamp,
                context=open_context,
            )
            self.bus.source_opened.emit(SourceEvent.OPENED, payload)
            self.bus.source_switched.emit(SourceEvent.SWITCHED, payload)
            self.bus.document_opened.emit(DocumentEvent.DOCUMENT_OPENED, payload)
            return

        doc = self.pm.get_doc(path)
        
        if doc:
            needs_ocr = self._check_needs_ocr(doc)
            # We emit the raw doc object to the bus. The PDFViewer will catch this and render it.
            payload = DocumentEventPayload(path=path, source_id=source_id, source_type="pdf", doc=doc, needs_ocr=needs_ocr)
            self.bus.source_opened.emit(SourceEvent.OPENED, payload)
            self.bus.source_switched.emit(SourceEvent.SWITCHED, payload)
            self.bus.document_opened.emit(DocumentEvent.DOCUMENT_OPENED, payload)
            self.bus.pdf_switched.emit(DocumentEvent.PDF_SWITCHED, payload)

    def _check_needs_ocr(self, doc) -> bool:
        """Determines if the document requires OCR without touching the UI."""
        try:
            pages_to_check = min(3, len(doc))
            total_text = "".join([doc.load_page(i).get_text() for i in range(pages_to_check)])
            return len(total_text.strip()) < 50
        except Exception:
            return False

    def _extract_pages(self, payload: DocumentPayload):
        if not payload.path or not payload.save_path or not payload.page_range:
            self.bus.status_message_requested.emit("Missing extraction details.", 5000)
            return

        worker = ExtractPagesWorker(payload.path, payload.save_path, payload.page_range, self)
        self.extract_workers.append(worker)
        worker.extraction_finished.connect(self._on_extraction_finished)
        worker.extraction_failed.connect(self._on_extraction_failed)
        worker.finished.connect(lambda w=worker: self._release_extract_worker(w))
        self.bus.status_message_requested.emit("Extracting pages...", 3000)
        worker.start()

    def _on_extraction_finished(self, save_path: str, page_count: int):
        if self.pm.add_pdf(save_path):
            source = self.pm.get_source_entity_by_path(save_path) if hasattr(self.pm, "get_source_entity_by_path") else None
            payload = DocumentEventPayload(path=save_path, source_id=source.id if source else None, source_type="pdf")
            self.bus.source_added.emit(SourceEvent.ADDED, payload)
            self.bus.document_added.emit(DocumentEvent.DOCUMENT_ADDED, payload)
        self.bus.status_message_requested.emit(f"Extracted {page_count} pages to {os.path.basename(save_path)}.", 5000)
        self.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=save_path))

    def _on_extraction_failed(self, message: str):
        self.bus.status_message_requested.emit(f"Extraction failed: {message}", 8000)

    def _release_extract_worker(self, worker: ExtractPagesWorker):
        if worker in self.extract_workers:
            self.extract_workers.remove(worker)
        worker.deleteLater()

    def _get_page(self, path: str, page_num: int):
        doc = self.pm.get_doc(path)
        if not doc or page_num is None or page_num < 0 or page_num >= len(doc):
            return None
        return doc.load_page(page_num)

    def _find_annot(self, path: str, page_num: int, annot_id: str):
        page = self._get_page(path, page_num)
        if not page:
            return None, None
        for annot in page.annots() or []:
            if annot.info and annot.info.get("title") == annot_id:
                return page, annot
        return page, None

    def _create_highlight(self, payload: DocumentPayload):
        try:
            import fitz

            page = self._get_page(payload.path, payload.page_num)
            if not page or not payload.rects:
                return

            quads = [fitz.Rect(rect).quad for rect in payload.rects]
            annot = page.add_highlight_annot(quads)
            color = payload.color or (1.0, 0.9, 0.0)
            annot.set_colors(stroke=color)

            annot_id = payload.annot_id or f"UserNote|{uuid.uuid4()}"
            note = payload.note or ""
            subject = payload.text or ""
            annot.set_info(info={"title": annot_id, "content": note, "subject": subject})
            annot.update()

            hex_color = self._rgb_to_hex(color)
            self.bus.highlight_created.emit(
                DocumentEvent.HIGHLIGHT_CREATED,
                DocumentEventPayload(highlight_data={
                    "id": annot_id,
                    "subject": subject,
                    "content": note,
                    "pdf_path": payload.path,
                    "page_num": payload.page_num,
                    "rect_coords": repr(list(annot.rect)),
                    "color": hex_color,
                }),
            )
            self._reload_if_active(payload.path, payload.page_num)
        except Exception as exc:
            self.bus.status_message_requested.emit(f"Could not create highlight: {exc}", 8000)

    def _create_highlight_from_text(self, payload: DocumentPayload):
        path = payload.path or self._path_for_doc_name(payload.doc_name)
        if not path or not payload.text:
            return

        try:
            import fitz

            page_numbers = [payload.page_num] if payload.page_num is not None else range(len(self.pm.get_doc(path)))
            doc = self.pm.get_doc(path)
            for page_num in page_numbers:
                page = doc.load_page(page_num)
                quads = page.search_for(payload.text, quads=True)
                if not quads:
                    chunks = payload.text.split()
                    for idx in range(0, len(chunks), 4):
                        chunk = " ".join(chunks[idx:idx + 6])
                        if chunk.strip():
                            quads.extend(page.search_for(chunk, quads=True))
                if not quads:
                    continue

                annot_id = payload.annot_id or f"AINote|{uuid.uuid4()}"
                color = payload.color or (0.7, 0.4, 1.0)
                annot = page.add_highlight_annot(quads)
                annot.set_colors(stroke=color)
                annot.set_info(info={"title": annot_id, "content": payload.note or "", "subject": payload.text})
                annot.update()
                self.bus.highlight_created.emit(
                    DocumentEvent.HIGHLIGHT_CREATED,
                    DocumentEventPayload(highlight_data={
                        "id": annot_id,
                        "subject": payload.text,
                        "content": payload.note or "",
                        "pdf_path": path,
                        "page_num": page_num,
                        "rect_coords": repr(list(annot.rect)),
                        "color": self._rgb_to_hex(color),
                    }),
                )
                self._reload_if_active(path, page_num)
                return

            self.bus.status_message_requested.emit("Could not locate the exact text bounds.", 5000)
        except Exception as exc:
            self.bus.status_message_requested.emit(f"Could not create highlight: {exc}", 8000)

    def _update_highlight_note(self, payload: DocumentPayload):
        page, annot = self._find_annot(payload.path, payload.page_num, payload.annot_id)
        if not annot:
            return
        info = dict(annot.info)
        info["content"] = payload.note or ""
        annot.set_info(info=info)
        annot.update()
        self.bus.highlight_updated.emit(
            DocumentEvent.HIGHLIGHT_UPDATED,
            DocumentEventPayload(
                annot_id=payload.annot_id,
                changes={"note": payload.note or "", "pdf_path": payload.path, "page_num": payload.page_num},
            ),
        )
        self._reload_if_active(payload.path, payload.page_num)

    def _update_highlight_color(self, payload: DocumentPayload):
        page, annot = self._find_annot(payload.path, payload.page_num, payload.annot_id)
        if not annot:
            return
        annot.set_colors(stroke=payload.color)
        annot.update()
        self.bus.highlight_updated.emit(
            DocumentEvent.HIGHLIGHT_UPDATED,
            DocumentEventPayload(
                annot_id=payload.annot_id,
                changes={"color": self._rgb_to_hex(payload.color), "pdf_path": payload.path, "page_num": payload.page_num},
            ),
        )
        self._reload_if_active(payload.path, payload.page_num)

    def _delete_highlight(self, payload: DocumentPayload):
        page, annot = self._find_annot(payload.path, payload.page_num, payload.annot_id)
        if not page or not annot:
            return
        page.delete_annot(annot)
        self.bus.highlight_deleted.emit(DocumentEvent.HIGHLIGHT_DELETED, DocumentEventPayload(annot_id=payload.annot_id))
        self._reload_if_active(payload.path, payload.page_num)

    def _reload_if_active(self, path: str, page_num: int):
        if path == getattr(self.pm, "active_file", None):
            self.bus.document_action_requested.emit(DocumentIntent.RELOAD_PAGE, DocumentPayload(page_num=page_num))

    def _path_for_doc_name(self, doc_name: str):
        if not doc_name:
            return None
        return next((path for path in self.pm.pdfs if os.path.basename(path) == doc_name), None)

    def _rgb_to_hex(self, color):
        if isinstance(color, str):
            return color
        try:
            return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(channel * 255))) for channel in color[:3]))
        except Exception:
            return "#ffe500"
