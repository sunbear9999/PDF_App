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
        "user_prompt": {"type": "string", "label": "User Research Focus (optional)"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.engine.analysis_runtime import AnalysisRuntime
        runtime = AnalysisRuntime(
            context.project_manager,
            context.prompt_manager,
            context.ontology_registry,
        )

        template = inputs.get("template") or {}
        user_prompt = (inputs.get("user_prompt") or "").strip()
        if user_prompt:
            effective_template = dict(template)
            effective_template["instructions"] = (
                effective_template.get("instructions", "") +
                "\n\nUSER RESEARCH FOCUS: " + user_prompt
            )
        else:
            effective_template = template

        contract = runtime.build_contract(effective_template)
        contract["prompt_contract"] = runtime.build_prompt_contract(contract)

        state_updates = {
            "analysis_limits": contract.get("limits", {}),
            "template_instructions": effective_template.get("instructions", ""),
            "template_schema": contract["prompt_contract"],
            "analysis_contract": contract,
            "analysis_user_prompt": user_prompt,
            "analysis_chunk_system_prompt": runtime.chunk_system_prompt(effective_template, contract),
            "analysis_synthesis_system_prompt": runtime.synthesis_system_prompt(effective_template, contract),
            "analysis_master_system_prompt": runtime.master_system_prompt(effective_template, contract),
            "analysis_chunk_query_prompt": runtime.chunk_query_prompt(effective_template),
            "analysis_synthesis_query_prompt": runtime.synthesis_query_prompt(effective_template),
            "analysis_master_query_prompt": runtime.master_query_prompt(effective_template),
            # Hierarchical pipeline prompt keys
            "analysis_evidence_system_prompt": runtime.evidence_system_prompt(effective_template, contract),
            "analysis_evidence_query_prompt": runtime.evidence_query_prompt(effective_template),
            "analysis_section_synthesis_system_prompt": runtime.section_synthesis_system_prompt(effective_template, contract),
            "analysis_section_synthesis_query_prompt": runtime.section_synthesis_query_prompt(effective_template),
            "analysis_graph_plan_system_prompt": runtime.graph_plan_system_prompt(effective_template, contract),
            "analysis_graph_plan_query_prompt": runtime.graph_plan_query_prompt(effective_template),
            "analysis_graph_assembly_system_prompt": runtime.graph_assembly_system_prompt(effective_template, contract),
            "analysis_graph_assembly_query_prompt": runtime.graph_assembly_query_prompt(effective_template),
            "llm_prompt_trace": [],
            "analysis_prompt_trace": [
                {"step": "analysis_contract", "name": "chunk_system_prompt",
                 "content": runtime.chunk_system_prompt(effective_template, contract)},
                {"step": "analysis_contract", "name": "chunk_query_prompt_template",
                 "content": runtime.chunk_query_prompt(effective_template)},
                {"step": "analysis_contract", "name": "synthesis_system_prompt",
                 "content": runtime.synthesis_system_prompt(effective_template, contract)},
                {"step": "analysis_contract", "name": "synthesis_query_prompt_template",
                 "content": runtime.synthesis_query_prompt(effective_template)},
                {"step": "analysis_contract", "name": "master_system_prompt",
                 "content": runtime.master_system_prompt(effective_template, contract)},
                {"step": "analysis_contract", "name": "master_query_prompt_template",
                 "content": runtime.master_query_prompt(effective_template)},
                {"step": "analysis_contract", "name": "template_schema",
                 "content": contract["prompt_contract"]},
                # Hierarchical pipeline prompt traces
                {"step": "analysis_contract", "name": "evidence_system_prompt",
                 "content": runtime.evidence_system_prompt(effective_template, contract)},
                {"step": "analysis_contract", "name": "evidence_query_prompt_template",
                 "content": runtime.evidence_query_prompt(effective_template)},
                {"step": "analysis_contract", "name": "section_synthesis_system_prompt",
                 "content": runtime.section_synthesis_system_prompt(effective_template, contract)},
                {"step": "analysis_contract", "name": "section_synthesis_query_prompt_template",
                 "content": runtime.section_synthesis_query_prompt(effective_template)},
                {"step": "analysis_contract", "name": "graph_plan_system_prompt",
                 "content": runtime.graph_plan_system_prompt(effective_template, contract)},
                {"step": "analysis_contract", "name": "graph_plan_query_prompt_template",
                 "content": runtime.graph_plan_query_prompt(effective_template)},
                {"step": "analysis_contract", "name": "graph_assembly_system_prompt",
                 "content": runtime.graph_assembly_system_prompt(effective_template, contract)},
                {"step": "analysis_contract", "name": "graph_assembly_query_prompt_template",
                 "content": runtime.graph_assembly_query_prompt(effective_template)},
            ],
        }

        return self.build_result(contract, state_updates=state_updates)
