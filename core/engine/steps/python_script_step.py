"""
core/engine/steps/python_script_step.py

Migrated from MasterActionRunner._run_python().
Injects WorkflowStepAPI as ``workflow_api`` instead of exposing raw runner state.
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class PythonScriptStep(BaseStep):
    step_type = "PYTHON_SCRIPT"
    label = "Python Script"
    category = "Transform"
    description = "Run a constrained local transform script."
    input_schema = {
        "script": {"type": "string", "label": "Python Script", "required": True},
    }

    def execute(self, context: StepContext, inputs: dict):
        script = inputs.get("script", "")
        if not script.strip():
            return self.build_result("")

        # Build the local scope — workflow_api is the only bridge to the runner.
        # analysis_api and bias_api are included for backward compat with existing scripts.
        local_scope: dict = {
            "state": dict(context.state),   # read-only snapshot
            "result": None,
            "workflow_api": context.api,
        }

        # backward-compat shims still import from runner environment
        try:
            from core.engine.bias_evaluator import BiasEvaluator
            local_scope["bias_api"] = BiasEvaluator(context.llm_manager, context.project_manager)
        except Exception:
            local_scope["bias_api"] = None

        try:
            from core.engine.analysis_runtime import AnalysisRuntime
            local_scope["analysis_api"] = AnalysisRuntime(
                context.project_manager,
                context.prompt_manager,
                context.ontology_registry,
            )
        except Exception:
            local_scope["analysis_api"] = None

        try:
            exec(script, {}, local_scope)  # noqa: S102
        except Exception as e:
            return self.build_result(f"Script Execution Error: {e}")

        return self.build_result(local_scope.get("result", ""))
