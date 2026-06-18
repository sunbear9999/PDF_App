"""
gui/help/help_gui_coordinator.py

GUI-side coordinator for the help subsystem.

Handles help_action_requested events that require GUI access:
  - Opening the Help Center dialog
  - Creating and managing the tutorial overlay
  - Activating/deactivating What's This? mode
  - Dispatching F1 help
  - Forwarding approved dock/tab actions to DockManager

Instantiated by MainWindow after all services are ready.
Holds no strong widget references beyond the main window itself.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject

from core.events.domains.help_events import HelpEvent, HelpIntent, HelpPayload
from core.utils.managed_signal_mixin import _ManagedSignalMixin
from gui.help.whats_this_handler import WhatsThisHandler
from gui.help.f1_help_handler import handle_f1_help

if TYPE_CHECKING:
    from gui.main_window import MainWindow
    from gui.app_context import AppContext

log = logging.getLogger(__name__)


class HelpGUICoordinator(QObject, _ManagedSignalMixin):
    """
    Bridges help events to GUI actions.

    Created once in MainWindow; owns the WhatsThisHandler and TutorialOverlay
    (overlay is created lazily on the first tutorial start to avoid early
    import issues).
    """

    def __init__(
        self,
        main_window: "MainWindow",
        app_context: "AppContext",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._init_signal_tracking()
        self._mw = main_window
        self._ctx = app_context
        self._overlay = None  # TutorialOverlay — created lazily

        hs = getattr(app_context, "help_service", None)
        if hs is None:
            log.warning("HelpGUICoordinator: help_service not available on AppContext")
            return

        self._whats_this = WhatsThisHandler(
            hs.ui_target_registry,
            hs.help_registry,
            app_context.bus,
            parent=self,
        )

        # Create overlay EAGERLY so it is connected to engine.step_ready before
        # any tutorial starts. HelpService is subscribed to the bus before us, so
        # the engine runs start_tutorial() — and emits step_ready synchronously —
        # before our _handle_intent ever fires. A lazy overlay would always miss
        # that first emission and the tutorial card would never appear.
        self._ensure_overlay()

        self._track_connection(app_context.bus.help_action_requested, self._handle_intent)
        self._track_connection(app_context.bus.help_event_occurred, self._handle_event)
        self._track_connection(app_context.bus.theme_changed, self._on_theme_changed)

    # ------------------------------------------------------------------
    # Intent dispatch
    # ------------------------------------------------------------------

    def _handle_intent(self, intent, payload) -> None:
        hs = getattr(self._ctx, "help_service", None)

        if intent in (HelpIntent.SHOW_CENTER, HelpIntent.SHOW_TOPIC):
            topic_id = getattr(payload, "topic_id", "") if payload else ""
            self._open_help_center(topic_id)

        elif intent == HelpIntent.START_TUTORIAL:
            # Ensure the overlay exists so it can wire up to the engine
            self._ensure_overlay()

        elif intent == HelpIntent.SHOW_WHATS_THIS:
            if self._whats_this.is_active():
                self._whats_this.deactivate()
            else:
                self._whats_this.activate()

        elif intent == HelpIntent.SHOW_F1_HELP and hs:
            handle_f1_help(hs.ui_target_registry, self._ctx.bus)

        elif intent == HelpIntent.OPEN_DOCK:
            dock_id = getattr(payload, "dock_id", "") or (getattr(payload, "data", {}) or {}).get("dock_id", "")
            if dock_id:
                dm = getattr(self._mw, "dock_manager", None)
                if dm:
                    dm.spawn(dock_id)

        elif intent == HelpIntent.SELECT_TAB:
            dock_id = getattr(payload, "dock_id", "") or (getattr(payload, "data", {}) or {}).get("dock_id", "")
            tab_index = getattr(payload, "tab_index", 0)
            if dock_id:
                self._select_tab(dock_id, tab_index)

    def _handle_event(self, event, payload) -> None:
        if event == HelpEvent.TUTORIAL_COMPLETED:
            self._ctx.bus.status_message_requested.emit("Tutorial completed!", 3000)
        elif event == HelpEvent.TUTORIAL_FAILED:
            reason = getattr(payload, "reason", "") if payload else ""
            short = reason[:100] if reason else "A tutorial target could not be found."
            self._ctx.bus.status_message_requested.emit(f"Tutorial unavailable: {short}", 5000)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _open_help_center(self, topic_id: str = "") -> None:
        dm = getattr(self._mw, "dialog_manager", None)
        if dm is None:
            log.warning("HelpGUICoordinator: no dialog_manager on main_window")
            return
        from gui.help.help_center_dialog import HelpCenterDialog
        dlg = dm.show(
            HelpCenterDialog,
            key="help_center",
            singleton=True,
            factory=lambda: HelpCenterDialog(self._ctx, parent=self._mw),
        )
        if topic_id and dlg is not None and hasattr(dlg, "show_topic"):
            dlg.show_topic(topic_id)

    def _ensure_overlay(self) -> None:
        """Create the TutorialOverlay lazily on first tutorial start."""
        if self._overlay is not None:
            return
        hs = getattr(self._ctx, "help_service", None)
        if hs is None:
            return
        from gui.help.tutorial_overlay import TutorialOverlay
        tm = getattr(self._ctx, "theme_manager", None)
        theme = tm.get_theme() if tm else {}
        self._overlay = TutorialOverlay(self._mw, hs.tutorial_engine, theme)
        log.debug("HelpGUICoordinator: TutorialOverlay created")

    def _select_tab(self, dock_id: str, tab_index: int) -> None:
        dm = getattr(self._mw, "dock_manager", None)
        if dm is None:
            return
        for widget in dm.get_inner_widgets(dock_id):
            if hasattr(widget, "setCurrentIndex"):
                widget.setCurrentIndex(tab_index)
                break
            # Also try stacked widget children
            if hasattr(widget, "nav_stack"):
                try:
                    widget.nav_stack.setCurrentIndex(tab_index)
                except Exception:
                    pass
                break

    def _on_theme_changed(self, _intent, theme) -> None:
        if isinstance(theme, dict) and self._overlay is not None:
            self._overlay.apply_theme(theme)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        self._disconnect_all_tracked()
        if self._whats_this and self._whats_this.is_active():
            self._whats_this.deactivate()
