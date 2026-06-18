"""
tests/test_dialog_manager.py

Unit tests for gui/managers/dialog_manager.py.

The tests use a real QApplication and real QWidget/QDialog instances but
do NOT open windows on screen (all dialogs are kept hidden or immediately
closed).  This allows the suite to run in headless CI environments that
have an offscreen QPA backend.

Run with:
    cd /home/sunbear/Desktop/PDF_App
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_dialog_manager.py -v

or with unittest:
    QT_QPA_PLATFORM=offscreen python -m unittest tests.test_dialog_manager -v
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget

# Ensure exactly one QApplication exists for the whole test module.
_app = QApplication.instance() or QApplication(sys.argv)

from gui.managers.dialog_manager import DialogManager, exec_as_modal, resolve_main_window


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SimpleDialog(QDialog):
    """Minimal QDialog subclass used as a stand-in for real Papyrus dialogs."""
    pass


class _MainWindow(QMainWindow):
    """Minimal main window that hosts a DialogManager."""

    def __init__(self):
        super().__init__()
        self.dialog_manager = DialogManager(self, parent=self)


# ---------------------------------------------------------------------------
# resolve_main_window tests
# ---------------------------------------------------------------------------

class TestResolveMainWindow(unittest.TestCase):

    def test_returns_main_window_from_direct_hint(self):
        win = _MainWindow()
        result = resolve_main_window(win)
        self.assertIs(result, win)
        win.close()

    def test_walks_parent_chain(self):
        win = _MainWindow()
        child = QWidget(win)
        grandchild = QWidget(child)
        result = resolve_main_window(grandchild)
        self.assertIs(result, win)
        win.close()

    def test_falls_back_to_top_level_scan_when_hint_is_none(self):
        win = _MainWindow()
        result = resolve_main_window(None)
        # Some QMainWindow must be found — may be another window if tests run
        # in a shared QApplication context, but the result must not be None.
        self.assertIsNotNone(result)
        win.close()

    def test_returns_none_when_no_main_window_exists(self):
        # Patch topLevelWidgets to return empty list and activeWindow to None
        with (
            patch.object(QApplication, "topLevelWidgets", return_value=[]),
            patch.object(QApplication, "activeWindow", return_value=None),
        ):
            result = resolve_main_window(None)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# DialogManager.show_instance tests
# ---------------------------------------------------------------------------

class TestDialogManagerShowInstance(unittest.TestCase):

    def setUp(self):
        self.win = _MainWindow()
        self.dm = self.win.dialog_manager

    def tearDown(self):
        self.dm.close_all()
        self.win.close()

    def test_dialog_is_tracked_after_show(self):
        dlg = _SimpleDialog(self.win)
        self.dm.show_instance(dlg)
        self.assertIn(dlg, self.dm._unkeyed)

    def test_dialog_is_nonmodal_after_show(self):
        dlg = _SimpleDialog(self.win)
        self.dm.show_instance(dlg)
        self.assertEqual(dlg.windowModality(), Qt.WindowModality.NonModal)
        dlg.close()

    def test_dialog_has_tool_flag_after_show(self):
        dlg = _SimpleDialog(self.win)
        self.dm.show_instance(dlg)
        self.assertTrue(dlg.windowFlags() & Qt.WindowType.Tool)
        dlg.close()

    def test_dialog_has_wadelete_on_close(self):
        dlg = _SimpleDialog(self.win)
        self.dm.show_instance(dlg)
        self.assertTrue(dlg.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))
        dlg.close()

    def test_keyed_dialog_is_tracked(self):
        dlg = _SimpleDialog(self.win)
        self.dm.show_instance(dlg, key="test_key")
        self.assertIn("test_key", self.dm._keyed)
        dlg.close()

    def test_keyed_dialog_removed_on_destroy(self):
        dlg = _SimpleDialog(self.win)
        self.dm.show_instance(dlg, key="destroy_key")
        self.assertIn("destroy_key", self.dm._keyed)
        dlg.close()  # WA_DeleteOnClose triggers deletion → destroyed signal
        _app.processEvents()
        self.assertNotIn("destroy_key", self.dm._keyed)

    def test_unkeyed_dialog_removed_on_destroy(self):
        dlg = _SimpleDialog(self.win)
        self.dm.show_instance(dlg)
        self.assertIn(dlg, self.dm._unkeyed)
        dlg.close()
        _app.processEvents()
        self.assertNotIn(dlg, self.dm._unkeyed)

    def test_multiple_unkeyed_dialogs_coexist(self):
        d1 = _SimpleDialog(self.win)
        d2 = _SimpleDialog(self.win)
        self.dm.show_instance(d1)
        self.dm.show_instance(d2)
        self.assertEqual(len(self.dm._unkeyed), 2)
        d1.close()
        d2.close()


# ---------------------------------------------------------------------------
# DialogManager.show (by class) tests
# ---------------------------------------------------------------------------

class TestDialogManagerShowByClass(unittest.TestCase):

    def setUp(self):
        self.win = _MainWindow()
        self.dm = self.win.dialog_manager

    def tearDown(self):
        self.dm.close_all()
        self.win.close()

    def test_show_creates_instance(self):
        dlg = self.dm.show(_SimpleDialog, key="cls_test")
        self.assertIsInstance(dlg, _SimpleDialog)
        dlg.close()

    def test_show_factory_used_when_provided(self):
        sentinel = _SimpleDialog(self.win)
        dlg = self.dm.show(_SimpleDialog, factory=lambda: sentinel, key="factory_test")
        self.assertIs(dlg, sentinel)
        dlg.close()

    def test_singleton_reuses_existing_instance(self):
        d1 = self.dm.show(_SimpleDialog, key="singleton", singleton=True)
        d2 = self.dm.show(_SimpleDialog, key="singleton", singleton=True)
        self.assertIs(d1, d2)
        d1.close()

    def test_singleton_creates_new_after_close(self):
        d1 = self.dm.show(_SimpleDialog, key="singleton2", singleton=True)
        d1.close()
        _app.processEvents()
        d2 = self.dm.show(_SimpleDialog, key="singleton2", singleton=True)
        self.assertIsNotNone(d2)
        d2.close()

    def test_non_singleton_allows_multiple(self):
        d1 = self.dm.show(_SimpleDialog, key="multi1")
        d2 = self.dm.show(_SimpleDialog, key="multi2")
        self.assertIsNot(d1, d2)
        d1.close()
        d2.close()


# ---------------------------------------------------------------------------
# DialogManager.exec_modal tests
# ---------------------------------------------------------------------------

class TestDialogManagerExecModal(unittest.TestCase):

    def setUp(self):
        self.win = _MainWindow()
        self.dm = self.win.dialog_manager

    def tearDown(self):
        self.win.close()

    def test_exec_modal_applies_window_modal(self):
        dlg = _SimpleDialog(self.win)
        # We do not actually run exec() — just verify that after exec_modal
        # is called the modality flag is set correctly.  We patch exec() to
        # avoid blocking.
        with patch.object(dlg, "exec", return_value=QDialog.DialogCode.Accepted):
            self.dm.exec_modal(dlg)
        self.assertEqual(dlg.windowModality(), Qt.WindowModality.WindowModal)

    def test_exec_modal_applies_tool_flag(self):
        dlg = _SimpleDialog(self.win)
        with patch.object(dlg, "exec", return_value=QDialog.DialogCode.Rejected):
            self.dm.exec_modal(dlg)
        self.assertTrue(dlg.windowFlags() & Qt.WindowType.Tool)

    def test_exec_modal_returns_exec_result(self):
        dlg = _SimpleDialog(self.win)
        with patch.object(dlg, "exec", return_value=QDialog.DialogCode.Accepted):
            result = self.dm.exec_modal(dlg)
        self.assertEqual(result, QDialog.DialogCode.Accepted)

    def test_exec_modal_sets_parent_when_missing(self):
        dlg = _SimpleDialog()  # no parent
        self.assertIsNone(dlg.parent())
        with patch.object(dlg, "exec", return_value=QDialog.DialogCode.Rejected):
            self.dm.exec_modal(dlg)
        self.assertIsNotNone(dlg.parent())


# ---------------------------------------------------------------------------
# exec_as_modal (module-level helper) tests
# ---------------------------------------------------------------------------

class TestExecAsModal(unittest.TestCase):

    def setUp(self):
        self.win = _MainWindow()

    def tearDown(self):
        self.win.close()

    def test_applies_tool_flag(self):
        dlg = _SimpleDialog(self.win)
        with patch.object(dlg, "exec", return_value=QDialog.DialogCode.Rejected):
            exec_as_modal(dlg)
        self.assertTrue(dlg.windowFlags() & Qt.WindowType.Tool)

    def test_applies_window_modal(self):
        dlg = _SimpleDialog(self.win)
        with patch.object(dlg, "exec", return_value=QDialog.DialogCode.Accepted):
            exec_as_modal(dlg)
        self.assertEqual(dlg.windowModality(), Qt.WindowModality.WindowModal)

    def test_returns_exec_result(self):
        dlg = _SimpleDialog(self.win)
        with patch.object(dlg, "exec", return_value=QDialog.DialogCode.Accepted):
            result = exec_as_modal(dlg)
        self.assertEqual(result, QDialog.DialogCode.Accepted)


# ---------------------------------------------------------------------------
# DialogManager.close_all tests
# ---------------------------------------------------------------------------

class TestDialogManagerCloseAll(unittest.TestCase):

    def setUp(self):
        self.win = _MainWindow()
        self.dm = self.win.dialog_manager

    def tearDown(self):
        self.win.close()

    def test_close_all_empties_keyed(self):
        d1 = _SimpleDialog(self.win)
        self.dm.show_instance(d1, key="k1")
        self.dm.close_all()
        self.assertEqual(len(self.dm._keyed), 0)

    def test_close_all_empties_unkeyed(self):
        d1 = _SimpleDialog(self.win)
        self.dm.show_instance(d1)
        self.dm.close_all()
        _app.processEvents()
        self.assertEqual(len(self.dm._unkeyed), 0)

    def test_close_all_handles_already_deleted_dialog(self):
        """close_all must not raise RuntimeError if a dialog's C++ obj is gone."""
        d1 = _SimpleDialog(self.win)
        self.dm.show_instance(d1, key="gone")
        # Simulate C++ deletion by removing the key manually and testing close_all
        # doesn't crash with stale refs.
        d1.close()
        _app.processEvents()
        # After close, the key should already be removed; close_all is still safe.
        self.dm.close_all()  # must not raise


# ---------------------------------------------------------------------------
# Accepted/rejected signal tests
# ---------------------------------------------------------------------------

class TestDialogSignals(unittest.TestCase):

    def setUp(self):
        self.win = _MainWindow()
        self.dm = self.win.dialog_manager

    def tearDown(self):
        self.dm.close_all()
        self.win.close()

    def test_accepted_signal_fires(self):
        dlg = _SimpleDialog(self.win)
        received = []
        dlg.accepted.connect(lambda: received.append(True))
        self.dm.show_instance(dlg)
        dlg.accept()
        _app.processEvents()
        self.assertTrue(received)

    def test_rejected_signal_fires(self):
        dlg = _SimpleDialog(self.win)
        received = []
        dlg.rejected.connect(lambda: received.append(True))
        self.dm.show_instance(dlg)
        dlg.reject()
        _app.processEvents()
        self.assertTrue(received)

    def test_finished_signal_fires_on_close(self):
        dlg = _SimpleDialog(self.win)
        codes = []
        dlg.finished.connect(codes.append)
        self.dm.show_instance(dlg)
        dlg.reject()
        _app.processEvents()
        self.assertTrue(len(codes) > 0)


# ---------------------------------------------------------------------------
# Parent-resolution tests
# ---------------------------------------------------------------------------

class TestDialogParent(unittest.TestCase):

    def setUp(self):
        self.win = _MainWindow()
        self.dm = self.win.dialog_manager

    def tearDown(self):
        self.dm.close_all()
        self.win.close()

    def test_show_gives_dialog_main_window_as_parent(self):
        dlg = self.dm.show(_SimpleDialog, key="parent_test")
        self.assertIs(dlg.parent(), self.win)
        dlg.close()

    def test_dialog_is_not_top_level_application_window(self):
        """A Tool-window dialog should not appear as an independent application window."""
        dlg = _SimpleDialog(self.win)
        self.dm.show_instance(dlg)
        # Qt.Tool windows have the Tool flag set
        self.assertTrue(bool(dlg.windowFlags() & Qt.WindowType.Tool))
        dlg.close()


if __name__ == "__main__":
    unittest.main()
