"""Queue intelligence stages through the existing ProcessRegistry."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from PySide6.QtCore import QThread, Signal

from core.models.intelligence_models import ModelRole, ProcessingStage, StageStatus


class IntelligenceWorker(QThread):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(object)

    def __init__(self, scheduler: "IntelligenceScheduler", source_id: str, stage: str):
        super().__init__()
        self.scheduler = scheduler
        self.source_id = source_id
        self.stage = stage
        self.job = None

    def run(self) -> None:
        try:
            result = self.scheduler.run_stage_now(
                self.source_id,
                self.stage,
                cancel_check=lambda: bool(self.job and self.job.abort_event.is_set()),
            )
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class IntelligenceScheduler:
    def __init__(
        self,
        *,
        process_registry=None,
        project_manager=None,
        artifact_service=None,
        policy_service=None,
        model_role_service=None,
        document_extraction_service=None,
        llm_manager=None,
        bus=None,
    ):
        self.process_registry = process_registry
        self.project_manager = project_manager
        self.artifact_service = artifact_service
        self.policy_service = policy_service
        self.model_role_service = model_role_service
        self.document_extraction_service = document_extraction_service
        self.llm_manager = llm_manager
        self.bus = bus
        self._workers: set[IntelligenceWorker] = set()

    def enqueue_source(self, source_id: str, stages: Optional[Iterable[str]] = None) -> List[Any]:
        source = self._source_by_id(source_id)
        if not source:
            return []
        stages = list(stages or self.policy_service.stages_for_source(source))
        jobs = []
        for stage in stages:
            job = self.enqueue_stage(source_id, stage, is_background=True)
            if job:
                jobs.append(job)
        return jobs

    def enqueue_stage(self, source_id: str, stage: str, *, priority: int = 100, is_background: bool = True):
        if not self.process_registry:
            return None
        worker = IntelligenceWorker(self, source_id, stage)
        self._workers.add(worker)
        worker.finished.connect(lambda result, w=worker: self._on_worker_done(w, result))
        worker.error.connect(lambda error, w=worker: self._on_worker_error(w, error))
        source = self._source_by_id(source_id) or {}
        job = self.process_registry.enqueue_runner(
            worker,
            f"Intelligence: {stage}",
            job_type="Intelligence",
            is_express=False,
            metadata={
                "source_id": source_id,
                "source_path": source.get("path"),
                "stage": stage,
                "priority": priority,
                "is_background": is_background,
                "can_cancel": True,
                "can_pause": False,
                "created_by": "core",
            },
        )
        self._record_stage_status(
            source_id,
            stage,
            StageStatus.QUEUED.value,
            job_id=getattr(job, "id", None),
            priority=priority,
        )
        self._record_job(job, status=StageStatus.QUEUED.value)
        return job

    def run_stage_now(self, source_id: str, stage: str, cancel_check=None) -> Dict[str, Any]:
        if cancel_check and cancel_check():
            return {"source_id": source_id, "stage": stage, "status": StageStatus.FAILED.value, "error": "Cancelled"}
        self._record_job(getattr(QThread.currentThread(), "job", None), status=StageStatus.RUNNING.value)
        self._record_stage_status(
            source_id,
            stage,
            StageStatus.RUNNING.value,
            job_id=getattr(getattr(QThread.currentThread(), "job", None), "id", None),
        )
        source = self._source_by_id(source_id)
        if not source:
            raise RuntimeError(f"Unknown source: {source_id}")
        output_ids: List[str] = []
        if stage == ProcessingStage.SOURCE_REGISTERED.value:
            artifact_id = self.artifact_service.ensure_source_artifact(source)
            output_ids = [artifact_id] if artifact_id else []
        elif stage in {
            ProcessingStage.PARSED_BLOCKS.value,
            ProcessingStage.SECTIONS.value,
            ProcessingStage.ENTITIES.value,
            ProcessingStage.DATES.value,
            ProcessingStage.CITATIONS.value,
            ProcessingStage.TABLES.value,
        }:
            output_ids = self._run_extraction_stage(source_id, stage)
        elif stage == ProcessingStage.TRANSCRIPT.value:
            output_ids = self.artifact_service.sync_transcript_artifacts(source_id)
        elif stage == ProcessingStage.EMBEDDINGS.value:
            self._run_embedding_stage(source)
            output_ids = self.artifact_service.get_artifacts(source_id, {"limit": 1000})
        else:
            output_ids = []
        return {
            "source_id": source_id,
            "stage": stage,
            "status": StageStatus.COMPLETE.value,
            "output_artifact_ids": [
                item["artifact_id"] if isinstance(item, dict) else item
                for item in output_ids
                if item
            ],
        }

    def handle_source_added(self, source_id: str) -> List[Any]:
        if not source_id or not self.policy_service:
            return []
        return self.enqueue_source(source_id)

    def cancel_source_jobs(self, source_id: str) -> int:
        registry = self.process_registry
        if not registry:
            return 0
        count = 0
        jobs = list(getattr(registry, "pending_queue", []))
        jobs += list(getattr(registry, "express_jobs", []))
        if getattr(registry, "active_job", None):
            jobs.append(registry.active_job)
        for job in jobs:
            if getattr(job, "source_id", None) == source_id:
                registry.cancel_job(job.id)
                count += 1
        return count

    def _run_extraction_stage(self, source_id: str, stage: str) -> List[str]:
        des = self.document_extraction_service
        if des:
            if stage in {ProcessingStage.PARSED_BLOCKS.value, ProcessingStage.SECTIONS.value}:
                des.get_blocks(source_id)
                if stage == ProcessingStage.SECTIONS.value:
                    des.get_sections(source_id)
            elif stage == ProcessingStage.ENTITIES.value:
                des.get_entities(source_id)
            elif stage == ProcessingStage.DATES.value:
                des.get_dates(source_id)
            elif stage == ProcessingStage.CITATIONS.value:
                des.get_citation_index(source_id)
            elif stage == ProcessingStage.TABLES.value:
                des.get_tables(source_id)
        return self.artifact_service.sync_extraction_artifacts(source_id, stages=[stage])

    def _run_embedding_stage(self, source: Dict[str, Any]) -> None:
        if not self.llm_manager or not getattr(self.llm_manager, "ai_enabled", False):
            return
        path = source.get("path")
        if not path:
            return
        # Existing vector index path already batches embeddings and enriches metadata
        # from DocumentExtractionService when available.
        self.llm_manager.index_documents([path])

    def _on_worker_done(self, worker: IntelligenceWorker, result: Dict[str, Any]) -> None:
        self._workers.discard(worker)
        job = getattr(worker, "job", None)
        self._record_stage_status(
            worker.source_id,
            worker.stage,
            StageStatus.COMPLETE.value,
            job_id=getattr(job, "id", None),
            output_artifact_ids=result.get("output_artifact_ids"),
        )
        self._record_job(job, status=StageStatus.COMPLETE.value, output_artifact_ids=result.get("output_artifact_ids"))
        if self.process_registry and job:
            self.process_registry.complete_job(job.id)
        self._emit_update(result)

    def _on_worker_error(self, worker: IntelligenceWorker, error: str) -> None:
        self._workers.discard(worker)
        job = getattr(worker, "job", None)
        self._record_stage_status(
            worker.source_id,
            worker.stage,
            StageStatus.FAILED.value,
            job_id=getattr(job, "id", None),
            error_text=error,
        )
        self._record_job(job, status=StageStatus.FAILED.value, error_text=error)
        if self.process_registry and job:
            self.process_registry.update_job_status(job.id, f"Failed: {error}")
            self.process_registry.complete_job(job.id)
        self._emit_update({
            "source_id": worker.source_id,
            "stage": worker.stage,
            "status": StageStatus.FAILED.value,
            "error": error,
        })

    def _record_job(self, job, **updates) -> None:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db or not job:
            return
        status = updates.get("status") or getattr(job, "status", "")
        values = {
            "source_id": _primitive(getattr(job, "source_id", None)),
            "artifact_id": _primitive(getattr(job, "artifact_id", None)),
            "job_type": _primitive(getattr(job, "type", "Intelligence")) or "Intelligence",
            "stage": _primitive(getattr(job, "stage", None)),
            "priority": _primitive(getattr(job, "priority", 100)) or 100,
            "status": status,
            "progress_current": _primitive(getattr(job, "progress_current", 0)) or 0,
            "progress_total": _primitive(getattr(job, "progress_total", 0)) or 0,
            "model_role": _primitive(getattr(job, "model_role", None)),
            "model_name": _primitive(getattr(job, "model_name", None)),
            "input_hash": _primitive(getattr(job, "input_hash", None)),
            "output_artifact_ids": updates.get("output_artifact_ids") or [],
            "error_text": updates.get("error_text"),
            "started_at": _now() if status == StageStatus.RUNNING.value else None,
            "finished_at": _now() if status in {StageStatus.COMPLETE.value, StageStatus.FAILED.value} else None,
        }
        db.record_job(job.id, values)

    def _record_stage_status(self, source_id: str, stage: str, status: str, **updates) -> None:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db or not source_id or not stage:
            return
        source = self._source_by_id(source_id) or {}
        model_role = updates.get("model_role")
        model_name = updates.get("model_name")
        if stage == ProcessingStage.EMBEDDINGS.value:
            model_role = model_role or ModelRole.EMBEDDING.value
        if model_role and not model_name and self.model_role_service:
            try:
                model_name = self.model_role_service.resolve_role(model_role)
            except Exception:
                model_name = None
        input_hash = updates.get("input_hash")
        if not input_hash and self.artifact_service:
            input_hash = self.artifact_service.source_input_hash(source.get("path"))
        db.upsert_stage_status(source_id, stage, {
            "status": status,
            "job_id": updates.get("job_id"),
            "error_text": updates.get("error_text"),
            "progress_current": updates.get("progress_current", 0),
            "progress_total": updates.get("progress_total", 0),
            "input_hash": input_hash,
            "model_role": model_role,
            "model_name": model_name,
        })

    def _emit_update(self, payload: Dict[str, Any]) -> None:
        if self.bus and hasattr(self.bus, "intelligence_state_changed"):
            try:
                self.bus.intelligence_state_changed.emit("source_intelligence_updated", payload)
            except Exception:
                pass

    def _source_by_id(self, source_id: str) -> Optional[Dict[str, Any]]:
        if not self.project_manager:
            return None
        for source in self.project_manager.list_sources(include_removed=True):
            if source.get("id") == source_id or source.get("path") == source_id:
                return source
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _primitive(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None
