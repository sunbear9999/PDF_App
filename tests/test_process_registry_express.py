"""Regression tests for express-job lifecycle in ProcessRegistry.

Plugin background tasks (api.tasks.run_background) are enqueued as express
jobs. Unlike MasterActionRunner, PluginWorker does not call complete_job()
itself, so run_background must wire completion — otherwise every finished
plugin task stays in ProcessRegistry.express_jobs forever, pinning its dead
QThread and connected closures (memory leak fixed in the core cleanup).
"""
import time
import unittest
from unittest.mock import MagicMock

from PySide6.QtCore import QCoreApplication

from core.engine.process_manager import ProcessRegistry
from core.plugins.papyrus_api import _PluginTaskNamespace


def _wait_for(condition, app, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


class TestExpressJobCompletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _make_namespace(self, registry):
        core = MagicMock()
        core.process_registry = registry
        return _PluginTaskNamespace(core, "test_plugin")

    def test_successful_task_is_removed_from_express_jobs(self):
        registry = ProcessRegistry()
        tasks = self._make_namespace(registry)
        results = []

        tasks.run_background(lambda: 42, on_done=results.append)

        self.assertTrue(
            _wait_for(lambda: not registry.express_jobs, self.app),
            "express job was not removed after successful completion",
        )
        self.assertEqual(results, [42])
        self.assertEqual(tasks._active_workers, [])

    def test_failing_task_is_removed_from_express_jobs(self):
        registry = ProcessRegistry()
        tasks = self._make_namespace(registry)
        errors = []

        def boom():
            raise RuntimeError("boom")

        tasks.run_background(boom, on_error=errors.append)

        self.assertTrue(
            _wait_for(lambda: not registry.express_jobs, self.app),
            "express job was not removed after task error",
        )
        self.assertEqual(errors, ["boom"])
        self.assertEqual(tasks._active_workers, [])

    def test_complete_job_is_idempotent(self):
        from core.plugins.plugin_worker import PluginWorker

        registry = ProcessRegistry()
        worker = PluginWorker(lambda: None, job_name="noop")
        job = registry.enqueue_runner(worker, "noop", is_express=True)
        worker.wait(3000)
        registry.complete_job(job.id)
        registry.complete_job(job.id)  # second call must be a harmless no-op
        self.assertEqual(registry.express_jobs, [])


if __name__ == "__main__":
    unittest.main()
