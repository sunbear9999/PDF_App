"""
core/engine/steps/analysis_compact_step.py

Migrated from MasterActionRunner._run_analysis_compact().
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class AnalysisCompactStep(BaseStep):
    step_type = "ANALYSIS_COMPACT"
    label = "Analysis Compact"
    category = "Analysis"
    description = "Compact analysis chunks to fit within the master synthesis context window."
    input_schema = {
        "chunks": {"type": "array", "label": "Analysis Chunks"},
        "max_chars": {"type": "integer", "label": "Max Characters"},
        "contract": {"type": "object", "label": "Analysis Contract"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.engine.analysis_runtime import AnalysisRuntime
        runtime = AnalysisRuntime(
            context.project_manager,
            context.prompt_manager,
            context.ontology_registry,
        )

        limits = context.state.get("analysis_limits") or {}
        contract = inputs.get("contract") or context.state.get("analysis_contract") or {}
        max_chars = int(
            inputs.get("max_chars")
            or limits.get("max_master_chars")
            or 16000
        )
        chunks = inputs.get("chunks", context.state.get("final_analysis", []))

        result = runtime.compact_master_input(chunks, max_chars, contract)
        return self.build_result(result)
