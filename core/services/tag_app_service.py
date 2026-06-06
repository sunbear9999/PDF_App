from PySide6.QtCore import QObject
from core.events.event_bus import EventBus
from core.events.domains.metadata_events import TagEvent, TagEventPayload, TagIntent, TagPayload
class TagAppService(QObject):
    def __init__(self, project_manager):
        super().__init__()
        self.pm = project_manager
        self.bus = EventBus.get_instance()
        self.bus.tag_action_requested.connect(self._handle_intent)

    def list_pdf_paths(self) -> list:
        return list(getattr(self.pm, "pdfs", []) or [])

    def _handle_intent(self, intent: TagIntent, payload: TagPayload):
        if intent == TagIntent.FETCH_ALL:
            self.bus.tag_data_updated.emit(
                TagEvent.ALL_TAGS,
                TagEventPayload(tags=self.pm.get_all_tags()),
            )

        elif intent == TagIntent.FETCH_DETAILS:
            docs = self.pm.get_docs_for_tag(payload["tag_id"])
            self.bus.tag_data_updated.emit(TagEvent.TAG_DETAILS, TagEventPayload(docs=docs))
            
        elif intent == TagIntent.FETCH_TARGET_ASSIGNMENTS:
            # Fetches tags assigned to a specific document or node
            tid = payload["target_id"]
            assigned = self.pm.get_tags_for_node(tid) if payload["target_type"] == "node" else self.pm.get_tags_for_doc(tid)
            all_tags = self.pm.get_all_tags()
            self.bus.tag_data_updated.emit(
                TagEvent.TARGET_ASSIGNMENTS,
                TagEventPayload(assigned=assigned, all_tags=all_tags),
            )
            
        elif intent == TagIntent.CREATE:
            self.pm.create_tag(payload["name"], payload["color"])
            self._handle_intent(TagIntent.FETCH_ALL, TagPayload()) # Auto-refresh UI

        elif intent == TagIntent.DELETE:
            self.pm.delete_tag(payload["tag_id"])
            self._handle_intent(TagIntent.FETCH_ALL, TagPayload())
            
        elif intent == TagIntent.MASS_ASSIGN:
            for path in payload.get("assign_docs", []) or []:
                self.pm.assign_tag_to_doc(path, payload["tag_id"])
            for path in payload.get("remove_docs", []) or []:
                self.pm.remove_tag_from_doc(path, payload["tag_id"])
            
        elif intent == TagIntent.UPDATE_ASSIGNMENTS:
            tid = payload["target_id"]
            ttype = payload["target_type"]
            for t_id in payload.get("assign_tags", []) or []:
                self.pm.assign_tag_to_node(tid, t_id) if ttype == "node" else self.pm.assign_tag_to_doc(tid, t_id)
            for t_id in payload.get("remove_tags", []) or []:
                self.pm.remove_tag_from_node(tid, t_id) if ttype == "node" else self.pm.remove_tag_from_doc(tid, t_id)
