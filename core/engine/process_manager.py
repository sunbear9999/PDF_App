# core/engine/process_manager.py
import threading
import uuid
from PySide6.QtCore import QObject, Signal

class LLMJob:
    def __init__(self, job_name, runner=None, job_type="Agent"):
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

    def kill(self):
        self.abort_event.set()
        self.status = "Aborting..."

class ProcessRegistry(QObject):
    job_added = Signal(object)
    job_removed = Signal(str)
    job_updated = Signal(object)
    queue_updated = Signal()

    def __init__(self):
        super().__init__()
        self.active_job = None
        self.pending_queue = []
        self.express_jobs = []  # <-- NEW TRACKER FOR CONCURRENT JOBS
        self.recent_jobs = []

    def enqueue_runner(self, runner, job_name, job_type="Agent", is_express=False):
        """Takes an instantiated MasterActionRunner, assigns it a job, and queues or runs it."""
        job = LLMJob(job_name, runner, job_type)
        job.is_express = is_express
        runner.job = job 
        runner.workflow_request_id = getattr(runner, "workflow_request_id", None) or job.id
        if not getattr(runner, "trace_id", None):
            runner.trace_id = None
        
        if is_express:
            job.status = "Running (Express)..."
            self.express_jobs.append(job)
            self.job_added.emit(job)
            if runner:
                runner.start()
        else:
            self.pending_queue.append(job)
            self.job_added.emit(job)
            self.queue_updated.emit()
            self._process_next()
            
        return job

    def _process_next(self):
        if self.active_job is None and self.pending_queue:
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

    def complete_job(self, job_id):
        if self.active_job and self.active_job.id == job_id:
            self._remember_recent_job(self.active_job)
            self.active_job = None
            self.job_removed.emit(job_id)
            self._process_next()
            return
            
        # Clean up completed express jobs
        for i, job in enumerate(self.express_jobs):
            if job.id == job_id:
                self._remember_recent_job(job)
                self.express_jobs.pop(i)
                self.job_removed.emit(job_id)
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
                self.express_jobs.pop(i)
                self.job_removed.emit(job_id)
                return

        for i, job in enumerate(self.pending_queue):
            if job.id == job_id:
                job.kill()
                self.pending_queue.pop(i)
                self.job_removed.emit(job_id)
                self.queue_updated.emit()
                break

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
        for job in self.express_jobs: job.kill()
        self.pending_queue.clear()
        self.express_jobs.clear()
        self.queue_updated.emit()
