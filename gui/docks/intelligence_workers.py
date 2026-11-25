"""Background snapshot and revision jobs for the Intelligence dock."""
from __future__ import annotations

import uuid
from contextlib import nullcontext

from PySide6.QtCore import QThread, Signal

DEFAULT_PAGE_SIZE = 200


def _entity_mode(view_id, query):
    """Entities page through canonical names with mention counts; so does Explore when
    only the Entity kind is chosen (that is what makes mention sorts and merging work there)."""
    return view_id == "entities" or (view_id == "artifacts" and list(query.artifact_types) == ["entity"])

class _IntelligenceRefreshWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, app_context, source_id, parent=None, *, query=None,
                 view_id="overview", request_key="", projection=None, facets=()):
        super().__init__(parent)
        self.facets = list(facets or [])
        self.ctx, self.source_id = app_context, source_id
        self.query, self.view_id, self.request_key = query, view_id, request_key
        self.projection=projection
        self._snapshot_id = str(uuid.uuid4())
        self._snapshot = None
        db = getattr(getattr(app_context, "project_manager", None), "db_intelligence", None)
        self._project_identity = (id(db), id(getattr(db, "_conn", None)))

    def run(self):
        try:
            pm = getattr(self.ctx, "project_manager", None)
            db = getattr(pm, "db_intelligence", None)
            with getattr(db, "_write_lock", nullcontext()):
                if self._project_identity != (id(db), id(getattr(db, "_conn", None))):
                    return
                snapshot = self._build_snapshot(pm, db)
            if not self.isInterruptionRequested():
                self._snapshot = snapshot
                self.ready.emit({"snapshot_id": self._snapshot_id})
        except Exception as exc:
            self.failed.emit(str(exc))

    def _build_snapshot(self, pm, db):
        snapshot = {"sources": pm.list_sources() if pm else [], "source_id": self.source_id,
                    "project_identity": self._project_identity, "request_key": self.request_key}
        if db and not self.isInterruptionRequested():
            snapshot.update(self._load_page(db))
            # Project-wide counts and model status only feed Overview; skipping them elsewhere
            # keeps refreshes after each review short (they hold the database lock).
            if self.view_id == "overview" and not snapshot.get("invalidated") and not self.isInterruptionRequested():
                snapshot.update(self._load_status(db))
        return snapshot

    def take_snapshot(self, snapshot_id):
        """Transfer ownership after ready; Qt carries only a small key."""
        if snapshot_id != self._snapshot_id:
            return None
        snapshot, self._snapshot = self._snapshot, None
        return snapshot

    def _load_page(self, db):
        from core.models.intelligence_models import ArtifactQuery, SourceScope, StageRunQuery
        query = self.query or ArtifactQuery(
            source_scope=SourceScope(source_ids=[self.source_id] if self.source_id else []), limit=DEFAULT_PAGE_SIZE,
        )
        entities = []
        service=getattr(self.ctx,'artifact_service',None)
        if self.view_id=='network' and service:
            return service.query_entity_network(query, self.projection, cancelled=self.isInterruptionRequested)
        if self.view_id=='timeline' and service and hasattr(service,'query_timeline'):
            result=service.query_timeline(query,self.projection,cancelled=self.isInterruptionRequested)
            return {**result,'timeline':result.get('items') or []}
        if self.view_id == "runs":
            run_query = StageRunQuery(source_scope=query.source_scope, text=query.text,
                                     cursor=query.cursor, limit=query.limit)
            page = db.query_stage_runs(run_query)
            return {**page, "runs": page["items"], "artifacts": [], "page_count": len(page["items"])}
        if _entity_mode(self.view_id, query):
            page = db.query_entities(query)
            entities = page.get("items") or []
            artifacts = entities
        elif hasattr(db, "query_artifacts"):
            page = db.query_artifacts(query)
            artifacts = page.get("items") or []
        else:
            artifacts = db.list_artifacts(source_id=self.source_id or None, limit=query.limit)
            page = {"next_cursor": ""}
        if page.get("invalidated"):
            return page
        page_count = len(artifacts)
        links = {}
        if self.view_id == "citations":
            ids = [row["artifact_id"] for row in artifacts if row["artifact_type"] == "in_text_citation"]
            links = db.citation_assignments(self.source_id or None, artifact_ids=ids)
            parents = db.list_artifacts_by_ids(list(set(value for value in links.values() if value)))
            artifacts = list({row["artifact_id"]: row for row in [*artifacts, *parents]}.values())
            from core.services.intelligence.entity_citations import citing_entities
            links["__named__"] = citing_entities(db, [row["artifact_id"] for row in artifacts
                                                      if row["artifact_type"] == "citation_entry"])
        snapshot = {"artifacts": artifacts, "entities": entities, "entity_mode": bool(entities),
                    "citation_links": links, "next_cursor": page.get("next_cursor", ""), "page_count": page_count,
                    "facets": self._facets(db, query)}
        graph_service = getattr(self.ctx, "deterministic_graph_analysis_service", None)
        if graph_service and self.view_id == "timeline":
            snapshot["timeline"] = graph_service.project_timeline(artifacts)
        elif graph_service and self.view_id == "graph":
            parent_ids = {row.get("parent_artifact_id") for row in artifacts if row.get("artifact_type") == "entity_mention"}
            parents = db.list_artifacts_by_ids(list(parent_ids - {None}))
            rows = list({row["artifact_id"]: row for row in [*artifacts, *parents]}.values())
            snapshot["graph"] = graph_service.project_graph(rows)
            snapshot["artifacts"] = rows
        return snapshot

    def _facets(self, db, query):
        if not self.facets or not hasattr(db, "facet_counts"):
            return {}
        entities = _entity_mode(self.view_id, query)
        return {field: db.facet_counts(query, field, entities=entities) for field in self.facets
                if not self.isInterruptionRequested() and not (entities and field == "artifact_type")}

    def _load_status(self, db):
        models = getattr(self.ctx, "model_dependency_service", None)
        classifier = getattr(self.ctx, "sentence_classifier_service", None)
        policy = getattr(self.ctx, "processing_policy_service", None)
        enhancer = policy.get_project_settings().get("use_sentence_role_enhancer", True) if policy else True
        return {
            "run_counts": db.stage_run_counts(self.source_id or None) if hasattr(db, "stage_run_counts") else {},
            "models": models.list_dependencies() if models else [],
            "sentence_roles": classifier.status(bool(enhancer)) if classifier else {},
            "counts": db.artifact_counts(self.source_id or None) if hasattr(db, "artifact_counts") else {},
        }


class _ArtifactEditWorker(QThread):
    saved = Signal()
    failed = Signal(str)

    def __init__(self, service, artifact_id, changes, provenance_confirmed,
                 citation_changed, citation_target, parent=None):
        super().__init__(parent)
        self.service = service
        self.artifact_id = artifact_id
        self.changes = changes
        self.provenance_confirmed = provenance_confirmed
        self.citation_changed = citation_changed
        self.citation_target = citation_target

    def run(self):
        try:
            self.service.apply_artifact_edit(
                self.artifact_id, self.changes,
                provenance_confirmed=self.provenance_confirmed,
                citation_changed=self.citation_changed,
                citation_target=self.citation_target,
            )
            self.saved.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
