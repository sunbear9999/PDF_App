"""
gui/help/whats_this_handler.py

What's This? mode — an application-level event filter that changes the cursor
and intercepts the next meaningful click to resolve a help topic.

Activated/deactivated via the Help menu or Shift+F1.
Cleans itself up automatically after one click, or on Escape.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from core.help.ui_target_registry import UITargetRegistry
    from core.help.help_registry import HelpRegistry
    from core.events.event_bus import EventBus

log = logging.getLogger(__name__)


class WhatsThisHandler(QObject):
    """
    App-level event filter for What's This? mode.

    On activation: installs itself as a global event filter and sets the cursor
    to Qt.WhatsThisCursor.

    On the next mouse click: resolves the clicked widget's nearest registered
    ancestor in UITargetRegistry, emits SHOW_TOPIC (or SHOW_CENTER as fallback),
    then deactivates.

    On Escape: deactivates without showing any topic.
    """

    def __init__(
        self,
        ui_target_registry: "UITargetRegistry",
        help_registry: "HelpRegistry",
        event_bus: "EventBus",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._targets = ui_target_registry
        self._help_reg = help_registry
        self._bus = event_bus
        self._active = False

    def activate(self) -> None:
        if self._active:
            return
        self._active = True
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WhatsThisCursor))
        log.debug("WhatsThisHandler: activated")

    def deactivate(self) -> None:
        if not self._active:
            return
        self._active = False
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
            QApplication.restoreOverrideCursor()
        log.debug("WhatsThisHandler: deactivated")

    def is_active(self) -> bool:
        return self._active

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not self._active:
            return False

        if event.type() == QEvent.Type.MouseButtonPress:
            from PySide6.QtWidgets import QWidget
            if isinstance(watched, QWidget):
                self._resolve_and_show(watched)
            self.deactivate()
            return True   # consume the click — does not activate the target's normal action

        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.deactivate()
                return True

        return False

    def _resolve_and_show(self, widget) -> None:
        from core.events.domains.help_events import HelpIntent, HelpPayload
        target_id = self._targets.resolve_nearest_ancestor(widget)
        topic_id: Optional[str] = None
        if target_id:
            topic_id = self._targets.get_topic_id(target_id)
        if topic_id:
            self._bus.help_action_requested.emit(
                HelpIntent.SHOW_TOPIC, HelpPayload(topic_id=topic_id)
            )
        else:
            self._bus.help_action_requested.emit(HelpIntent.SHOW_CENTER, HelpPayload())
