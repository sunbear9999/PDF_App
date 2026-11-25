"""
gui/components/image_viewer.py

Zoomable viewer for standalone image sources (png/jpg/tiff/bmp/webp/...).
Uses a QGraphicsView/QGraphicsPixmapItem canvas — the same interaction model
as PDFViewer's page canvas (wheel-zoom, pan) — since there's no shared base
class to inherit from (composition over inheritance per rules.md 2.7).

OCR word blocks (DocumentBlock.bbox, produced by ImageOcrParser) are drawn
as an invisible clickable layer: clicking a word's bbox creates a highlight
via the shared verified project-note editor, pinning the OCR block and image.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from core.events.domains.document_events import SourceEvent
from core.events.event_bus import EventBus
from gui.components.selection_source_loader import SelectionSourceLoader

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
        self.snapshot = {}
        self._pending_anchor = None

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
        from gui.components.artifact_tasks import ArtifactTaskRunner
        self.image_runner = ArtifactTaskRunner(app_context, self, service_name='reference_note_service')
        self.image_runner.completed.connect(self._image_ready)
        self.image_runner.failed.connect(self._image_failed)
        self.destroyed.connect(self.image_runner.shutdown)
        self._image_hash = ''
        self.loader = SelectionSourceLoader(app_context, self, self._snapshot_ready,
                                             self._load_failed, self._clear_source)
        self.bus.source_opened.connect(self._on_source_opened)

    def _on_source_opened(self, event, payload):
        if event != SourceEvent.OPENED or payload.source_type != "image":
            return
        self._pending_anchor = (payload.context or {}).get('source_anchor')
        self._load(payload.path)

    def _load(self, path: str) -> None:
        self.current_source_path = path
        self._title_label.setText(path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "")
        self.scene.clear()
        self._pixmap_item = None
        self._blocks = []
        self.snapshot = {}

        self.image_runner.start('decode_image', path=path)

    def _image_ready(self, operation):
        result, self.image_runner.result = self.image_runner.result or {}, None
        self._image_hash = result['source_hash']
        pixmap = QPixmap.fromImage(result['image'])
        self._pixmap_item = QGraphicsPixmapItem(pixmap)
        self._pixmap_item.setScale(result['width'] / pixmap.width())
        self.scene.addItem(self._pixmap_item)
        self.scene.setSceneRect(QRectF(0, 0, result['width'], result['height']))
        block_ids = [span['block_id'] for span in (self._pending_anchor or {}).get('spans') or [] if span.get('block_id')]
        self.loader.load(self.current_source_path, block_ids or None)
        self._zoom = _BASE_ZOOM
        self.view.resetTransform()

    def _snapshot_ready(self, snapshot):
        if snapshot['source_hash'] != self._image_hash:
            self._load_failed('The image changed after it was decoded. Reopen the source.')
            return
        self.snapshot, self._blocks = snapshot, snapshot['blocks']
        for block in self._blocks:
            if block.bbox and len(block.bbox) == 4:
                x0, y0, x1, y1 = block.bbox
                self.scene.addItem(_WordBoxItem(QRectF(x0, y0, x1-x0, y1-y0), block, self))
        if self._pending_anchor:
            anchor, self._pending_anchor = self._pending_anchor, None
            if not self._show_spans(anchor.get('spans') or []):
                self._load_failed('The saved image region no longer matches its OCR block.')

    def _load_failed(self, error):
        self.snapshot, self._blocks, self._pending_anchor = {}, [], None
        self._title_label.setText(error + ' Run source extraction in Intelligence → Processing.')

    def _image_failed(self, error):
        self._clear_source()
        self._title_label.setText('Could not display the image: ' + error)

    def _clear_source(self):
        self.image_runner.cancel()
        self.current_source_path, self.snapshot, self._blocks = None, {}, []
        self._pending_anchor, self._pixmap_item = None, None
        self.scene.clear()

    def _on_word_clicked(self, block) -> None:
        if not self.snapshot or block not in self._blocks:
            return
        from gui.components.dialogs.reference_note_dialog import open_selection_note
        selection = {key: self.snapshot[key] for key in ('source_id', 'source_path', 'source_hash', 'blocks_hash')}
        selection.update(quote=block.exact_text, block_spans=[{'block_id': block.block_id,
                         'char_start': 0, 'char_end': len(block.exact_text)}])
        open_selection_note(self._ctx, selection, parent=self)

    def show_anchor(self, path, anchor):
        if path == self.current_source_path and self.snapshot:
            return self._show_spans(anchor.get('spans') or [])
        self._pending_anchor = anchor
        self._load(path)
        return True

    def _show_spans(self, spans):
        blocks = {block.block_id: block for block in self._blocks}
        matches = []
        for span in spans:
            block = blocks.get(span.get('block_id'))
            if not block or block.exact_text[span.get('char_start', 0):span.get('char_end')] != span.get('quote'):
                return False
            if not block.bbox or list(span.get('rects') or []) != [list(block.bbox)]:
                return False
            matches.append(block)
        for block in matches:
            self._flash_block(block)
        return bool(matches)

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
        import shiboken6
        QTimer.singleShot(900, self, lambda: self.scene.removeItem(flash)
                          if shiboken6.isValid(flash) and flash.scene() else None)
