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
        self.abort_event = threading.Event()
        self.runner = runner # Store the MasterActionRunner

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

    def enqueue_runner(self, runner, job_name, job_type="Agent"):
        """Takes an instantiated MasterActionRunner, assigns it a job, and queues it."""
        job = LLMJob(job_name, runner, job_type)
        runner.job = job # Inject the job tracker into the runner
        self.pending_queue.append(job)
        
        self.job_added.emit(job)
        self.queue_updated.emit()
        self._process_next()
        return job

    def _process_next(self):
        """Pulls the next job from the queue and executes its thread."""
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
        else:
            for job in self.pending_queue:
                if job.id == job_id:
                    job.status = new_status
                    self.job_updated.emit(job)
                    break

    def complete_job(self, job_id):
        """Called automatically by MasterActionRunner when it finishes execution."""
        if self.active_job and self.active_job.id == job_id:
            self.active_job = None
            self.job_removed.emit(job_id)
            self._process_next()
        else:
            # Handles if a user canceled a queued job directly
            self.cancel_job(job_id)

    def cancel_job(self, job_id):
        if self.active_job and self.active_job.id == job_id:
            self.active_job.kill() # Sets the flag; the runner thread will cleanly exit itself
        else:
            # Remove silently from queue
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
        if self.active_job:
            self.active_job.kill()
        for job in self.pending_queue:
            job.kill()
        self.pending_queue.clear()
        self.queue_updated.emit()