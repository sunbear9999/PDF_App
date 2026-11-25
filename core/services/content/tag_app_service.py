from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
from core.events.domains.metadata_events import TagEvent, TagEventPayload, TagIntent, TagPayload
from core.services.content.tag_rules import TagRule, TagRuleEngine


class _TagRuleWorker(QThread):
    completed = Signal(object)

    def __init__(self, project_manager, payload, apply_rule=False, parent=None):
        super().__init__(parent)
        self.pm = project_manager
        self.payload = payload
        self.apply_rule = apply_rule

    def run(self):
        try:
            rule = TagRule(**dict(self.payload.get("rule") or {}))
            lock = getattr(self.pm, "db_lock", None)
            if lock:
                with lock:
                    result = self._execute(rule)
            else:
                result = self._execute(rule)
            self.completed.emit(result)
        except (ValueError, TypeError, KeyError) as exc:
            self.completed.emit({"error": str(exc)})

    def _execute(self, rule):
        sources = self.pm.list_sources()
        assignments = self.pm.get_doc_tag_map()
        tagged = {path for path, tags in assignments.items() if tags}
        paths = TagRuleEngine().matching_paths(sources, rule, tagged)
        changed = 0
        if self.apply_rule:
            changed = self.pm.assign_tag_to_docs(paths, self.payload.get("tag_id"))
        by_path = {source.get("path"): source for source in sources}
        preview = [
            {"path": path, "name": by_path[path].get("title") or path}
            for path in paths[:200] if path in by_path
        ]
        return {"matched_sources": preview, "matched_count": len(paths), "changed_count": changed}


class TagAppService(QObject):
    def __init__(self, project_manager):
        super().__init__()
        self.pm = project_manager
        self.bus = EventBus.get_instance()
        self._workers = set()
        self.bus.tag_action_requested.connect(self._handle_intent)

    def list_pdf_paths(self) -> list:
        if hasattr(self.pm, "list_sources"):
            return [s.get("path") for s in self.pm.list_sources() if s.get("path")]
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
            self._assignments_changed()
            
        elif intent == TagIntent.UPDATE_ASSIGNMENTS:
            tid = payload["target_id"]
            ttype = payload["target_type"]
            for t_id in payload.get("assign_tags", []) or []:
                self.pm.assign_tag_to_node(tid, t_id) if ttype == "node" else self.pm.assign_tag_to_doc(tid, t_id)
            for t_id in payload.get("remove_tags", []) or []:
                self.pm.remove_tag_from_node(tid, t_id) if ttype == "node" else self.pm.remove_tag_from_doc(tid, t_id)
            self._assignments_changed()

        elif intent in {TagIntent.PREVIEW_RULE, TagIntent.APPLY_RULE}:
            self._start_rule_worker(payload, intent == TagIntent.APPLY_RULE)

        elif intent == TagIntent.SET_PARENT:
            self._set_parent(payload)

    def _start_rule_worker(self, payload, apply_rule):
        worker = _TagRuleWorker(self.pm, payload, apply_rule, self)
        self._workers.add(worker)
        worker.completed.connect(
            lambda result, current=worker: self._finish_rule(payload, apply_rule, current, result)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _finish_rule(self, payload, applied, worker, result):
        self._workers.discard(worker)
        event = TagEvent.RULE_APPLIED if applied else TagEvent.RULE_PREVIEW
        self.bus.tag_data_updated.emit(
            event,
            TagEventPayload(request_id=payload.get("request_id"), **result),
        )
        if applied and not result.get("error"):
            self._assignments_changed(result.get("changed_count", 0))

    def _assignments_changed(self, changed_count=0):
        self.bus.tag_data_updated.emit(
            TagEvent.ASSIGNMENTS_CHANGED,
            TagEventPayload(changed_count=changed_count),
        )

    def _set_parent(self, payload):
        error = None
        try:
            self.pm.set_tag_parent(payload["tag_id"], payload.get("parent_tag_id"))
        except ValueError as exc:
            error = str(exc)
        self.bus.tag_data_updated.emit(
            TagEvent.PARENT_UPDATED,
            TagEventPayload(error=error),
        )
        if error is None:
            self._handle_intent(TagIntent.FETCH_ALL, TagPayload())
