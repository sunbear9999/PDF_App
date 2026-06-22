"""Tests for ExecutionResult / SystemEvent dataclasses and coercion logic."""
import unittest
from core.engine.execution_result import ExecutionResult, SystemEvent


class TestExecutionResultDefaults(unittest.TestCase):
    def test_empty_construction(self):
        r = ExecutionResult()
        self.assertIsNone(r.raw_value)
        self.assertEqual(r.state_updates, {})
        self.assertEqual(r.ui_payloads, [])
        self.assertEqual(r.system_events, [])

    def test_raw_value_stored(self):
        r = ExecutionResult(raw_value="hello")
        self.assertEqual(r.raw_value, "hello")

    def test_state_updates_stored(self):
        r = ExecutionResult(state_updates={"key": "value"})
        self.assertEqual(r.state_updates["key"], "value")

    def test_ui_payloads_stored(self):
        payload = {"type": "card_grid", "content": "{}"}
        r = ExecutionResult(ui_payloads=[payload])
        self.assertEqual(r.ui_payloads[0], payload)

    def test_system_events_stored(self):
        ev = SystemEvent(event_type="bus_emit", payload={"signal": "test"})
        r = ExecutionResult(system_events=[ev])
        self.assertEqual(r.system_events[0].event_type, "bus_emit")

    def test_mutable_fields_are_independent(self):
        """Default lists must not be shared between instances."""
        a = ExecutionResult()
        b = ExecutionResult()
        a.ui_payloads.append({"type": "x"})
        self.assertEqual(b.ui_payloads, [])


class TestSystemEvent(unittest.TestCase):
    def test_fields(self):
        ev = SystemEvent(event_type="prompt_trace", payload={"model": "llama3"})
        self.assertEqual(ev.event_type, "prompt_trace")
        self.assertEqual(ev.payload["model"], "llama3")

    def test_bus_emit_event_type(self):
        ev = SystemEvent(event_type="bus_emit", payload={"signal": "ai_graph_generated", "intent": None, "payload": None})
        self.assertEqual(ev.event_type, "bus_emit")
        self.assertEqual(ev.payload["signal"], "ai_graph_generated")


if __name__ == "__main__":
    unittest.main()
