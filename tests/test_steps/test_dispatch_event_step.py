"""Tests for DispatchEventStep — verifies bus_emit SystemEvent is created."""
import unittest
from unittest.mock import MagicMock
from core.engine.steps.dispatch_event_step import DispatchEventStep
from core.engine.execution_result import ExecutionResult
from core.plugins.plugin_step_protocol import StepContext


def _make_context(state=None):
    ctx = MagicMock(spec=StepContext)
    ctx.state = state or {}
    ctx.abort_check = lambda: False
    ctx.project_manager = MagicMock()
    return ctx


class TestDispatchEventStep(unittest.TestCase):
    def test_returns_execution_result(self):
        step = DispatchEventStep()
        ctx = _make_context()
        inputs = {"signal_name": "test_signal", "intent": "RUN_BLUEPRINT", "payload": {"key": "val"}}
        result = step.execute(ctx, inputs)
        self.assertIsInstance(result, ExecutionResult)

    def test_bus_emit_system_event_created(self):
        step = DispatchEventStep()
        ctx = _make_context()
        inputs = {"signal_name": "workflow_action_requested", "intent": None, "payload": {}}
        result = step.execute(ctx, inputs)
        bus_events = [e for e in result.system_events if e.event_type == "bus_emit"]
        self.assertGreater(len(bus_events), 0)

    def test_signal_name_in_event_payload(self):
        step = DispatchEventStep()
        ctx = _make_context()
        inputs = {"signal_name": "my_special_signal", "intent": None, "payload": {}}
        result = step.execute(ctx, inputs)
        bus_events = [e for e in result.system_events if e.event_type == "bus_emit"]
        if bus_events:
            self.assertEqual(bus_events[0].payload.get("signal"), "my_special_signal")


if __name__ == "__main__":
    unittest.main()
