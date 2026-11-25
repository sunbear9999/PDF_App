# core/engine/process_manager.py
import threading
import time
import uuid
from PySide6.QtCore import QMetaMethod, QObject, QTimer, Signal

# Shown on a queued job the memory manager is holding back.
WAITING_FOR_MEMORY = "Waiting for memory"
# A held job starts anyway after this, so a machine that stays short of
# memory (another app) never stalls the queue forever.
MAX_MEMORY_HOLD_S = 120

class LLMJob:
    def __init__(self, job_name, runner=None, job_type="Agent", metadata=None):
        metadata = metadata or {}
        self.id = str(uuid.uuid4())
        self.name = job_name
        self.type = job_type
        self.status = "Queued"
        self.is_express = False  # <-- NEW FLAG
        self.abort_event = threading.Event()
        self.runner = runner
        blueprint = getattr(runner, "blueprint", None)
        self.blueprint_id = getattr(runner, "blueprint_id", None) or getattr(blueprint, "_registry_id", None)
        self.blueprint_name = getattr(blueprint, "name", job_name)
        self.target_id = getattr(runner, "target_id", None)
        self.trace_id = getattr(runner, "trace_id", None)
        self.source_id = metadata.get("source_id")
        self.source_path = metadata.get("source_path")
        self.artifact_id = metadata.get("artifact_id")
        self.stage = metadata.get("stage")
        self.priority = metadata.get("priority", 100)
        self.progress_current = metadata.get("progress_current", 0)
        self.progress_total = metadata.get("progress_total", 0)
        self.model_role = metadata.get("model_role")
        self.model_name = metadata.get("model_name")
        self.created_by = metadata.get("created_by", "core")
        self.can_pause = bool(metadata.get("can_pause", False))
        self.can_cancel = bool(metadata.get("can_cancel", True))
        self.is_background = bool(metadata.get("is_background", False))
        self.input_hash = metadata.get("input_hash")
        self.error_text = metadata.get("error_text")
        self.force_admit = False   # "Run anyway" past the memory manager
        self.held_since = 0.0

    def kill(self):
        self.abort_event.set()
        self.status = "Aborting..."

class ProcessRegistry(QObject):
    job_added = Signal(object)
    job_removed = Signal(str)
    job_updated = Signal(object)
    queue_updated = Signal()
    job_progress_updated = Signal(object)
    # Internal relay so completions reported from worker threads are queued
    # onto the registry's own thread before touching the job lists.
    _job_finished_threadsafe = Signal(str)

    def __init__(self):
        super().__init__()
        self._job_finished_threadsafe.connect(self.complete_job)
        self.active_job = None
        self.pending_queue = []
        self.express_jobs = []  # <-- NEW TRACKER FOR CONCURRENT JOBS
        self.recent_jobs = []
        # Memory manager hooks (set by PapyrusCore): may a queued job start
        # now, and how many express jobs may run at once.
        self.admission = None
        self.express_limit = None
        self.express_waiting = []
        # Keep QThread wrappers alive until Qt has emitted finished on the UI
        # thread. Dropping the last wrapper while run() is unwinding is fatal.
        self._retiring_runners = set()

    def enqueue_runner(self, runner, job_name, job_type="Agent", is_express=False, metadata=None):
        """Takes an instantiated MasterActionRunner, assigns it a job, and queues or runs it."""
        job = LLMJob(job_name, runner, job_type, metadata=metadata)
        job.is_express = is_express
        runner.job = job 
        runner.workflow_request_id = getattr(runner, "workflow_request_id", None) or job.id
        if not getattr(runner, "trace_id", None):
            runner.trace_id = None
        
        if is_express:
            if self.express_limit and len(self.express_jobs) >= max(1, int(self.express_limit())):
                job.status = WAITING_FOR_MEMORY
                self.express_waiting.append(job)
                self.job_added.emit(job)
                return job
            self._start_express(job)
        else:
            self.pending_queue.append(job)
            self.pending_queue.sort(key=lambda queued: getattr(queued, "priority", 100))
            self.job_added.emit(job)
            self.queue_updated.emit()
            self._process_next()
            
        return job

    def update_job_progress(self, job_id, current=None, total=None, message=None):
        job = self._find_job(job_id)
        if not job:
            return
        if current is not None:
            job.progress_current = current
        if total is not None:
            job.progress_total = total
        if message:
            job.status = message
            self.job_updated.emit(job)
        self.job_progress_updated.emit(job)

    def _find_job(self, job_id):
        if self.active_job and self.active_job.id == job_id:
            return self.active_job
        for job in self.express_jobs:
            if job.id == job_id:
                return job
        for job in self.pending_queue + self.express_waiting:
            if job.id == job_id:
                return job
        return None

    def _start_express(self, job):
        job.status = "Running (Express)..."
        self.express_jobs.append(job)
        self.job_added.emit(job)
        if job.runner:
            job.runner.start()

    def _start_waiting_express(self):
        while self.express_waiting and (
                not self.express_limit or len(self.express_jobs) < max(1, int(self.express_limit()))):
            job = self.express_waiting.pop(0)
            self.job_removed.emit(job.id)
            self._start_express(job)

    def on_memory_state(self, event, _payload=None):
        """Memory pressure changed: held-back jobs may be able to start now."""
        if getattr(event, "name", "") == "PRESSURE_CHANGED":
            self._process_next()
            self._start_waiting_express()

    def run_anyway(self, job_id):
        """Start a job the memory manager is holding back."""
        for job in self.pending_queue + self.express_waiting:
            if job.id == job_id:
                job.force_admit = True
        if self.express_limit is not None:
            for job in [j for j in self.express_waiting if j.force_admit]:
                self.express_waiting.remove(job)
                self.job_removed.emit(job.id)
                self._start_express(job)
        self._process_next()

    def _admitted(self, job) -> bool:
        held = time.monotonic() - job.held_since if job.held_since else 0.0
        if job.force_admit or self.admission is None or held >= MAX_MEMORY_HOLD_S or self.admission(job):
            return True
        if job.status != WAITING_FOR_MEMORY:
            job.status = WAITING_FOR_MEMORY
            job.held_since = time.monotonic()
            self.job_updated.emit(job)
            QTimer.singleShot(MAX_MEMORY_HOLD_S * 1000 + 100, self._process_next)
        return False

    def _process_next(self):
        if self.active_job is None and self.pending_queue:
            if not self._admitted(self.pending_queue[0]):
                return
            self.active_job = self.pending_queue.pop(0)
            self.active_job.status = "Starting..."
            self.job_updated.emit(self.active_job)
            self.queue_updated.emit()
            
            if self.active_job.runner:
                self.active_job.runner.start()

    def update_job_status(self, job_id, new_status):
        if self.active_job and self.active_job.id == job_id:
            self.active_job.status = new_status
            self.job_updated.emit(self.active_job)
            return
            
        for job in self.express_jobs:
            if job.id == job_id:
                job.status = new_status
                self.job_updated.emit(job)
                return

        for job in self.pending_queue:
            if job.id == job_id:
                job.status = new_status
                self.job_updated.emit(job)
                break

    def _disconnect_runner(self, job):
        """Sever all signal connections on a finished runner to break reference cycles."""
        runner = getattr(job, "runner", None)
        if runner is None:
            return
        if runner.isRunning():
            self._retiring_runners.add(runner)
            runner.finished.connect(self._release_finished_runners)
        for signal_name in (
            "progress_update", "step_started", "step_complete",
            "step_result", "action_complete", "error", "state_snapshot", "user_input_requested",
            "progress", "partial",
        ):
            sig = getattr(runner, signal_name, None)
            if sig is not None and self._signal_has_receivers(runner, signal_name):
                try:
                    sig.disconnect()
                except RuntimeError:
                    pass
        job.runner = None

    @staticmethod
    def _signal_has_receivers(runner, signal_name):
        meta = runner.metaObject()
        for index in range(meta.methodCount()):
            method = meta.method(index)
            if method.methodType() != QMetaMethod.MethodType.Signal:
                continue
            signature = bytes(method.methodSignature()).decode("utf-8")
            if signature.partition("(")[0] != signal_name:
                continue
            if runner.receivers(f"2{signature}") > 0:
                return True
        return False

    def _release_finished_runners(self):
        self._retiring_runners = {
            runner for runner in self._retiring_runners if runner.isRunning()
        }

    def complete_job_threadsafe(self, job_id):
        """Complete a job from any thread.

        Runners that finish on a background thread must use this instead of
        complete_job(); the signal relay queues the call onto the registry's
        thread so the job lists are never mutated concurrently.
        """
        self._job_finished_threadsafe.emit(job_id)

    def complete_job(self, job_id):
        if self.active_job and self.active_job.id == job_id:
            self._disconnect_runner(self.active_job)
            self._remember_recent_job(self.active_job)
            self.active_job = None
            self.job_removed.emit(job_id)
            self._process_next()
            return

        # Clean up completed express jobs
        for i, job in enumerate(self.express_jobs):
            if job.id == job_id:
                self._disconnect_runner(job)
                self._remember_recent_job(job)
                self.express_jobs.pop(i)
                self.job_removed.emit(job_id)
                self._start_waiting_express()
                break

    def _remember_recent_job(self, job):
        if not job:
            return
        trace_id = getattr(job, "trace_id", None) or getattr(getattr(job, "runner", None), "trace_id", None)
        if not trace_id:
            return
        job.trace_id = trace_id
        job.status = "Completed"
        self.recent_jobs.insert(0, job)
        self.recent_jobs = self.recent_jobs[:10]

    def cancel_job(self, job_id):
        if self.active_job and self.active_job.id == job_id:
            self.active_job.kill() 
            return
            
        for i, job in enumerate(self.express_jobs):
            if job.id == job_id:
                job.kill()
                if job.runner and job.runner.isRunning():
                    self._retiring_runners.add(job.runner)
                    job.runner.finished.connect(self._release_finished_runners)
                self.express_jobs.pop(i)
                self.job_removed.emit(job_id)
                return

        for queue in (self.pending_queue, self.express_waiting):
            for i, job in enumerate(queue):
                if job.id == job_id:
                    job.kill()
                    queue.pop(i)
                    self.job_removed.emit(job_id)
                    self.queue_updated.emit()
                    return

    def move_job_up(self, job_id):
        for i, job in enumerate(self.pending_queue):
            if job.id == job_id and i > 0:
                self.pending_queue[i], self.pending_queue[i-1] = self.pending_queue[i-1], self.pending_queue[i]
                self.queue_updated.emit()
                break

    def move_job_down(self, job_id):
        for i, job in enumerate(self.pending_queue):
            if job.id == job_id and i < len(self.pending_queue) - 1:
                self.pending_queue[i], self.pending_queue[i+1] = self.pending_queue[i+1], self.pending_queue[i]
                self.queue_updated.emit()
                break

    def kill_all(self):
        if self.active_job: self.active_job.kill()
        for job in self.pending_queue: job.kill()
        for job in self.express_jobs:
            job.kill()
            if job.runner and job.runner.isRunning():
                self._retiring_runners.add(job.runner)
                job.runner.finished.connect(self._release_finished_runners)
        for job in self.express_waiting: job.kill()
        self.pending_queue.clear()
        self.express_jobs.clear()
        self.express_waiting.clear()
        self.queue_updated.emit()
