"""
core/services/help_service.py

Headless service that owns the help subsystem: registries, tutorial engine,
progress store, and content loading.

Instantiated in PapyrusCore; exposed via AppContext and PapyrusAPI.
GUI interaction (opening dialogs, overlays) is handled by HelpGUICoordinator
in the gui/help/ layer — this service has no widget imports.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject

from core.help.help_registry import HelpRegistry
from core.help.tutorial_registry import TutorialRegistry
from core.help.ui_target_registry import UITargetRegistry
from core.help.action_executor import ApprovedActionExecutor
from core.help.tutorial_engine import TutorialEngine
from core.help.progress_store import ProgressStore
from core.help.content_loader import load_builtin_topics, load_builtin_tutorials
from core.events.domains.help_events import HelpIntent, HelpEvent, HelpEventPayload
from core.utils.managed_signal_mixin import _ManagedSignalMixin

if TYPE_CHECKING:
    from core.events.event_bus import EventBus

log = logging.getLogger(__name__)


class HelpService(QObject, _ManagedSignalMixin):
    """
    Coordinates the help registries, tutorial engine, and progress store.

    Plugins register help content via api.help_registry / api.tutorial_registry.
    The GUI coordinator connects to tutorial_engine signals to drive the overlay.
    """

    def __init__(self, event_bus: "EventBus", parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._init_signal_tracking()
        self._bus = event_bus

        # Registries
        self.help_registry = HelpRegistry()
        self.tutorial_registry = TutorialRegistry()
        self.ui_target_registry = UITargetRegistry()
        self.progress_store = ProgressStore()

        # Engine
        self.action_executor = ApprovedActionExecutor(self.ui_target_registry, event_bus)
        self.tutorial_engine = TutorialEngine(
            self.tutorial_registry,
            self.ui_target_registry,
            self.action_executor,
            event_bus,
            parent=self,
        )

        # Relay engine events back to the bus for interested listeners
        self._track_connection(self.tutorial_engine.tutorial_completed, self._on_tutorial_completed)
        self._track_connection(self.tutorial_engine.tutorial_cancelled, self._on_tutorial_cancelled)
        self._track_connection(self.tutorial_engine.tutorial_failed, self._on_tutorial_failed)

        # Handle service-side intents from the bus
        self._track_connection(event_bus.help_action_requested, self._handle_intent)

        # Load built-in content (failures are logged, never raised)
        load_builtin_topics(self.help_registry)
        load_builtin_tutorials(self.tutorial_registry)

    # ------------------------------------------------------------------
    # Intent handling
    # ------------------------------------------------------------------

    def _handle_intent(self, intent, payload) -> None:
        if intent == HelpIntent.START_TUTORIAL:
            tid = getattr(payload, "tutorial_id", "")
            if tid:
                ok = self.tutorial_engine.start_tutorial(tid)
                if not ok:
                    log.warning("HelpService: failed to start tutorial %r", tid)
        elif intent == HelpIntent.STOP_TUTORIAL:
            self.tutorial_engine.cancel()
        elif intent == HelpIntent.RESET_PROGRESS:
            self.progress_store.reset_all()
            log.info("HelpService: tutorial progress reset")
        # GUI-only intents (SHOW_CENTER, SHOW_TOPIC, SHOW_WHATS_THIS, SHOW_F1_HELP,
        # OPEN_DOCK, SELECT_TAB) are handled by HelpGUICoordinator — no-op here.

    # ------------------------------------------------------------------
    # Engine event relay
    # ------------------------------------------------------------------

    def _on_tutorial_completed(self, tutorial_id: str) -> None:
        self.progress_store.mark_completed(tutorial_id)
        self._bus.help_event_occurred.emit(
            HelpEvent.TUTORIAL_COMPLETED,
            HelpEventPayload(tutorial_id=tutorial_id),
        )

    def _on_tutorial_cancelled(self, tutorial_id: str) -> None:
        self._bus.help_event_occurred.emit(
            HelpEvent.TUTORIAL_CANCELLED,
            HelpEventPayload(tutorial_id=tutorial_id),
        )

    def _on_tutorial_failed(self, tutorial_id: str, reason: str) -> None:
        self._bus.help_event_occurred.emit(
            HelpEvent.TUTORIAL_FAILED,
            HelpEventPayload(tutorial_id=tutorial_id, reason=reason),
        )

    def shutdown(self) -> None:
        """Disconnect all tracked signal connections. Called on app close."""
        self._disconnect_all_tracked()
