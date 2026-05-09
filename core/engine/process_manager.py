import threading
import uuid
from PySide6.QtCore import QObject, Signal

class LLMJob:
    def __init__(self, job_name, job_type="Agent"):
        self.id = str(uuid.uuid4())
        self.name = job_name
        self.type = job_type
        self.status = "Initializing..."
        self.abort_event = threading.Event()
        
    def kill(self):
        self.abort_event.set()
        self.status = "Aborted"

class ProcessRegistry(QObject):
    job_added = Signal(object)
    job_removed = Signal(str)
    job_updated = Signal(object)

    def __init__(self):
        super().__init__()
        self.active_jobs = {}

    def register_job(self, job_name, job_type="Agent"):
        job = LLMJob(job_name, job_type)
        self.active_jobs[job.id] = job
        self.job_added.emit(job)
        return job

    def update_job_status(self, job_id, new_status):
        if job_id in self.active_jobs:
            self.active_jobs[job_id].status = new_status
            self.job_updated.emit(self.active_jobs[job_id])

    def complete_job(self, job_id):
        if job_id in self.active_jobs:
            job = self.active_jobs.pop(job_id)
            self.job_removed.emit(job_id)

    def kill_job(self, job_id):
        if job_id in self.active_jobs:
            self.active_jobs[job_id].kill()
            
    def kill_all(self):
        for job in self.active_jobs.values():
            job.kill()