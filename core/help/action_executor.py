"""
core/help/action_executor.py

Executes approved declarative tutorial actions expressed as plain dicts.

Tutorial step before_actions are lists of dicts like:
    {"type": "open_dock", "dock_id": "research"}
    {"type": "focus_widget", "target_id": "toolbar.add_pdf_btn"}
    {"type": "select_tab", "dock_id": "research", "tab_index": 2}
    {"type": "show_status", "message": "Opening analysis dock…", "duration_ms": 2000}

No eval, no arbitrary callables, no arbitrary imports from data.
Only type strings listed in APPROVED_TYPES can be executed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.help.ui_target_registry import UITargetRegistry
    from core.events.event_bus import EventBus

log = logging.getLogger(__name__)


class ApprovedActionExecutor:
    """
    Dispatches tutorial step actions by type ID to fixed handler methods.

    The set of approved types is a frozenset of string constants; any type
    not in APPROVED_TYPES is logged and silently skipped — it never reaches
    eval or dynamic import.
    """

    APPROVED_TYPES: frozenset[str] = frozenset({
        "open_dock",
        "focus_widget",
        "select_tab",
        "scroll_to_target",
        "show_status",
    })

    def __init__(
        self,
        ui_target_registry: "UITargetRegistry",
        event_bus: "EventBus",
    ) -> None:
        self._targets = ui_target_registry
        self._bus = event_bus

    def execute(self, action: dict[str, Any]) -> bool:
        """
        Execute one approved action dict.
        Returns True if handled, False if the type is unknown or the action fails.
        """
        action_type = action.get("type", "")
        if action_type not in self.APPROVED_TYPES:
            log.warning("ActionExecutor: unknown action type %r — skipped", action_type)
            return False
        handler = getattr(self, f"_exec_{action_type}", None)
        if handler is None:
            log.warning("ActionExecutor: no handler for approved type %r", action_type)
            return False
        try:
            return bool(handler(action))
        except Exception:
            log.exception("ActionExecutor: error executing action %r", action)
            return False

    def execute_many(self, actions: list[dict[str, Any]]) -> None:
        for a in actions:
            self.execute(a)

    # ------------------------------------------------------------------
    # Handlers (one per approved type)
    # ------------------------------------------------------------------

    def _exec_open_dock(self, action: dict) -> bool:
        dock_id = action.get("dock_id", "")
        if not dock_id:
            return False
        from core.events.domains.help_events import HelpIntent, HelpPayload
        self._bus.help_action_requested.emit(HelpIntent.OPEN_DOCK, HelpPayload(dock_id=dock_id))
        return True

    def _exec_focus_widget(self, action: dict) -> bool:
        target_id = action.get("target_id", "")
        widget = self._targets.resolve(target_id)
        if widget is not None:
            widget.setFocus()
            return True
        log.debug("ActionExecutor: focus_widget — target %r not found", target_id)
        return False

    def _exec_select_tab(self, action: dict) -> bool:
        dock_id = action.get("dock_id", "")
        tab_index = int(action.get("tab_index", 0))
        if not dock_id:
            return False
        from core.events.domains.help_events import HelpIntent, HelpPayload
        self._bus.help_action_requested.emit(
            HelpIntent.SELECT_TAB,
            HelpPayload(dock_id=dock_id, tab_index=tab_index),
        )
        return True

    def _exec_scroll_to_target(self, action: dict) -> bool:
        target_id = action.get("target_id", "")
        widget = self._targets.resolve(target_id)
        if widget is not None:
            widget.show()
            widget.raise_()
            return True
        log.debug("ActionExecutor: scroll_to_target — target %r not found", target_id)
        return False

    def _exec_show_status(self, action: dict) -> bool:
        msg = action.get("message", "")
        duration = int(action.get("duration_ms", 3000))
        if msg:
            self._bus.status_message_requested.emit(msg, duration)
            return True
        return False
