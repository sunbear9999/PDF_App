"""Stage ledger for IntelligenceScheduler: statuses, run history, jobs, events.

Separated from queueing/dispatch so each stays small. Outcomes are recorded
truthfully: complete, blocked (prerequisite missing), cancelled or failed —
and each run records the producer identity that actually ran.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.models.intelligence_models import StageStatus
from core.models.producer_contract import ProducerBlocked

_FINISHED = {
    StageStatus.COMPLETE.value, StageStatus.FAILED.value,
    StageStatus.BLOCKED.value, StageStatus.CANCELLED.value,
}


class SchedulerAccounting:
    # ----------------------------------------------------- worker outcomes

    def _on_worker_done(self, worker: Any, result: Dict[str, Any]) -> None:
        self._release(worker)
        job = getattr(worker, "job", None)
        if getattr(worker, "generation", self._generation) == self._generation:
            self._finalize(worker, result)
            self._update_backfill(worker.source_id,
                                  error=result.get("error") if result.get("status") == StageStatus.FAILED.value else "")
            self._emit_update(result)
        if self.process_registry and job:
            self.process_registry.complete_job(job.id)

    def _on_worker_error(self, worker: Any, error: str) -> None:
        self._release(worker)
        job = getattr(worker, "job", None)
        result = {"source_id": worker.source_id, "stage": worker.stage,
                  "status": StageStatus.FAILED.value, "error": error,
                  "output_artifact_ids": []}
        if getattr(worker, "generation", self._generation) == self._generation:
            self._finalize(worker, result)
            self._update_backfill(worker.source_id, error=error)
            self._emit_update(result)
        if self.process_registry and job:
            self.process_registry.update_job_status(job.id, f"Failed: {error}")
            self.process_registry.complete_job(job.id)

    def _release(self, worker: Any) -> None:
        self._workers.discard(worker)
        key = (worker.source_id, worker.stage)
        if self._jobs_by_key.get(key) is getattr(worker, "job", None):
            self._jobs_by_key.pop(key, None)

    def _finalize(self, run: Any, result: Dict[str, Any]) -> None:
        status = result.get("status") or StageStatus.FAILED.value
        fingerprint = result.get("fingerprint") or self._stage_fingerprint(run.source_id, run.stage)
        outputs = result.get("output_artifact_ids") or []
        error = result.get("error") or ""
        job = getattr(run, "job", None)
        self._record_stage_status(run.source_id, run.stage, status,
                                  job_id=getattr(job, "id", None), error_text=error,
                                  output_artifact_ids=outputs, **fingerprint)
        self._record_stage_run(run, status, output_artifact_ids=outputs,
                               error_text=error, fingerprint=fingerprint)
        db = getattr(self.project_manager, "db_intelligence", None)
        if db and result.get("identity") and hasattr(db, "record_stage_run_identity"):
            db.record_stage_run_identity(run.run_id, result["identity"])
        self._record_job(job, status=status, error_text=error or None,
                         output_artifact_ids=outputs)

    def _mark_running(self, source_id: str, stage: str, worker: Any) -> None:
        job = getattr(worker, "job", None)
        self._record_job(job, status=StageStatus.RUNNING.value)
        fingerprint = self._stage_fingerprint(source_id, stage)
        self._record_stage_status(source_id, stage, StageStatus.RUNNING.value,
                                  job_id=getattr(job, "id", None), **fingerprint)
        if worker is not None:
            self._record_stage_run(worker, StageStatus.RUNNING.value, fingerprint=fingerprint)

    # ------------------------------------------------------------- ledger

    def _record_job(self, job, **updates) -> None:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db or not job:
            return
        status = updates.get("status") or getattr(job, "status", "")
        db.record_job(job.id, {
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
            "finished_at": _now() if status in _FINISHED else None,
        })

    def _record_stage_status(self, source_id: str, stage: str, status: str, **updates) -> None:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db or not source_id or not stage:
            return
        input_hash = updates.get("input_hash")
        if not input_hash and self.artifact_service:
            source = self._source_by_id(source_id) or {}
            input_hash = self.artifact_service.source_input_hash(source.get("path"))
        db.upsert_stage_status(source_id, stage, {
            "status": status,
            "job_id": updates.get("job_id"),
            "error_text": updates.get("error_text") or None,
            "progress_current": updates.get("progress_current", 0),
            "progress_total": updates.get("progress_total", 0),
            "input_hash": input_hash,
            "settings_hash": updates.get("settings_hash"),
            "extractor_name": updates.get("producer_name"),
            "extractor_version": updates.get("producer_version"),
            "model_role": updates.get("model_role") or None,
            "model_name": updates.get("model_name") or None,
        })

    def _record_stage_run(self, worker: Any, status: str, *,
                          output_artifact_ids: Optional[List[str]] = None,
                          error_text: str = "",
                          fingerprint: Optional[Dict[str, str]] = None) -> None:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db:
            return
        fp = fingerprint or self._stage_fingerprint(worker.source_id, worker.stage)
        now = _now()
        db.record_stage_run(worker.run_id, {
            "source_id": worker.source_id, "stage": worker.stage, "status": status,
            "input_hash": fp.get("input_hash"), "settings_hash": fp.get("settings_hash"),
            "dependency_hash": fp.get("dependency_hash"),
            "producer_name": fp.get("producer_name"),
            "producer_version": fp.get("producer_version"),
            "model_role": fp.get("model_role"), "model_name": fp.get("model_name"),
            "output_artifact_ids": output_artifact_ids or [],
            "error_text": error_text or None,
            "started_at": now if status == StageStatus.RUNNING.value else None,
            "finished_at": now if status in _FINISHED else None,
        })

    def _update_backfill(self, source_id: str, error: str = "") -> None:
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db:
            return
        state = next((item for item in db.list_backfill_state()
                      if item.get("source_id") == source_id), None)
        if not state:
            return
        selected = list(state.get("selected_stages") or [])
        statuses = db.get_stage_statuses(source_id)
        completed = [stage for stage in selected
                     if (statuses.get(stage) or {}).get("status") == StageStatus.COMPLETE.value]
        status = ("failed" if error else
                  "complete" if len(completed) == len(selected) else "running")
        db.set_backfill_state(source_id, selected_stages=selected,
                              completed_stages=completed, status=status, error_text=error)

    # ------------------------------------------------------- dependencies

    def _assert_dependencies_complete(self, source: Dict[str, Any], stage: str) -> None:
        if not self.stage_registry:
            return
        source_id = source.get("id") or source.get("source_id") or ""
        db = getattr(self.project_manager, "db_intelligence", None)
        statuses = db.get_stage_statuses(source_id) if db else {}
        missing = [
            dependency for dependency in self.stage_registry.dependencies_for(
                stage, self._category_for_source(source))
            if (statuses.get(dependency) or {}).get("status") != StageStatus.COMPLETE.value
        ]
        if missing:
            raise ProducerBlocked(f"Waiting for: {', '.join(missing)}")

    def mark_downstream_stale(self, source_id: str, stage: str) -> List[str]:
        source = self._source_by_id(source_id) or {}
        if not self.stage_registry:
            return []
        downstream = self.stage_registry.downstream_of(stage, self._category_for_source(source))
        db = getattr(self.project_manager, "db_intelligence", None)
        if db and downstream:
            db.mark_source_stages_status(source_id, StageStatus.STALE.value, stages=downstream)
            db.mark_artifacts_by_stages_status(source_id, downstream, StageStatus.STALE.value)
        return downstream

    def _transcript_available(self, source_id: str) -> bool:
        pm = self.project_manager
        if not pm or not hasattr(pm, "get_video_transcript"):
            return False
        try:
            return bool((pm.get_video_transcript(source_id) or {}).get("segments"))
        except Exception:
            return False

    def _category_for_source(self, source: Dict[str, Any]) -> str:
        source_type = source.get("source_type") or "unknown"
        registry = getattr(self.policy_service, "source_format_registry", None)
        spec = registry.get(source_type) if registry else None
        if spec:
            return spec.category
        if source_type in {"audio", "video", "image"}:
            return source_type
        return "document"

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
