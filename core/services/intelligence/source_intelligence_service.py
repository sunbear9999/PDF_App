"""Public facade for source-level intelligence controls."""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QObject

from core.models.intelligence_models import ProcessingStage, StageStatus


class SourceIntelligenceService(QObject):
    def __init__(
        self,
        *,
        project_manager=None,
        artifact_service=None,
        scheduler=None,
        policy_service=None,
        hybrid_retrieval_service=None,
        bus=None,
        parent=None,
    ):
        super().__init__(parent)
        self.project_manager = project_manager
        self.artifact_service = artifact_service
        self.scheduler = scheduler
        self.policy_service = policy_service
        self.hybrid_retrieval_service = hybrid_retrieval_service
        self.bus = bus
        if self.bus:
            self.bus.source_added.connect(self._on_source_added)
            self.bus.highlight_created.connect(self._on_highlight_changed)
            self.bus.highlight_updated.connect(self._on_highlight_changed)
            self.bus.video_status_updated.connect(self._on_video_status)

    def get_source_status(self, source_id: str) -> Dict[str, Any]:
        source = self._source_by_id(source_id)
        if not source:
            return {"source_id": source_id, "stages": {}}
        canonical_source_id = source.get("id") or source_id
        artifacts = self.artifact_service.get_artifacts(canonical_source_id, {"limit": 2000})
        db = getattr(self.project_manager, "db_intelligence", None)
        stage_rows = db.get_stage_statuses(canonical_source_id) if db else {}
        stages = {stage.value: StageStatus.NOT_RUN.value for stage in ProcessingStage}
        for artifact in artifacts:
            stage = (artifact.get("metadata") or {}).get("stage")
            if stage and stage in stages:
                status = artifact.get("status") or StageStatus.COMPLETE.value
                if stages[stage] != StageStatus.STALE.value:
                    stages[stage] = status
        for stage, row in stage_rows.items():
            if stage in stages:
                stages[stage] = row.get("status") or stages[stage]
        for stage in self.policy_service.stages_for_source(source) if self.policy_service else []:
            stages.setdefault(stage, StageStatus.NOT_RUN.value)
        return {
            "source_id": canonical_source_id,
            "source": source,
            "processing_mode": (
                self.policy_service.processing_mode_for_source(source)
                if self.policy_service else ""
            ),
            "stages": stages,
            "stage_details": stage_rows,
            "artifact_count": len(artifacts),
        }

    def run_stage(self, source_id: str, stage: str):
        return self.scheduler.enqueue_stage(source_id, stage) if self.scheduler else None

    def run_missing(self, source_id: str):
        return self.scheduler.enqueue_source(source_id) if self.scheduler else []

    def rerun_stale(self, source_id: str):
        status = self.get_source_status(source_id)
        stale = [stage for stage, value in status.get("stages", {}).items() if value == StageStatus.STALE.value]
        return [self.run_stage(source_id, stage) for stage in stale]

    def delete_derived(self, source_id: str, stage: Optional[str] = None) -> int:
        return self.artifact_service.delete_derived(source_id, stage=stage) if self.artifact_service else 0

    def set_source_processing_mode(self, source_id: str, mode: str) -> None:
        if self.policy_service:
            self.policy_service.set_source_processing_mode(source_id, mode)

    def get_artifacts(self, source_id: Optional[str] = None, filters: Optional[Dict[str, Any]] = None):
        return self.artifact_service.get_artifacts(source_id, filters) if self.artifact_service else []

    def get_related_artifacts(self, artifact_id: str):
        return self.artifact_service.get_related_artifacts(artifact_id) if self.artifact_service else {}

    def build_retrieval_plan(self, query: str, **kwargs):
        retrieval = getattr(self.scheduler, "hybrid_retrieval_service", None) if self.scheduler else None
        retrieval = retrieval or getattr(self, "hybrid_retrieval_service", None)
        if retrieval and hasattr(retrieval, "build_plan"):
            return retrieval.build_plan(query, **kwargs)
        from core.models.intelligence_models import RetrievalPlan
        return RetrievalPlan(query=query, **kwargs)

    def retrieve(self, plan):
        retrieval = getattr(self.scheduler, "hybrid_retrieval_service", None) if self.scheduler else None
        retrieval = retrieval or getattr(self, "hybrid_retrieval_service", None)
        return retrieval.retrieve(plan) if retrieval else []

    def cancel_source_jobs(self, source_id: str) -> int:
        return self.scheduler.cancel_source_jobs(source_id) if self.scheduler else 0

    def _on_source_added(self, event, payload) -> None:
        source_id = getattr(payload, "source_id", None)
        path = getattr(payload, "path", None)
        if not source_id and path and self.project_manager:
            source = self.project_manager.get_source_record_by_path(path)
            source_id = source.get("id") if source else None
        if not source_id:
            return
        source = self._source_by_id(source_id)
        if source:
            self.artifact_service.ensure_source_artifact(source)
            try:
                self.artifact_service.mark_source_stale(source_id)
            except Exception:
                pass
        if self.scheduler:
            self.scheduler.handle_source_added(source_id)

    def _on_highlight_changed(self, event, payload) -> None:
        highlight_id = getattr(payload, "annot_id", None)
        highlight_data = getattr(payload, "highlight_data", None) or {}
        highlight_id = highlight_id or highlight_data.get("id")
        if highlight_id and self.artifact_service:
            self.artifact_service.sync_annotation_artifact(highlight_id)

    def _on_video_status(self, event, payload) -> None:
        if str(getattr(event, "name", event)) != "TRANSCRIPTION_COMPLETED":
            return
        source_id = getattr(payload, "source_id", None)
        if source_id and self.artifact_service:
            self.artifact_service.sync_transcript_artifacts(source_id)

    def _source_by_id(self, source_id: str) -> Optional[Dict[str, Any]]:
        if not self.project_manager:
            return None
        for source in self.project_manager.list_sources(include_removed=True):
            if source.get("id") == source_id or source.get("path") == source_id:
                return source
        return None
