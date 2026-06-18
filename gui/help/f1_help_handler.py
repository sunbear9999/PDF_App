"""
gui/help/f1_help_handler.py

F1 context-sensitive help.

Resolves the currently focused widget to the nearest registered UI target,
then opens the associated help topic. Falls back to the Help Center home
if no topic is found.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from core.help.ui_target_registry import UITargetRegistry
    from core.events.event_bus import EventBus


def handle_f1_help(ui_target_registry: "UITargetRegistry", event_bus: "EventBus") -> None:
    """
    Resolve the focused widget → nearest registered target → help topic.
    Emits SHOW_TOPIC if a topic is found; otherwise SHOW_CENTER.
    """
    from core.events.domains.help_events import HelpIntent, HelpPayload

    focused = QApplication.focusWidget()
    if focused is not None:
        target_id = ui_target_registry.resolve_nearest_ancestor(focused)
        if target_id:
            topic_id = ui_target_registry.get_topic_id(target_id)
            if topic_id:
                event_bus.help_action_requested.emit(
                    HelpIntent.SHOW_TOPIC, HelpPayload(topic_id=topic_id)
                )
                return

    event_bus.help_action_requested.emit(HelpIntent.SHOW_CENTER, HelpPayload())
