from __future__ import annotations

import json
import dataclasses

from PySide6.QtCore import QObject

from core.models.workspace_models import WorkspaceModel
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload, WorkspaceIntent, WorkspacePayload


class WorkspaceStateService(QObject):
    """Manages undo/redo history for workspaces, decoupled from the UI."""

    def __init__(self, event_bus=None, parent=None):
        super().__init__(parent)
        self.bus = event_bus
        self.undo_stack = []
        self.redo_stack = []
        self.is_restoring = False

        if self.bus:
            self.bus.workspace_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent_name: WorkspaceIntent, payload: WorkspacePayload):
        if intent_name == WorkspaceIntent.SAVE_UNDO_STATE:
            self._save_state(payload.get("model"))
        elif intent_name == WorkspaceIntent.UNDO_TRIGGERED:
            self._undo()
        elif intent_name == WorkspaceIntent.REDO_TRIGGERED:
            self._redo()

    def _save_state(self, current_model: WorkspaceModel):
        if self.is_restoring or not current_model:
            return
        state_str = json.dumps(dataclasses.asdict(current_model), sort_keys=True)
        if not self.undo_stack or self.undo_stack[-1][0] != state_str:
            self.undo_stack.append((state_str, current_model))
            if len(self.undo_stack) > 50:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
            self._broadcast_button_states()

    def _undo(self):
        if not self.undo_stack:
            return
        self.is_restoring = True
        _, prev_state = self.undo_stack.pop()
        if self.bus:
            self.bus.workspace_state_restored.emit(WorkspaceEvent.STATE_RESTORED, WorkspaceEventPayload(model=prev_state))
        self.is_restoring = False
        self._broadcast_button_states()

    def _redo(self):
        if not self.redo_stack:
            return
        self.is_restoring = True
        _, next_state = self.redo_stack.pop()
        if self.bus:
            self.bus.workspace_state_restored.emit(WorkspaceEvent.STATE_RESTORED, WorkspaceEventPayload(model=next_state))
        self.is_restoring = False
        self._broadcast_button_states()

    def _broadcast_button_states(self):
        if self.bus:
            self.bus.workspace_action_requested.emit(
                WorkspaceIntent.UPDATE_HISTORY_BUTTONS,
                WorkspacePayload(can_undo=len(self.undo_stack) > 0, can_redo=len(self.redo_stack) > 0),
            )
