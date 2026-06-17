from PySide6.QtCore import QObject
from core.events.event_bus import EventBus
from core.events.domains.tool_events import (
    EssayIntent, EssayEvent, EssayPayload, EssayEventPayload,
)


class EssayAppService(QObject):
    """Owns all essay persistence logic; removes direct PM access from EssayTab."""

    def __init__(self, project_manager, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.bus = EventBus.get_instance()
        self.bus.essay_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent: EssayIntent, payload: EssayPayload):
        if isinstance(payload, dict):
            payload = EssayPayload(**payload)
        if intent == EssayIntent.LIST:
            self._list_essays()
        elif intent == EssayIntent.LOAD:
            self._load_essay(payload)
        elif intent == EssayIntent.SAVE:
            self._save_essay(payload)

    def _list_essays(self):
        essays = self.pm.get_all_essays() or []
        self.bus.essay_data_ready.emit(
            EssayEvent.LIST_READY,
            EssayEventPayload(essays=essays),
        )

    def _load_essay(self, payload: EssayPayload):
        data = self.pm.get_essay(payload.essay_id)
        if data:
            self.bus.essay_data_ready.emit(
                EssayEvent.LOADED,
                EssayEventPayload(
                    essay_id=data.get("id", payload.essay_id),
                    title=data.get("title", ""),
                    content=data.get("content", ""),
                ),
            )

    def _save_essay(self, payload: EssayPayload):
        self.pm.upsert_essay(payload.essay_id, payload.title, payload.content)
        self.bus.essay_data_ready.emit(
            EssayEvent.SAVED,
            EssayEventPayload(essay_id=payload.essay_id),
        )
