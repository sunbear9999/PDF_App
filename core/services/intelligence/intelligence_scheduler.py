"""Queue intelligence stages through ProcessRegistry and dispatch to producers.

The scheduler owns *when* and *whether* a stage runs (plans, dependencies,
idempotency, coalescing, cancellation, project lifetime) and the run ledger.
*What* a stage does lives in a registered producer looked up by the stage's
``handler_id`` — built-in and plugin stages run through the same path.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional

from core.models.intelligence_models import ProcessingStage, StageStatus
from core.models.producer_contract import (
    ProducerBlocked, ProducerCancelled, ProducerContext,
)
from core.registries.intelligence_producer_registry import IntelligenceProducerRegistry
from core.services.intelligence.producers.builtin import register_builtin_producers
from core.services.intelligence.scheduler_accounting import SchedulerAccounting
from core.services.intelligence.stage_fingerprint import stage_fingerprint

_MEDIA = {"audio", "video"}
# Working memory a stage may need beyond what is already resident: spaCy and
# the extraction caches for a long book. Light bookkeeping stages need none.
STAGE_MEMORY_BYTES = 512 * 1024 * 1024
_LIGHT_STAGES = {ProcessingStage.SOURCE_REGISTERED.value, ProcessingStage.FULL_TEXT_INDEX.value}
_TRANSCRIPT_WAIT = (
    "Waiting for transcription: this stage runs once a transcript exists."
)



_HANDING_OFF = object()   # a job being created on the Qt thread


def _off_qt_thread() -> bool:
    from PySide6.QtCore import QCoreApplication, QThread
    app = QCoreApplication.instance()
    return app is not None and QThread.currentThread() is not app.thread()


def _make_qt_caller():
    from core.services._shared.qt_tasks import MainThreadCaller
    return MainThreadCaller()     # the scheduler is built on the Qt thread, so calls land there


PROJECT_STAGE_PRIORITY = 1000     # after per-source work (lower numbers run first)
MAX_DEPENDENCY_DEPTH = 16

class IntelligenceScheduler(SchedulerAccounting):
    def __init__(self, *, process_registry=None, project_manager=None,
                 artifact_service=None, policy_service=None, model_role_service=None,
                 document_extraction_service=None, llm_manager=None, bus=None,
                 entity_extraction_pipeline=None, sentence_classifier=None,
                 extractive_summarizer=None, artifact_embedding_service=None,
                 stage_registry=None, date_extraction_service=None,
                 nli_relation_scorer=None, producer_registry=None,
                 source_format_registry=None, data_provider_registry=None,
                 memory_manager=None):
        self.process_registry = process_registry
        self.memory_manager = memory_manager
        self.project_manager = project_manager
        self.artifact_service = artifact_service
        self.policy_service = policy_service
        self.model_role_service = model_role_service
        self.bus = bus
        self.stage_registry = stage_registry
        self.producer_registry = producer_registry or _default_producers(
            project_manager=project_manager, artifact_service=artifact_service,
            document_extraction_service=document_extraction_service,
            date_extraction_service=date_extraction_service,
            entity_extraction_pipeline=entity_extraction_pipeline,
            sentence_classifier=sentence_classifier,
            extractive_summarizer=extractive_summarizer,
            artifact_embedding_service=artifact_embedding_service,
            llm_manager=llm_manager, model_role_service=model_role_service,
            nli_relation_scorer=nli_relation_scorer,
            source_format_registry=source_format_registry,
            data_provider_registry=data_provider_registry,
        )
        self._workers: set[Any] = set()
        self._jobs_by_key: Dict[tuple, Any] = {}
        self._paused_sources: set[str] = set()
        # Bumped when the project closes; work started under an older
        # generation must not write into whatever project is open next.
        self._generation = 0
        self._qt_caller = _make_qt_caller()

    # ------------------------------------------------------------ planning

    def enqueue_source(self, source_id: str, stages: Optional[Iterable[str]] = None,
                       *, force_stages: Optional[Iterable[str]] = None) -> List[Any]:
        source = self._source_by_id(source_id)
        if not source:
            return []
        stages = list(stages or self.policy_service.stages_for_source(source))
        category = self._category_for_source(source)
        if category in _MEDIA and not self._transcript_available(source_id):
            # Transcription runs asynchronously (VideoTranscriptionService) and
            # resumes this plan when it completes. Until then the dependent
            # stages are visibly *blocked*, not silently dropped.
            waiting = [s for s in stages if s != ProcessingStage.SOURCE_REGISTERED.value]
            for stage in waiting:
                # A run row too, so the Runs view shows the wait, not nothing.
                self._finalize(_InlineRun(source_id, stage), _outcome(
                    source_id, stage, StageStatus.BLOCKED.value, _TRANSCRIPT_WAIT))
            stages = [s for s in stages if s == ProcessingStage.SOURCE_REGISTERED.value]
        if self.stage_registry:
            stages = self.stage_registry.expand_dependencies(stages, category)
        forced = set(force_stages or [])
        jobs = []
        for stage in stages:
            job = self.enqueue_stage(source_id, stage, is_background=True,
                                     force=stage in forced)
            if job:
                jobs.append(job)
        return jobs

    def is_stage_current(self, source_id: str, stage: str) -> bool:
        """Public read-only freshness check for composed background workflows."""
        return self._is_stage_current(source_id, stage)

    def has_source_transcript(self, source_id: str) -> bool:
        """Shared readiness check used by source plans and queued processing."""
        return self._transcript_available(source_id)

    def _is_stage_current(self, source_id: str, stage: str, *, compute_hash: bool = True) -> bool:
        """Complete, and every fingerprint component unchanged."""
        db = getattr(self.project_manager, "db_intelligence", None)
        if not db:
            return False
        row = db.get_stage_statuses(source_id).get(stage)
        if not row or row.get("status") != StageStatus.COMPLETE.value:
            return False
        latest = db.latest_stage_run(source_id, stage)
        if not latest or latest.get("status") != StageStatus.COMPLETE.value:
            return False
        if stage == ProcessingStage.CLAIMS.value and latest.get("output_artifact_ids") and not db.list_artifacts(
            source_id=source_id, artifact_type="argument_unit", status=StageStatus.COMPLETE.value, limit=1,
        ):
            return False
        fingerprint = self._stage_fingerprint(source_id, stage, compute_hash=compute_hash)
        return all(
            (latest.get(key) or "") == (fingerprint.get(key) or "")
            for key in ("input_hash", "settings_hash", "dependency_hash",
                        "producer_version", "model_name")
        )

    # ------------------------------------------------------------- queueing

    def enqueue_stage(self, source_id: str, stage: str, *, priority: int = 100,
                      is_background: bool = True, force: bool = False):
        if not self.process_registry or source_id in self._paused_sources:
            return None
        spec = self.stage_registry.get(stage) if self.stage_registry else None
        if self.stage_registry and (not spec or not spec.enabled):
            return None
        existing = self._jobs_by_key.get((source_id, stage))
        if existing is not None:
            # Identical request already queued/running (or on its way to the Qt thread): coalesce.
            return None if existing is _HANDING_OFF else existing
        if not force and self._is_stage_current(source_id, stage):
            return None
        if self._project_wide(stage):
            # Reads every source's results: run after the batch's other work, so it sees the
            # settled project and runs once per source instead of once per finished source.
            priority = max(priority, PROJECT_STAGE_PRIORITY)
        db = getattr(self.project_manager, "db_intelligence", None)
        previous = (db.get_stage_statuses(source_id).get(stage) if db else None) or {}
        if force or previous.get("status") in {
            StageStatus.COMPLETE.value, StageStatus.STALE.value
        }:
            self.mark_downstream_stale(source_id, stage)
        return self._enqueue_worker(source_id, stage, priority, is_background)

    def _project_wide(self, stage: str, depth: int = 0) -> bool:
        spec = self.stage_registry.get(stage) if self.stage_registry else None
        if spec is None or depth > MAX_DEPENDENCY_DEPTH:
            return False
        return bool(spec.project_inputs) or any(self._project_wide(dep, depth + 1) for dep in spec.dependencies)

    def _enqueue_worker(self, source_id: str, stage: str, priority: int, is_background: bool):
        fingerprint = self._stage_fingerprint(source_id, stage)     # slow part: stays on the caller's thread
        if _off_qt_thread():
            # Background planners (adding sources, Run missing) call in here. A worker QThread
            # created on such a thread never delivers its finished signal (that thread has no
            # event loop, and the process queue is not thread-safe), so the stage would sit at
            # "running" forever. The worker is created and queued on the Qt thread instead.
            self._jobs_by_key[(source_id, stage)] = _HANDING_OFF
            generation = self._generation
            self._qt_caller.call(lambda: self._finish_hand_off(source_id, stage, priority, is_background,
                                                               fingerprint, generation))
            return None
        return self._enqueue_on_qt(source_id, stage, priority, is_background, fingerprint)

    def _finish_hand_off(self, source_id, stage, priority, is_background, fingerprint, generation):
        if self._jobs_by_key.get((source_id, stage)) is not _HANDING_OFF or generation != self._generation:
            return None                    # cancelled, or the project closed, while handing off
        self._jobs_by_key.pop((source_id, stage), None)
        return self._enqueue_on_qt(source_id, stage, priority, is_background, fingerprint)

    def _enqueue_on_qt(self, source_id, stage, priority, is_background, fingerprint):
        from core.engine.intelligence_stage_worker import IntelligenceStageWorker

        worker = IntelligenceStageWorker(self, source_id, stage)
        worker.generation = self._generation
        self._workers.add(worker)
        worker.stage_finished.connect(lambda result, w=worker: self._on_worker_done(w, result))
        worker.error.connect(lambda error, w=worker: self._on_worker_error(w, error))
        source = self._source_by_id(source_id) or {}
        job = self.process_registry.enqueue_runner(
            worker, f"Intelligence: {stage}", job_type="Intelligence", is_express=False,
            metadata={
                "source_id": source_id, "source_path": source.get("path"),
                "stage": stage, "priority": priority, "is_background": is_background,
                "can_cancel": True, "can_pause": False, "created_by": "core",
                "input_hash": fingerprint["input_hash"],
                "model_role": fingerprint["model_role"],
                "model_name": fingerprint["model_name"],
            },
        )
        self._jobs_by_key[(source_id, stage)] = job
        self._record_stage_status(source_id, stage, StageStatus.QUEUED.value,
                                  job_id=getattr(job, "id", None), priority=priority,
                                  **fingerprint)
        self._record_stage_run(worker, StageStatus.QUEUED.value, fingerprint=fingerprint)
        self._record_job(job, status=StageStatus.QUEUED.value)
        return job

    # -------------------------------------------------------------- running

    def run_stage_now(self, source_id: str, stage: str, cancel_check=None,
                      worker=None) -> Dict[str, Any]:
        """Run one stage on the calling (background) thread.

        Returns a result whose ``status`` is complete, blocked or cancelled;
        unexpected errors propagate and are recorded as failed.
        """
        cancel_check = cancel_check or (lambda: False)
        if worker is not None and getattr(worker, "generation", self._generation) != self._generation:
            return _outcome(source_id, stage, StageStatus.CANCELLED.value, "Project closed")
        if cancel_check():
            return _outcome(source_id, stage, StageStatus.CANCELLED.value, "Cancelled")
        self._mark_running(source_id, stage, worker)
        if self.memory_manager is not None and stage not in _LIGHT_STAGES:
            # Frees idle models/caches first if the stage would not fit (worker thread).
            self.memory_manager.reserve(STAGE_MEMORY_BYTES, f"intelligence ({stage})")
        source = self._source_by_id(source_id)
        if not source:
            raise RuntimeError(f"Unknown source: {source_id}")
        self._pin_source_revision(source)
        try:
            self._assert_dependencies_complete(source, stage)
            context = self._context(source, stage, cancel_check)
            producer = self._producer_for(stage)
            result = producer.run(context)
        except ProducerBlocked as exc:
            return {**_outcome(source_id, stage, StageStatus.BLOCKED.value, str(exc)),
                    "reason": exc.reason, "details": exc.details,
                    "fingerprint": self._stage_fingerprint(source_id, stage)}
        except ProducerCancelled as exc:
            return _outcome(source_id, stage, StageStatus.CANCELLED.value, str(exc))
        identity = result.identity or producer.identity(context)
        return {
            **_outcome(source_id, stage, StageStatus.COMPLETE.value, ""),
            "output_artifact_ids": [str(item) for item in result.output_ids if item],
            "identity": identity.to_metadata(),
            "diagnostics": dict(result.diagnostics or {}),
            # Computed here, on the worker: it reads every dependency output,
            # ~0.4 s for a long book, which froze the UI when done in _finalize.
            "fingerprint": self._stage_fingerprint(source_id, stage),
        }

    def run_stage_inline(self, source_id: str, stage: str, cancel_check=None) -> Dict[str, Any]:
        """Run and fully account for a stage inside another background runner.

        A workflow step already runs inside MasterActionRunner, so nesting a
        worker thread would break ordering; this keeps the same ledger
        semantics synchronously.
        """
        run = _InlineRun(source_id, stage)
        generation = self._generation
        if cancel_check and cancel_check():
            return _outcome(source_id, stage, StageStatus.CANCELLED.value, "Cancelled")
        self._record_stage_run(run, StageStatus.RUNNING.value,
                               fingerprint=self._stage_fingerprint(source_id, stage))
        try:
            result = self.run_stage_now(source_id, stage, cancel_check=cancel_check)
        except Exception as exc:
            result = _outcome(source_id, stage, StageStatus.FAILED.value, str(exc))
        if generation != self._generation:
            return _outcome(source_id, stage, StageStatus.CANCELLED.value, "Project changed")
        self._finalize(run, result)
        self._emit_update(result)
        return result

    def _pin_source_revision(self, source) -> None:
        """Record the file content this run reads; artifacts carry its id."""
        db = getattr(self.project_manager, "db_intelligence", None)
        if db and self.artifact_service and hasattr(db, "record_source_revision"):
            content_hash = self.artifact_service.source_input_hash(source.get("path"))
            db.record_source_revision(source.get("id") or "", content_hash or "",
                                      source.get("source_type") or "")

    def _context(self, source, stage: str, cancel_check) -> ProducerContext:
        spec = self.stage_registry.get(stage) if self.stage_registry else None
        return ProducerContext(
            source=source, source_id=source.get("id") or source.get("source_id") or "",
            stage=stage, category=self._category_for_source(source),
            producer_version=getattr(spec, "producer_version", "1.0") or "1.0",
            settings=self.policy_service.get_project_settings() if self.policy_service else {},
            cancel_check=cancel_check,
        )

    def _producer_for(self, stage: str):
        spec = self.stage_registry.get(stage) if self.stage_registry else None
        handler = getattr(spec, "handler_id", "") or stage
        producer = self.producer_registry.get(handler) if self.producer_registry else None
        if producer is None:
            raise RuntimeError(f"Intelligence stage '{stage}' has no registered producer '{handler}'.")
        return producer

    def stage_identity(self, source_id: str, stage: str):
        """Identity of the producer that would run ``stage`` now (cheap)."""
        source = self._source_by_id(source_id)
        if not source:
            return None
        try:
            return self._producer_for(stage).identity(self._context(source, stage, lambda: False))
        except Exception:
            return None

    def _stage_fingerprint(self, source_id: str, stage: str, *, compute_hash: bool = True) -> Dict[str, str]:
        source = self._source_by_id(source_id) or {}
        identity = self.stage_identity(source_id, stage) if source else None
        return stage_fingerprint(self, source, stage, identity, compute_hash=compute_hash)

    # ----------------------------------------------------- source lifecycle

    def handle_source_added(self, source_id: str) -> List[Any]:
        if not source_id or not self.policy_service:
            return []
        return self.enqueue_source(source_id)

    def pause_source(self, source_id: str) -> int:
        self._paused_sources.add(source_id)
        return self.cancel_source_jobs(source_id)

    def resume_source(self, source_id: str) -> List[Any]:
        self._paused_sources.discard(source_id)
        return self.enqueue_source(source_id)

    def is_source_paused(self, source_id: str) -> bool:
        return source_id in self._paused_sources

    def cancel_source_jobs(self, source_id: str) -> int:
        return self._cancel_jobs(lambda key: key[0] == source_id)

    def cancel_stage_jobs(self, stage_ids: Iterable[str]) -> int:
        """Cancel queued/running work for stages (e.g. a plugin unloading)."""
        stages = set(stage_ids or [])
        return self._cancel_jobs(lambda key: key[1] in stages)

    def live_stage_keys(self):
        """(source_id, stage) pairs that have a queued or running job in this session."""
        return list(self._jobs_by_key)

    def on_project_closing(self, *_args) -> int:
        """Cancel all work and invalidate in-flight results for this project."""
        self._generation += 1
        self._paused_sources.clear()
        return self._cancel_jobs(lambda _key: True, record=False)

    def _cancel_jobs(self, matches, record: bool = True) -> int:
        registry = self.process_registry
        count = 0
        for key, job in list(self._jobs_by_key.items()):
            if not matches(key):
                continue
            if job is _HANDING_OFF:
                self._jobs_by_key.pop(key, None)       # the hand-off sees it gone and drops the job
                continue
            is_active = registry is not None and getattr(registry, "active_job", None) is job
            if registry is not None:
                registry.cancel_job(job.id)
            count += 1
            if not is_active:
                # A running job reports its own cancellation when it stops.
                self._jobs_by_key.pop(key, None)
                if record:
                    self._record_stage_status(key[0], key[1], StageStatus.CANCELLED.value,
                                              error_text="Cancelled before it started")
        return count


class _InlineRun:
    def __init__(self, source_id: str, stage: str) -> None:
        self.source_id = source_id
        self.stage = stage
        self.run_id = f"stage_run:{uuid.uuid4()}"
        self.job = None


def _outcome(source_id: str, stage: str, status: str, error: str) -> Dict[str, Any]:
    return {"source_id": source_id, "stage": stage, "status": status,
            "error": error, "output_artifact_ids": []}


def _default_producers(**services) -> IntelligenceProducerRegistry:
    registry = IntelligenceProducerRegistry()
    register_builtin_producers(registry, **services)
    return registry
