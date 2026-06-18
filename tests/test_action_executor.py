"""Tests for core/help/action_executor.py."""
import sys
import unittest
from unittest.mock import MagicMock, call, patch


def _make_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


class TestApprovedActionExecutor(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def _make_executor(self, resolve_result=None):
        from core.help.action_executor import ApprovedActionExecutor
        mock_targets = MagicMock()
        mock_targets.resolve.return_value = resolve_result
        mock_bus = MagicMock()
        return ApprovedActionExecutor(mock_targets, mock_bus), mock_targets, mock_bus

    # ------------------------------------------------------------------
    # Unknown type
    # ------------------------------------------------------------------

    def test_unknown_type_returns_false(self):
        exe, _, _ = self._make_executor()
        result = exe.execute({"type": "rm_rf_slash"})
        self.assertFalse(result)

    def test_unknown_type_does_not_emit(self):
        exe, _, mock_bus = self._make_executor()
        exe.execute({"type": "arbitrary_bad_action"})
        mock_bus.help_action_requested.emit.assert_not_called()

    # ------------------------------------------------------------------
    # open_dock
    # ------------------------------------------------------------------

    def test_open_dock_emits_intent(self):
        from core.events.domains.help_events import HelpIntent
        exe, _, mock_bus = self._make_executor()
        result = exe.execute({"type": "open_dock", "dock_id": "research"})
        self.assertTrue(result)
        mock_bus.help_action_requested.emit.assert_called_once()
        args = mock_bus.help_action_requested.emit.call_args[0]
        self.assertEqual(args[0], HelpIntent.OPEN_DOCK)
        self.assertEqual(args[1].dock_id, "research")

    def test_open_dock_missing_dock_id_returns_false(self):
        exe, _, _ = self._make_executor()
        result = exe.execute({"type": "open_dock"})
        self.assertFalse(result)

    # ------------------------------------------------------------------
    # focus_widget
    # ------------------------------------------------------------------

    def test_focus_widget_calls_set_focus(self):
        from PySide6.QtWidgets import QPushButton
        btn = QPushButton()
        exe, mock_targets, _ = self._make_executor(resolve_result=btn)
        with patch.object(btn, "setFocus") as mock_focus:
            result = exe.execute({"type": "focus_widget", "target_id": "toolbar.btn"})
        self.assertTrue(result)
        mock_focus.assert_called_once()

    def test_focus_widget_missing_target_returns_false(self):
        exe, mock_targets, _ = self._make_executor(resolve_result=None)
        result = exe.execute({"type": "focus_widget", "target_id": "nonexistent.btn"})
        self.assertFalse(result)

    # ------------------------------------------------------------------
    # select_tab
    # ------------------------------------------------------------------

    def test_select_tab_emits_intent(self):
        from core.events.domains.help_events import HelpIntent
        exe, _, mock_bus = self._make_executor()
        result = exe.execute({"type": "select_tab", "dock_id": "research", "tab_index": 4})
        self.assertTrue(result)
        mock_bus.help_action_requested.emit.assert_called_once()
        args = mock_bus.help_action_requested.emit.call_args[0]
        self.assertEqual(args[0], HelpIntent.SELECT_TAB)
        self.assertEqual(args[1].dock_id, "research")
        self.assertEqual(args[1].tab_index, 4)

    # ------------------------------------------------------------------
    # show_status
    # ------------------------------------------------------------------

    def test_show_status_emits_message(self):
        exe, _, mock_bus = self._make_executor()
        result = exe.execute({"type": "show_status", "message": "Hello!", "duration_ms": 2000})
        self.assertTrue(result)
        mock_bus.status_message_requested.emit.assert_called_once_with("Hello!", 2000)

    def test_show_status_empty_message_returns_false(self):
        exe, _, _ = self._make_executor()
        result = exe.execute({"type": "show_status", "message": ""})
        self.assertFalse(result)

    # ------------------------------------------------------------------
    # execute_many
    # ------------------------------------------------------------------

    def test_execute_many(self):
        exe, _, mock_bus = self._make_executor()
        exe.execute_many([
            {"type": "show_status", "message": "A", "duration_ms": 1000},
            {"type": "show_status", "message": "B", "duration_ms": 1000},
        ])
        self.assertEqual(mock_bus.status_message_requested.emit.call_count, 2)


if __name__ == "__main__":
    unittest.main()
