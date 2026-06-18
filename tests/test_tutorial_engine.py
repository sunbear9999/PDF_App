"""Tests for core/help/tutorial_engine.py."""
import sys
import unittest
from unittest.mock import MagicMock, patch


def _make_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _make_tutorial(tid="core.test", steps=None):
    from core.help.models import TutorialDefinition, TutorialStep
    if steps is None:
        steps = [
            TutorialStep(id="s1", target_id="toolbar.btn", text="Click this"),
            TutorialStep(id="s2", target_id="panel.main", text="Now here"),
        ]
    return TutorialDefinition(id=tid, title="Test Tutorial", steps=steps)


def _make_engine(resolve_widget=None):
    from core.help.tutorial_registry import TutorialRegistry
    from core.help.ui_target_registry import UITargetRegistry
    from core.help.action_executor import ApprovedActionExecutor
    from core.help.tutorial_engine import TutorialEngine
    from core.events.event_bus import EventBus

    # Build minimal real registry with one tutorial
    t_registry = TutorialRegistry()

    mock_targets = MagicMock()
    mock_targets.resolve.return_value = resolve_widget

    mock_bus = MagicMock()

    mock_executor = MagicMock()

    engine = TutorialEngine(t_registry, mock_targets, mock_executor, mock_bus)
    return engine, t_registry, mock_targets, mock_bus, mock_executor


class TestTutorialEngineIdle(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def test_initial_state_is_idle(self):
        from core.help.models import TutorialState
        engine, *_ = _make_engine()
        self.assertEqual(engine.state, TutorialState.IDLE)

    def test_start_unknown_tutorial_returns_false(self):
        engine, *_ = _make_engine()
        self.assertFalse(engine.start_tutorial("nonexistent.tutorial"))

    def test_step_count_when_idle(self):
        engine, *_ = _make_engine()
        self.assertEqual(engine.step_count, 0)

    def test_current_step_when_idle(self):
        engine, *_ = _make_engine()
        self.assertIsNone(engine.current_step)


class TestTutorialEngineStart(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def _make_with_tutorial(self, resolve_widget=None):
        from PySide6.QtWidgets import QPushButton
        widget = resolve_widget or QPushButton()
        engine, t_registry, mock_targets, mock_bus, mock_executor = _make_engine(resolve_widget=widget)
        tutorial = _make_tutorial()
        t_registry.register(tutorial)
        mock_targets.resolve.return_value = widget
        return engine, widget

    def test_start_known_tutorial_returns_true(self):
        engine, _ = self._make_with_tutorial()
        result = engine.start_tutorial("core.test")
        self.assertTrue(result)

    def test_step_count_after_start(self):
        engine, _ = self._make_with_tutorial()
        engine.start_tutorial("core.test")
        self.assertEqual(engine.step_count, 2)

    def test_state_transitions_to_waiting_for_interaction(self):
        from core.help.models import TutorialState
        from PySide6.QtTest import QTest
        engine, _ = self._make_with_tutorial()
        engine.start_tutorial("core.test")
        # When target is immediately resolved, engine reaches WAITING_FOR_INTERACTION
        self.assertEqual(engine.state, TutorialState.WAITING_FOR_INTERACTION)

    def test_step_ready_signal_emitted(self):
        received = []
        engine, widget = self._make_with_tutorial()
        engine.step_ready.connect(lambda step, w: received.append((step, w)))
        engine.start_tutorial("core.test")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0].id, "s1")

    def test_cannot_start_while_running(self):
        engine, _ = self._make_with_tutorial()
        engine.start_tutorial("core.test")
        result = engine.start_tutorial("core.test")
        self.assertFalse(result)


class TestTutorialEngineAdvance(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def _started_engine(self):
        from PySide6.QtWidgets import QPushButton
        widget = QPushButton()
        engine, t_registry, mock_targets, mock_bus, mock_executor = _make_engine(resolve_widget=widget)
        tutorial = _make_tutorial()
        t_registry.register(tutorial)
        mock_targets.resolve.return_value = widget
        engine.start_tutorial("core.test")
        return engine, mock_bus

    def test_advance_moves_to_next_step(self):
        engine, _ = self._started_engine()
        self.assertEqual(engine.current_step_index, 0)
        engine.advance()
        self.assertEqual(engine.current_step_index, 1)

    def test_advance_on_last_step_completes(self):
        from core.help.models import TutorialState
        engine, _ = self._started_engine()
        engine.advance()  # step 0 → step 1
        engine.advance()  # step 1 → COMPLETED
        self.assertEqual(engine.state, TutorialState.COMPLETED)

    def test_tutorial_completed_signal_emitted(self):
        completed = []
        engine, _ = self._started_engine()
        engine.tutorial_completed.connect(lambda tid: completed.append(tid))
        engine.advance()
        engine.advance()
        self.assertEqual(completed, ["core.test"])

    def test_go_back_moves_to_previous_step(self):
        engine, _ = self._started_engine()
        engine.advance()
        self.assertEqual(engine.current_step_index, 1)
        engine.go_back()
        self.assertEqual(engine.current_step_index, 0)

    def test_go_back_on_first_step_is_noop(self):
        engine, _ = self._started_engine()
        engine.go_back()
        self.assertEqual(engine.current_step_index, 0)


class TestTutorialEngineCancel(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def test_cancel_emits_cancelled_signal(self):
        from PySide6.QtWidgets import QPushButton
        widget = QPushButton()
        engine, t_registry, mock_targets, *_ = _make_engine(resolve_widget=widget)
        tutorial = _make_tutorial()
        t_registry.register(tutorial)
        mock_targets.resolve.return_value = widget

        cancelled = []
        engine.tutorial_cancelled.connect(lambda tid: cancelled.append(tid))
        engine.start_tutorial("core.test")
        engine.cancel()
        self.assertIn("core.test", cancelled)

    def test_cancel_sets_state_to_cancelled(self):
        from core.help.models import TutorialState
        from PySide6.QtWidgets import QPushButton
        widget = QPushButton()
        engine, t_registry, mock_targets, *_ = _make_engine(resolve_widget=widget)
        tutorial = _make_tutorial()
        t_registry.register(tutorial)
        mock_targets.resolve.return_value = widget
        engine.start_tutorial("core.test")
        engine.cancel()
        self.assertEqual(engine.state, TutorialState.CANCELLED)

    def test_poll_timer_stopped_after_cancel(self):
        from PySide6.QtWidgets import QPushButton
        # Use a target that is NOT found so the poll timer starts
        engine, t_registry, mock_targets, *_ = _make_engine(resolve_widget=None)
        tutorial = _make_tutorial()
        t_registry.register(tutorial)
        engine.start_tutorial("core.test")
        # Poll timer should be running now
        self.assertTrue(engine._poll_timer.isActive())
        engine.cancel()
        self.assertFalse(engine._poll_timer.isActive())


class TestTutorialEngineTargetNotFound(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def test_target_missing_stays_in_waiting_state(self):
        from core.help.models import TutorialState
        engine, t_registry, mock_targets, *_ = _make_engine(resolve_widget=None)
        tutorial = _make_tutorial()
        t_registry.register(tutorial)
        engine.start_tutorial("core.test")
        self.assertEqual(engine.state, TutorialState.WAITING_FOR_TARGET)

    def test_poll_timer_starts_when_target_missing(self):
        engine, t_registry, mock_targets, *_ = _make_engine(resolve_widget=None)
        tutorial = _make_tutorial()
        t_registry.register(tutorial)
        engine.start_tutorial("core.test")
        self.assertTrue(engine._poll_timer.isActive())
        engine.cancel()


if __name__ == "__main__":
    unittest.main()
