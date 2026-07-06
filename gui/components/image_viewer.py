"""
gui/components/image_viewer.py

Zoomable viewer for standalone image sources (png/jpg/tiff/bmp/webp/...).
Uses a QGraphicsView/QGraphicsPixmapItem canvas — the same interaction model
as PDFViewer's page canvas (wheel-zoom, pan) — since there's no shared base
class to inherit from (composition over inheritance per rules.md 2.7).

OCR word blocks (DocumentBlock.bbox, produced by ImageOcrParser) are drawn
as an invisible clickable layer: clicking a word's bbox creates a highlight
via the same DocumentIntent.CREATE_HIGHLIGHT_FROM_TEXT path reflowable-text
highlights use (locator.bbox populated instead of char_start/char_end).
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from core.events.domains.document_events import DocumentIntent, DocumentPayload, SourceEvent
from core.events.event_bus import EventBus
from core.models.extraction_models import SourceLocation

_BASE_ZOOM = 1.0
_MIN_ZOOM = 0.1
_MAX_ZOOM = 8.0
_ZOOM_STEP = 1.2


class _WordBoxItem(QGraphicsRectItem):
    """Invisible clickable rect over one OCR word/line block."""

    def __init__(self, rect: QRectF, block, viewer: "ImageViewer"):
        super().__init__(rect)
        self.block = block
        self._viewer = viewer
        self.setPen(Qt.PenStyle.NoPen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        self._viewer._on_word_clicked(self.block)
        super().mousePressEvent(event)

    def hoverEnterEvent(self, event):
        self.setBrush(Qt.GlobalColor.yellow)
        self.setOpacity(0.25)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setBrush(Qt.BrushStyle.NoBrush)
        super().hoverLeaveEvent(event)


class ImageViewer(QWidget):
    def __init__(self, app_context=None, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.bus = EventBus.get_instance()
        self.current_source_path = None
        self._blocks = []
        self._zoom = _BASE_ZOOM

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        self._title_label = QLabel("")
        self._title_label.setStyleSheet("font-weight: bold; padding: 4px;")
        toolbar.addWidget(self._title_label, 1)
        layout.addLayout(toolbar)

        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHints(self.view.renderHints())
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        layout.addWidget(self.view)

        self._pixmap_item = None
        self.bus.source_opened.connect(self._on_source_opened)

    def _on_source_opened(self, event, payload):
        if event != SourceEvent.OPENED or payload.source_type != "image":
            return
        self._load(payload.path)

    def _load(self, path: str) -> None:
        self.current_source_path = path
        self._title_label.setText(path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "")
        self.scene.clear()
        self._pixmap_item = None
        self._blocks = []

        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self.scene.addText("Could not load image.")
            return
        self._pixmap_item = QGraphicsPixmapItem(pixmap)
        self.scene.addItem(self._pixmap_item)
        self.scene.setSceneRect(QRectF(pixmap.rect()))

        des = getattr(self._ctx, "document_extraction_service", None)
        self._blocks = des.get_blocks(path) if des and path else []
        for block in self._blocks:
            if not block.bbox or len(block.bbox) < 4:
                continue
            x0, y0, x1, y1 = block.bbox
            rect_item = _WordBoxItem(QRectF(x0, y0, x1 - x0, y1 - y0), block, self)
            self.scene.addItem(rect_item)

        self._zoom = _BASE_ZOOM
        self.view.resetTransform()

    def _on_word_clicked(self, block) -> None:
        path = self.current_source_path
        if not path:
            return
        locator = SourceLocation(doc_id=path, bbox=list(block.bbox) if block.bbox else None)
        self.bus.document_action_requested.emit(
            DocumentIntent.CREATE_HIGHLIGHT_FROM_TEXT,
            DocumentPayload(path=path, text=block.exact_text, locator=locator),
        )

    # ------------------------------------------------------------ zoom
    def zoom_in(self):
        self._zoom = min(self._zoom * _ZOOM_STEP, _MAX_ZOOM)
        self._apply_zoom()

    def zoom_out(self):
        self._zoom = max(self._zoom / _ZOOM_STEP, _MIN_ZOOM)
        self._apply_zoom()

    def zoom_reset(self):
        self._zoom = _BASE_ZOOM
        self._apply_zoom()

    def _apply_zoom(self):
        self.view.resetTransform()
        self.view.scale(self._zoom, self._zoom)

    # ------------------------------------------------------------ jump-to-source
    def jump_to_source(self, doc_name: str, quote: str) -> bool:
        """Fuzzy substring search over the cached OCR blocks — the image
        analog of PDFViewer's fuzzy full-text search — flashing and
        centering the matching word/line's bbox."""
        needle = " ".join((quote or "").lower().split())
        if not needle:
            return False
        for block in self._blocks:
            haystack = " ".join((block.exact_text or "").lower().split())
            if needle in haystack or haystack in needle:
                self._flash_block(block)
                return True
        return False

    def _flash_block(self, block) -> None:
        if not block.bbox or len(block.bbox) < 4 or self._pixmap_item is None:
            return
        x0, y0, x1, y1 = block.bbox
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)
        self.view.centerOn(rect.center())
        flash = QGraphicsRectItem(rect)
        flash.setBrush(Qt.GlobalColor.yellow)
        flash.setOpacity(0.4)
        self.scene.addItem(flash)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(900, lambda: self.scene.removeItem(flash) if flash.scene() else None)
