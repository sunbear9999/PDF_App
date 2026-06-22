"""
core/engine/steps/analysis_contract_step.py

Migrated from MasterActionRunner._run_analysis_contract().
All self.state["analysis_*"] assignments become state_updates in ExecutionResult.
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class AnalysisContractStep(BaseStep):
    step_type = "ANALYSIS_CONTRACT"
    label = "Analysis Contract"
    category = "Analysis"
    description = "Build a document analysis contract and populate state for subsequent analysis steps."
    input_schema = {
        "template": {"type": "object", "label": "Analysis Template"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.engine.analysis_runtime import AnalysisRuntime
        runtime = AnalysisRuntime(
            context.project_manager,
            context.prompt_manager,
            context.ontology_registry,
        )

        template = inputs.get("template") or {}
        contract = runtime.build_contract(template)
        contract["prompt_contract"] = runtime.build_prompt_contract(contract)

        state_updates = {
            "analysis_limits": contract.get("limits", {}),
            "template_instructions": template.get("instructions", ""),
            "template_schema": contract["prompt_contract"],
            "analysis_contract": contract,
            "analysis_chunk_system_prompt": runtime.chunk_system_prompt(template, contract),
            "analysis_synthesis_system_prompt": runtime.synthesis_system_prompt(template, contract),
            "analysis_master_system_prompt": runtime.master_system_prompt(template, contract),
            "analysis_chunk_query_prompt": runtime.chunk_query_prompt(template),
            "analysis_synthesis_query_prompt": runtime.synthesis_query_prompt(template),
            "analysis_master_query_prompt": runtime.master_query_prompt(template),
            "llm_prompt_trace": [],
            "analysis_prompt_trace": [
                {"step": "analysis_contract", "name": "chunk_system_prompt",
                 "content": runtime.chunk_system_prompt(template, contract)},
                {"step": "analysis_contract", "name": "chunk_query_prompt_template",
                 "content": runtime.chunk_query_prompt(template)},
                {"step": "analysis_contract", "name": "synthesis_system_prompt",
                 "content": runtime.synthesis_system_prompt(template, contract)},
                {"step": "analysis_contract", "name": "synthesis_query_prompt_template",
                 "content": runtime.synthesis_query_prompt(template)},
                {"step": "analysis_contract", "name": "master_system_prompt",
                 "content": runtime.master_system_prompt(template, contract)},
                {"step": "analysis_contract", "name": "master_query_prompt_template",
                 "content": runtime.master_query_prompt(template)},
                {"step": "analysis_contract", "name": "template_schema",
                 "content": contract["prompt_contract"]},
            ],
        }

        return self.build_result(contract, state_updates=state_updates)
