"""Debounced previews, exact-position navigation, and reading history."""
from PySide6.QtCore import QObject, QTimer, QPoint
from PySide6.QtGui import QCursor

from gui.components.link_preview_card import LinkPreviewCard
from gui.components.artifact_actions import bind_artifact_action
from gui.components.dialogs.artifact_inspector_dialog import ArtifactInspectorDialog

HOVER_DELAY_MS = 300
LEAVE_GRACE_MS = 250
MAX_HISTORY = 50


class LinkHoverController(QObject):
    def __init__(self, ctx, viewer, layer):
        super().__init__(viewer)
        self.ctx, self.viewer, self.layer = ctx, viewer, layer
        self.card, self.hit, self.history = LinkPreviewCard(viewer), None, []
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(HOVER_DELAY_MS)
        self.timer.timeout.connect(self.preview)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(LEAVE_GRACE_MS)
        self.hide_timer.timeout.connect(self._hide)
        self.card.jump_requested.connect(self.jump)
        self.card.inspect_requested.connect(self.inspect)
        self.entity_hit = None
        for name, label, callback in (("link_back", "Back to reading position", self.back),
                                      ("toggle_smart_links", "Show smart links", self.toggle),
                                      ("open_link_preview", "Preview reading link", self.preview_pointer),
                                      ("next_mention", "Next mention of this entity", self.next_mention),
                                      ("prev_mention", "Previous mention of this entity", self.previous_mention),
                                      ("toggle_entity_links", "Show entity links", self.toggle_entities)):
            bind_artifact_action(ctx, viewer, "viewer."+name, label, callback, "toolbar:pdf_viewer")
        registry = getattr(getattr(ctx, "help_service", None), "ui_target_registry", None)
        if registry:
            identity = f"reading.smart_links.{id(viewer)}"
            registry.register_widget(identity, self.card, "core.reading.smart_links")
            viewer.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def clear(self, *, history=True):
        self.timer.stop()
        self.hide_timer.stop()
        self.card.hide()
        self.hit = None
        if history:
            self.history.clear()

    def move(self, scene_pos):
        hit = self.layer.hit_at(scene_pos)
        if hit:
            self.hide_timer.stop()
            if hit.get("kind") == "entity":
                self.entity_hit = hit
            if hit != self.hit:
                self.hit = hit
                self.card.hide()
                self.timer.start()
        elif not self.card.geometry().contains(QCursor.pos()):
            self.timer.stop()
            if not self.hide_timer.isActive():
                self.hide_timer.start()

    def _hide(self):
        local = self.viewer.viewport().mapFromGlobal(QCursor.pos())
        source_hit = self.layer.hit_at(self.viewer.mapToScene(local)) if self.viewer.viewport().rect().contains(local) else None
        if self.card.isVisible() and (self.card.geometry().contains(QCursor.pos()) or source_hit == self.hit):
            self.hide_timer.start()
        else:
            self.card.hide()
            self.hit = None

    def preview(self):
        if self.hit:
            self.card.show_hit(self.hit, QCursor.pos() + QPoint(12, 16))
            self.hide_timer.start()

    def preview_pointer(self):
        local = self.viewer.viewport().mapFromGlobal(QCursor.pos())
        self.hit = self.layer.hit_at(self.viewer.mapToScene(local))
        self.preview()

    def click(self, scene_pos):
        hit = self.layer.hit_at(scene_pos)
        if not hit:
            return False
        self.hit = hit
        if hit.get("alternatives"):
            self.preview()
        else:
            self.card.hide()
            self.jump()
        return True

    def jump(self):
        if not self.hit:
            return
        self.history.append((self.viewer.pdf_path, self.viewer.horizontalScrollBar().value(),
                             self.viewer.verticalScrollBar().value()))
        self.history = self.history[-MAX_HISTORY:]
        hit = self.card.selected_hit if self.card.isVisible() else self.hit
        self.viewer._jump_to_location(hit["target_page"], hit["target_rects"])
        self.card.hide()

    def next_mention(self):
        self.step_mention(1)

    def previous_mention(self):
        self.step_mention(-1)

    def step_mention(self, direction):
        """Jump to the next (1) or previous (-1) mention of the entity last hovered or visited."""
        hit = self.entity_hit
        page = hit and hit.get("target_page" if direction > 0 else "previous_page")
        if page is None:
            return
        self.history.append((self.viewer.pdf_path, self.viewer.horizontalScrollBar().value(),
                             self.viewer.verticalScrollBar().value()))
        self.history = self.history[-MAX_HISTORY:]
        self.viewer._jump_to_location(page, hit.get("target_rects" if direction > 0 else "previous_rects") or [])
        self.card.hide()
        self.layer.follow_entity(hit.get("next_mention_id" if direction > 0 else "previous_mention_id"), page)

    def toggle_entities(self):
        from dataclasses import replace
        service = getattr(self.ctx, "source_viewer_settings_service", None)
        if service:
            settings = service.get_settings()
            service.set_settings(replace(settings, entity_links_enabled=not settings.entity_links_enabled))

    def back(self):
        if self.history:
            path, x, y = self.history.pop()
            if path == self.viewer.pdf_path:
                self.viewer.horizontalScrollBar().setValue(x)
                self.viewer.verticalScrollBar().setValue(y)

    def toggle(self):
        from dataclasses import replace
        service = getattr(self.ctx, "source_viewer_settings_service", None)
        if service:
            service.set_settings(replace(service.get_settings(), smart_links_enabled=not self.layer.enabled))
            return
        self.layer.enabled = not self.layer.enabled
        self.layer._changed()

    def inspect(self):
        hit = self.card.selected_hit if self.card.isVisible() else self.hit
        if not hit or not hit.get("target_artifact_id"):
            return
        manager = getattr(self.ctx, "dialog_manager", None)
        if manager:
            dialog = manager.show(ArtifactInspectorDialog, key="intelligence_expanded", singleton=True,
                                  factory=lambda: ArtifactInspectorDialog(self.ctx))
            dialog.load_artifact(hit["target_artifact_id"])
