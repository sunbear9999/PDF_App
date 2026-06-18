"""Tests for core/help/progress_store.py."""
import sys
import unittest


def _make_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


class TestProgressStore(unittest.TestCase):
    """
    Uses a patched QSettings with a unique test org/app name to avoid
    polluting real user settings.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def setUp(self):
        from unittest.mock import patch
        from PySide6.QtCore import QSettings
        # Use an isolated settings group for tests
        self._patcher = patch(
            "core.help.progress_store.QSettings",
            lambda org, app: QSettings("PapyrusTest_HelpTests", "TestRun"),
        )
        self._patcher.start()
        from core.help.progress_store import ProgressStore
        self.store = ProgressStore()
        # Clear any leftover state from previous test runs
        self.store.reset_all()

    def tearDown(self):
        self.store.reset_all()
        self._patcher.stop()

    def test_not_completed_by_default(self):
        self.assertFalse(self.store.is_completed("core.tutorial.x"))

    def test_mark_and_check_completed(self):
        self.store.mark_completed("core.tutorial.x")
        self.assertTrue(self.store.is_completed("core.tutorial.x"))

    def test_reset_single_tutorial(self):
        self.store.mark_completed("core.tutorial.x")
        self.store.mark_completed("core.tutorial.y")
        self.store.reset_tutorial("core.tutorial.x")
        self.assertFalse(self.store.is_completed("core.tutorial.x"))
        self.assertTrue(self.store.is_completed("core.tutorial.y"))

    def test_reset_all(self):
        self.store.mark_completed("core.tutorial.a")
        self.store.mark_completed("core.tutorial.b")
        self.store.reset_all()
        self.assertFalse(self.store.is_completed("core.tutorial.a"))
        self.assertFalse(self.store.is_completed("core.tutorial.b"))

    def test_get_all_completed(self):
        self.store.mark_completed("core.tutorial.a")
        self.store.mark_completed("core.tutorial.b")
        all_ids = self.store.get_all_completed()
        self.assertIn("core.tutorial.a", all_ids)
        self.assertIn("core.tutorial.b", all_ids)

    def test_get_all_completed_empty(self):
        ids = self.store.get_all_completed()
        self.assertEqual(ids, [])


if __name__ == "__main__":
    unittest.main()
