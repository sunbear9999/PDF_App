# services/thread_manager.py
# [REFACTOR] Centralized thread management service for all AI workers
# Prevents memory leaks, race conditions, and provides graceful shutdown

import logging
from typing import Dict, List, Optional
from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer

logger = logging.getLogger(__name__)


class ThreadManager(QObject):
    """[REFACTOR] Centralized manager for all background threads and workers.

    Responsibilities:
    - Track all active workers
    - Provide graceful shutdown of all threads
    - Prevent memory leaks from abandoned threads
    - Monitor thread health and cleanup completed threads
    - Ensure proper thread affinity for UI updates

    Usage:
        thread_manager = ThreadManager()
        worker = SomeWorker()
        thread_manager.register_worker("my_worker", worker)
        worker.start()
        # ThreadManager will automatically clean up when worker finishes
    """

    # Signals for monitoring
    worker_started = pyqtSignal(str)  # worker_id
    worker_finished = pyqtSignal(str)  # worker_id
    worker_error = pyqtSignal(str, str)  # worker_id, error_msg

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_workers: Dict[str, QThread] = {}
        self.completed_workers: List[str] = []

        # Periodic cleanup of completed threads
        self.cleanup_timer = QTimer(self)
        self.cleanup_timer.timeout.connect(self._cleanup_completed_workers)
        self.cleanup_timer.start(30000)  # Check every 30 seconds

        logger.debug("ThreadManager initialized")

    def register_worker(self, worker_id: str, worker: QThread) -> None:
        """Register a worker thread for centralized management.

        Args:
            worker_id: Unique identifier for this worker
            worker: The QThread worker instance
        """
        if worker_id in self.active_workers:
            logger.warning(f"Worker {worker_id} already registered, replacing")
            self._cleanup_worker(worker_id)

        self.active_workers[worker_id] = worker

        # Connect to worker signals for monitoring
        if hasattr(worker, 'finished'):
            worker.finished.connect(lambda: self._on_worker_finished(worker_id))
        if hasattr(worker, 'error'):
            worker.error.connect(lambda msg: self._on_worker_error(worker_id, msg))

        logger.debug(f"Registered worker: {worker_id}")

    def unregister_worker(self, worker_id: str) -> None:
        """Manually unregister a worker (usually called after manual cleanup)."""
        if worker_id in self.active_workers:
            del self.active_workers[worker_id]
            logger.debug(f"Unregistered worker: {worker_id}")

    def stop_worker(self, worker_id: str, timeout: int = 5000) -> bool:
        """Stop a specific worker thread gracefully.

        Args:
            worker_id: ID of worker to stop
            timeout: Milliseconds to wait for graceful shutdown

        Returns:
            True if stopped successfully, False if timed out or not found
        """
        if worker_id not in self.active_workers:
            logger.warning(f"Worker {worker_id} not found for stopping")
            return False

        worker = self.active_workers[worker_id]

        # Request graceful stop if worker supports it
        if hasattr(worker, 'stop'):
            worker.stop()

        # Wait for completion
        if worker.isRunning():
            if not worker.wait(timeout):
                logger.error(f"Worker {worker_id} did not stop gracefully, terminating")
                worker.terminate()
                worker.wait(1000)  # Give it 1 second to terminate

        self._cleanup_worker(worker_id)
        return True

    def stop_all_workers(self, timeout: int = 10000) -> None:
        """Stop all active workers gracefully. Called during app shutdown."""
        logger.info(f"Stopping {len(self.active_workers)} active workers")

        for worker_id in list(self.active_workers.keys()):
            self.stop_worker(worker_id, timeout // max(1, len(self.active_workers)))

        logger.info("All workers stopped")

    def get_active_workers(self) -> List[str]:
        """Get list of currently active worker IDs."""
        return list(self.active_workers.keys())

    def is_worker_active(self, worker_id: str) -> bool:
        """Check if a specific worker is currently active."""
        return worker_id in self.active_workers and self.active_workers[worker_id].isRunning()

    def _on_worker_finished(self, worker_id: str) -> None:
        """Handle worker completion."""
        if worker_id in self.active_workers:
            logger.debug(f"Worker finished: {worker_id}")
            self.worker_finished.emit(worker_id)
            # Don't cleanup immediately - let periodic cleanup handle it
            # This prevents issues if finished signal is emitted multiple times

    def _on_worker_error(self, worker_id: str, error_msg: str) -> None:
        """Handle worker errors."""
        logger.error(f"Worker {worker_id} error: {error_msg}")
        self.worker_error.emit(worker_id, error_msg)

    def _cleanup_completed_workers(self) -> None:
        """Periodic cleanup of completed workers."""
        to_cleanup = []

        for worker_id, worker in self.active_workers.items():
            if not worker.isRunning():
                to_cleanup.append(worker_id)

        for worker_id in to_cleanup:
            self._cleanup_worker(worker_id)

        if to_cleanup:
            logger.debug(f"Cleaned up {len(to_cleanup)} completed workers")

    def _cleanup_worker(self, worker_id: str) -> None:
        """Clean up a completed worker."""
        if worker_id in self.active_workers:
            worker = self.active_workers[worker_id]

            # Disconnect signals to prevent memory leaks
            try:
                if hasattr(worker, 'finished'):
                    worker.finished.disconnect()
                if hasattr(worker, 'error'):
                    worker.error.disconnect()
            except (TypeError, RuntimeError):
                # Signals may already be disconnected
                pass

            # Mark as completed and remove from active list
            self.completed_workers.append(worker_id)
            del self.active_workers[worker_id]

            # Keep only last 100 completed workers for debugging
            if len(self.completed_workers) > 100:
                self.completed_workers = self.completed_workers[-100:]

    def __del__(self):
        """Ensure cleanup on destruction."""
        if hasattr(self, 'cleanup_timer') and self.cleanup_timer:
            self.cleanup_timer.stop()

        # Force stop all workers on destruction
        try:
            self.stop_all_workers(2000)
        except Exception as e:
            logger.error(f"Error during ThreadManager cleanup: {e}")
