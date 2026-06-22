"""Tests for PythonScriptStep: sandbox exec, WorkflowStepAPI contract."""
import unittest
from unittest.mock import MagicMock
from core.engine.steps.python_script_step import PythonScriptStep
from core.engine.workflow_step_api import WorkflowStepAPI
from core.engine.execution_result import ExecutionResult
from core.plugins.plugin_step_protocol import StepContext


def _make_context(state=None):
    state = state or {}
    ctx = MagicMock(spec=StepContext)
    ctx.state = state
    ctx.abort_check = lambda: False
    ctx.project_manager = MagicMock()
    ctx.llm_manager = MagicMock()
    ctx.prompt_manager = MagicMock()
    ctx.ontology_registry = MagicMock()

    emitted = []
    api = WorkflowStepAPI(
        state_snapshot=state,
        emit_fn=lambda et, p: emitted.append((et, p)),
    )
    ctx.api = api
    ctx._emitted = emitted
    return ctx


class TestPythonScriptStep(unittest.TestCase):
    def test_simple_script_runs(self):
        step = PythonScriptStep()
        ctx = _make_context({"x": 10})
        inputs = {"script": "result = state.get('x', 0) * 2"}
        result = step.execute(ctx, inputs)
        self.assertIsInstance(result, ExecutionResult)
        self.assertEqual(result.raw_value, 20)

    def test_workflow_api_get_state(self):
        step = PythonScriptStep()
        ctx = _make_context({"answer": 42})
        inputs = {"script": "result = workflow_api.get_state('answer')"}
        result = step.execute(ctx, inputs)
        self.assertEqual(result.raw_value, 42)

    def test_workflow_api_emit_event(self):
        step = PythonScriptStep()
        ctx = _make_context()
        inputs = {"script": "workflow_api.emit_event('debug_log', {'msg': 'hi'}); result = 'done'"}
        result = step.execute(ctx, inputs)
        self.assertEqual(result.raw_value, "done")
        self.assertIn(("debug_log", {"msg": "hi"}), ctx._emitted)

    def test_script_runtime_error_returns_error_string(self):
        step = PythonScriptStep()
        ctx = _make_context()
        inputs = {"script": "result = 1 / 0"}
        result = step.execute(ctx, inputs)
        self.assertIn("Error", str(result.raw_value))

    def test_no_result_variable_returns_none_or_empty(self):
        step = PythonScriptStep()
        ctx = _make_context()
        inputs = {"script": "x = 1 + 1"}
        result = step.execute(ctx, inputs)
        # result starts as None in local_scope; script doesn't change it
        self.assertIn(result.raw_value, (None, "", "None"))

    def test_empty_script_returns_empty(self):
        step = PythonScriptStep()
        ctx = _make_context()
        inputs = {"script": ""}
        result = step.execute(ctx, inputs)
        self.assertEqual(result.raw_value, "")

    def test_missing_script_key_returns_empty(self):
        step = PythonScriptStep()
        ctx = _make_context()
        result = step.execute(ctx, {})
        self.assertEqual(result.raw_value, "")


if __name__ == "__main__":
    unittest.main()
