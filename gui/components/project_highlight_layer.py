"""Worker-fed PDF overlays for project notes; source PDFs remain unchanged."""
from PySide6.QtCore import QObject, Qt, QTimer, QRectF
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem
from collections import OrderedDict
from core.events.domains.intelligence_events import IntelligenceEvent
from gui.components.artifact_tasks import ArtifactTaskRunner

MAX_CACHED_PAGES = 12
MAX_REQUEST_PAGES = 8


class ProjectHighlightLayer(QObject):
    def __init__(self, app_context, viewer):
        super().__init__(viewer)
        self.viewer, self.ctx = viewer, app_context
        self.rows, self.items, self.pending = OrderedDict(), {}, set()
        self.path, self.request_path, self.request_pages = "", "", []
        self.runner = ArtifactTaskRunner(app_context, self, service_name="reference_note_service")
        self.runner.completed.connect(self._completed)
        self.runner.busy_changed.connect(self._busy)
        self.runner.failed.connect(self._failed)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._load)
        viewer.destroyed.connect(self.runner.shutdown)
        bus = getattr(app_context, "bus", None)
        if bus is not None:
            bus.intelligence_state_changed.connect(self._changed)
            bus.project_clearing_started.connect(self.clear)

    def clear(self, *_):
        self.timer.stop()
        self.runner.cancel()
        for page in list(self.items):
            self._remove(page)
        self.rows.clear()
        self.pending.clear()
        self.path = ""

    def draw(self, page, page_item):
        path = getattr(self.viewer, "pdf_path", "")
        if self.path != path:
            self.clear()
            self.path = path
        self._remove(page)
        if page not in self.rows:
            self.pending.add(page)
            self.timer.start()
            return
        self.items[page] = []
        self.rows.move_to_end(page)
        for row in self.rows[page]:
            pixmap = QPixmap()
            pixmap.loadFromData(row['png'], 'PNG')
            item = QGraphicsPixmapItem(pixmap, page_item)
            item.setScale(row['width'] * self.viewer.base_zoom / pixmap.width())
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            item.setZValue(20)
            item.setToolTip('Linked notes saved in this project; inspect individual notes in Notes')
            self.items[page].append(item)
        while len(self.rows) > MAX_CACHED_PAGES:
            oldest, _ = self.rows.popitem(last=False)
            self._remove(oldest)

    def _remove(self, page):
        for item in self.items.pop(page, []):
            try:
                if item.scene():
                    item.scene().removeItem(item)
            except RuntimeError:
                pass

    def _load(self):
        if self.runner.busy or not self.path or not self.pending:
            return
        self.request_path, self.request_pages = self.path, sorted(self.pending)[:MAX_REQUEST_PAGES]
        self.pending.difference_update(self.request_pages)
        self.runner.start('overlay_images', source_path=self.path, page_indices=self.request_pages)

    def _completed(self, _):
        if self.request_path != getattr(self.viewer, "pdf_path", ""):
            return
        for page in self.request_pages:
            self.rows[page] = []
        for row in self.runner.result or []:
            self.rows.setdefault(row["page"], []).append(row)
        for page in self.request_pages:
            pixmaps = getattr(self.viewer, "page_pixmaps", [])
            if page < len(pixmaps) and pixmaps[page] is not None:
                self.draw(page, pixmaps[page])

    def _busy(self, busy):
        if not busy and self.pending:
            self.timer.start()

    def _changed(self, event, _payload):
        if event == IntelligenceEvent.LINKED_NOTE_CHANGED and self.path:
            pages = set(self.rows) | set(self.request_pages) | self.pending
            self.runner.cancel()
            self.rows.clear()
            for page in pages:
                self._remove(page)
                self.pending.add(page)
            self.timer.start()

    def _failed(self, error):
        bus = getattr(self.ctx, "bus", None)
        if bus is not None:
            bus.status_message_requested.emit(f"Project highlights could not load: {error}", 6000)
