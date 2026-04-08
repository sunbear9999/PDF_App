# core/render_pool.py
import threading
from collections import deque
from typing import Deque, Optional
from PyQt6.QtCore import QThread

class ObjectPool:
    """Generic object pool for reusable objects."""
    
    def __init__(self, factory, max_size: int = 10):
        self.factory = factory
        self.max_size = max_size
        self.pool: Deque = deque()
        self.lock = threading.Lock()
        self.created_count = 0
    
    def acquire(self):
        """Get an object from the pool, creating one if necessary."""
        with self.lock:
            if self.pool:
                return self.pool.popleft()
            else:
                self.created_count += 1
                return self.factory()
    
    def release(self, obj):
        """Return an object to the pool if there's space."""
        with self.lock:
            if len(self.pool) < self.max_size:
                self.pool.append(obj)
            else:
                # Pool is full, let the object be garbage collected
                pass
    
    def clear(self):
        """Clear all objects from the pool."""
        with self.lock:
            self.pool.clear()
            self.created_count = 0

class RenderWorkerPool:
    """Pool for RenderWorker instances."""
    
    def __init__(self, max_workers: int = 5):
        self.pool = ObjectPool(lambda: None, max_workers)  # We'll set the worker later
        self.available_workers: Deque[QThread] = deque()
        self.lock = threading.Lock()
    
    def get_worker(self, doc, zoom, page_num, parent=None):
        """Get a RenderWorker for the given parameters."""
        with self.lock:
            if self.available_workers:
                worker = self.available_workers.popleft()
                # Reconfigure the worker
                worker.doc = doc
                worker.zoom = zoom
                worker.page_num = page_num
                worker._is_running = True
                return worker
            else:
                # Create new worker
                from gui.components.pdf_viewer import RenderWorker
                worker = RenderWorker(doc, zoom, page_num, parent)
                return worker
    
    def return_worker(self, worker):
        """Return a worker to the pool."""
        with self.lock:
            if len(self.available_workers) < self.pool.max_size:
                self.available_workers.append(worker)
            else:
                worker.deleteLater()