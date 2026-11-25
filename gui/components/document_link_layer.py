"""Bounded page batches and underlines for canonical reading links."""
from collections import OrderedDict

from PySide6.QtCore import QObject, Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsLineItem

from core.models.memory_models import MemoryConsumer, ConsumerKind
from gui.components.artifact_tasks import ArtifactTaskRunner

MAX_CACHED_PAGES = 12
MAX_REQUEST_PAGES = 8
ENTITY_UNDERLINE = "#8a9a5b"   # entity mentions: a different line from note/citation links


class DocumentLinkLayer(QObject):
    def __init__(self, ctx, viewer):
        super().__init__(viewer)
        self.ctx, self.viewer = ctx, viewer
        self.rows, self.items, self.pending = OrderedDict(), {}, set()
        self._follow = (None, None)   # (mention id, page) to continue Alt+] / Alt+[ from after a jump
        self.path, self.request_path, self.request_pages = "", "", []
        settings = getattr(ctx, "source_viewer_settings_service", None)
        self.enabled = settings.get_settings().smart_links_enabled if settings else True
        self.entities = settings.get_settings().entity_links_enabled if settings else False
        self.estimated_bytes = 0
        self.runner = ArtifactTaskRunner(ctx, self, service_name="document_link_service")
        self.runner.completed.connect(self._completed)
        self.runner.busy_changed.connect(self._busy)
        self.runner.failed.connect(self._failed)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._load)
        viewer.destroyed.connect(self.runner.shutdown)
        bus = getattr(ctx, "bus", None)
        if bus:
            bus.intelligence_state_changed.connect(self._changed)
            bus.gui_action_requested.connect(self._settings_changed)
            bus.project_clearing_started.connect(self.clear)
        memory = getattr(ctx, "memory_manager_service", None)
        if memory:
            identity = f"document_links:{id(viewer)}"
            memory.register_consumer(MemoryConsumer(
                id=identity, label="Reading link previews", kind=ConsumerKind.CACHE.value,
                usage=lambda: self.estimated_bytes, release=self.release, thread="gui", priority=10))
            viewer.destroyed.connect(lambda: memory.unregister_consumer(identity))

    def release(self, _level):
        size = self.estimated_bytes
        self.clear()
        return size

    def clear(self, *_, history=True):
        self.timer.stop()
        self.runner.cancel()
        for page in list(self.items):
            self._remove(page)
        self.rows.clear()
        self.pending.clear()
        self.path, self.estimated_bytes = "", 0
        hover = getattr(self.viewer, "link_hover", None)
        if hover:
            hover.clear(history=history)

    def draw(self, page, item):
        path = getattr(self.viewer, "pdf_path", "")
        if path != self.path:
            self.clear()
            self.path = path
        self._remove(page)
        if not self.enabled or not getattr(self.ctx, "document_link_service", None):
            return
        if page not in self.rows:
            self.pending.add(page)
            self.timer.start()
            return
        self.rows.move_to_end(page)
        pens = {"entity": QPen(QColor(ENTITY_UNDERLINE), 1, Qt.PenStyle.DashLine)}
        default = QPen(QColor("#658bb8"), 1, Qt.PenStyle.DotLine)
        self.items[page] = []
        for hit in self.rows[page]:
            pen = pens.get(hit.get("kind"), default)
            for x0, y0, x1, y1 in hit["rects"]:
                line = QGraphicsLineItem(x0*self.viewer.base_zoom, y1*self.viewer.base_zoom,
                                         x1*self.viewer.base_zoom, y1*self.viewer.base_zoom, item)
                line.setPen(pen)
                line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                line.setZValue(21)
                self.items[page].append(line)

    def hit_at(self, scene_pos):
        if not self.enabled:
            return None
        page, item = self.viewer.annot_manager._get_page_at_pos(scene_pos)
        if page < 0 or item is None:
            return None
        local = item.mapFromScene(scene_pos) / self.viewer.base_zoom
        hits = []
        for hit in self.rows.get(page, []):
            if any(QRectF(x0, y0, x1-x0, y1-y0).contains(local) for x0, y0, x1, y1 in hit["rects"]):
                hits.append(hit)
        if not hits:
            return None
        # Prefer the extraction's precise targets over overlapping native links.
        hits = [h for h in hits if h.get("source_artifact_id")] or hits
        first = hits[0]
        alternatives = [h for h in hits if h.get("source_artifact_id") == first.get("source_artifact_id")]
        return {**first, "alternatives": alternatives} if len(alternatives) > 1 else first

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
        self.runner.start("page_links", source_path=self.path, page_indices=self.request_pages, entities=self.entities)

    def _completed(self, _):
        if self.request_path != getattr(self.viewer, "pdf_path", ""):
            return
        for page in self.request_pages:
            self.rows[page] = []
        for hit in self.runner.result or []:
            self.rows.setdefault(hit["page"], []).append(hit)
        while len(self.rows) > MAX_CACHED_PAGES:
            old, _ = self.rows.popitem(last=False)
            self._remove(old)
        self.estimated_bytes = sum(len(str(h))*2 for hits in self.rows.values() for h in hits)
        if self._follow[0]:
            self._resolve_follow()
        for page in self.request_pages:
            pixmaps = self.viewer.page_pixmaps
            if page in self.rows and page < len(pixmaps) and pixmaps[page] is not None:
                self.draw(page, pixmaps[page])

    def follow_entity(self, mention_id, page):
        """After a jump, the mention arrived at becomes the one Alt+] / Alt+[ continue from."""
        self._follow = (mention_id, page)
        if page in self.rows:
            self._resolve_follow()
        else:
            self.pending.add(page)
            self.timer.start()

    def _resolve_follow(self):
        mention_id, page = self._follow
        hover = getattr(self.viewer, "link_hover", None)
        hit = next((h for h in self.rows.get(page, []) if h.get("source_artifact_id") == mention_id
                    and h.get("kind") == "entity"), None)
        if hit and hover:
            hover.entity_hit = hit
            self._follow = (None, None)

    def _busy(self, busy):
        if not busy and self.pending:
            self.timer.start()

    def _changed(self, *_):
        pages = set(self.rows) | self.pending
        path = self.path
        self.clear(history=False)
        self.path, self.pending = path, pages
        self.timer.start()

    def _failed(self, error):
        bus = getattr(self.ctx, "bus", None)
        if bus:
            bus.status_message_requested.emit(f"Reading links unavailable: {error}", 5000)

    def _settings_changed(self, event, payload):
        from core.events.domains.gui_events import GuiEvent
        if event == GuiEvent.SOURCE_VIEWER_SETTINGS_CHANGED and payload.settings:
            enabled, entities = payload.settings.smart_links_enabled, payload.settings.entity_links_enabled
            if (enabled, entities) != (self.enabled, self.entities):
                self.enabled, self.entities = enabled, entities
                self._changed()
