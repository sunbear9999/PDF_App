from __future__ import annotations

import os

from PySide6.QtWidgets import QStackedWidget

from core.events.domains.document_events import DocumentIntent, DocumentPayload, SourceEvent, SourceIntent, SourcePayload
from core.events.event_bus import EventBus
from gui.components.pdf_viewer import PDFViewer
from gui.components.video_player import VideoPlayer


class SourceViewerHost(QStackedWidget):
    def __init__(self, app_context=None, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.bus = EventBus.get_instance()
        self.pdf_viewer = PDFViewer(app_context=app_context, parent=self)
        self.video_player = VideoPlayer(app_context=app_context, parent=self)
        self.addWidget(self.pdf_viewer)
        self.addWidget(self.video_player)
        self.bus.source_opened.connect(self._on_source_opened)

    @property
    def pdf_path(self):
        return getattr(self.pdf_viewer, "pdf_path", None)

    @property
    def current_source_path(self):
        if self.currentWidget() is self.video_player:
            return getattr(self.video_player, "video_path", None)
        return getattr(self.pdf_viewer, "pdf_path", None)

    @property
    def worker(self):
        return getattr(self.pdf_viewer, "worker", None)

    @property
    def scene(self):
        return getattr(self.pdf_viewer, "scene", None)

    @property
    def doc(self):
        return getattr(self.pdf_viewer, "doc", None)

    @doc.setter
    def doc(self, value):
        self.pdf_viewer.doc = value

    @property
    def annot_manager(self):
        return getattr(self.pdf_viewer, "annot_manager", None)

    def _on_source_opened(self, event, payload):
        if event != SourceEvent.OPENED:
            return
        self.setCurrentWidget(self.video_player if payload.source_type == "video" else self.pdf_viewer)

    def register_shortcuts(self):
        if hasattr(self.video_player, "register_shortcuts"):
            self.video_player.register_shortcuts()

    def zoom_out(self):
        return self.pdf_viewer.zoom_out()

    def zoom_reset(self):
        return self.pdf_viewer.zoom_reset()

    def zoom_in(self):
        return self.pdf_viewer.zoom_in()

    def sharpen_focus(self):
        return self.pdf_viewer.sharpen_focus()

    def rotate_view(self):
        return self.pdf_viewer.rotate_view()

    def swap_document_handle(self, doc):
        return self.pdf_viewer.swap_document_handle(doc)

    def jump_to_annotation(self, page_num, annot_id):
        return self.pdf_viewer.jump_to_annotation(page_num, annot_id)

    def jump_to_page(self, page_num):
        return self.pdf_viewer.jump_to_page(page_num)

    def reload_page(self, page_num):
        return self.pdf_viewer.reload_page(page_num)

    def jump_to_source(self, doc_name, quote):
        pm = getattr(self._ctx, "project_manager", None) if self._ctx else None
        if pm and hasattr(pm, "list_sources"):
            source = next((s for s in pm.list_sources() if doc_name in os.path.basename(s.get("path", ""))), None)
            if source and source.get("source_type") == "video":
                timestamp = self._timestamp_for_video_quote(source.get("id"), quote)
                self.bus.source_action_requested.emit(
                    SourceIntent.OPEN,
                    SourcePayload(
                        path=source.get("path"),
                        source_id=source.get("id"),
                        source_type="video",
                        timestamp=timestamp,
                    ),
                )
                return
        return self.pdf_viewer.jump_to_source(doc_name, quote)

    def jump_to_video(self, source_id=None, path=None, timestamp=0):
        self.bus.source_action_requested.emit(
            SourceIntent.OPEN,
            SourcePayload(
                path=path,
                source_id=source_id,
                source_type="video",
                timestamp=timestamp,
                context={"autoplay": True},
            ),
        )

    def _timestamp_for_video_quote(self, source_id, quote):
        pm = getattr(self._ctx, "project_manager", None) if self._ctx else None
        transcript = pm.get_video_transcript(source_id) if pm and source_id and hasattr(pm, "get_video_transcript") else {}
        needle = " ".join((quote or "").lower().split())
        if not needle:
            return 0
        for segment in transcript.get("segments", []) or []:
            text = " ".join(str(segment.get("text", "")).lower().split())
            if needle in text or text in needle:
                return float(segment.get("start", 0) or 0)
        words = needle.split()
        if len(words) > 6:
            partial = " ".join(words[:6])
            for segment in transcript.get("segments", []) or []:
                if partial in " ".join(str(segment.get("text", "")).lower().split()):
                    return float(segment.get("start", 0) or 0)
        return 0
