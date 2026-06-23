"""
core/engine/steps/analysis_finalize_step.py

Migrated from MasterActionRunner._run_analysis_finalize() and _emit_analysis_event().
Bus emissions are returned as SystemEvents instead of being fired directly.

Compatible with both the legacy pipeline (chunks=final_analysis, master=master_diagram,
synthesis=argument_synthesis string) and the hierarchical pipeline
(chunks=all_chunk_evidence, master=hydrated_graph, synthesis=all_section_syntheses list).
"""
from __future__ import annotations

import json

from core.engine.steps.base_step import BaseStep
from core.engine.execution_result import SystemEvent
from core.plugins.plugin_step_protocol import StepContext


class AnalysisFinalizeStep(BaseStep):
    step_type = "ANALYSIS_FINALIZE"
    label = "Analysis Finalize"
    category = "Analysis"
    description = "Normalize, save, and broadcast the completed analysis result."
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path"},
        "template": {"type": "object", "label": "Analysis Template"},
        "template_id": {"type": "string", "label": "Template ID"},
        "run_id": {"type": "string", "label": "Run ID"},
        "contract": {"type": "object", "label": "Analysis Contract"},
        "chunks": {"type": "array", "label": "Chunk Results"},
        "master": {"type": "object", "label": "Master Diagram"},
        "synthesis": {"type": "string", "label": "Argument Synthesis"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.engine.analysis_runtime import AnalysisRuntime
        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload

        runtime = AnalysisRuntime(
            context.project_manager,
            context.prompt_manager,
            context.ontology_registry,
        )

        template = inputs.get("template") or {}
        template_id = (
            inputs.get("template_id")
            or context.state.get("analysis_template_id")
            or template.get("id")
            or "analysis"
        )
        doc_path = (
            inputs.get("doc_path")
            or context.state.get("analysis_doc_path")
            or context.state.get("target_doc")
        )
        run_id = (
            inputs.get("run_id")
            or context.state.get("analysis_run_id")
            or runtime.run_id(doc_path, template_id)
        )
        contract = (
            inputs.get("contract")
            or context.state.get("analysis_contract")
            or runtime.build_contract(template)
        )

        chunks_raw = inputs.get("chunks") or context.state.get("all_chunk_evidence") or context.state.get("final_analysis", [])
        master_raw = inputs.get("master") or context.state.get("hydrated_graph") or context.state.get("master_diagram", {})
        synthesis_raw = inputs.get("synthesis") or context.state.get("all_section_syntheses") or context.state.get("argument_synthesis", "")
        # Serialize synthesis list to string for normalize_result compatibility
        if isinstance(synthesis_raw, (list, dict)):
            synthesis_str = json.dumps(synthesis_raw, ensure_ascii=False)
        else:
            synthesis_str = str(synthesis_raw or "")

        result = runtime.normalize_result(
            doc_path,
            template_id,
            run_id,
            template,
            contract,
            chunks_raw,
            master_raw,
            synthesis_str,
        )
        result["prompt_trace"] = {
            "rendered_templates": context.state.get("analysis_prompt_trace", []),
            "llm_calls": context.state.get("llm_prompt_trace", []),
            "blueprint": "DefaultBlueprints.get_analysis_blueprint",
        }
        runtime.save_result(result)

        def _make_bus_event(event_name: str) -> SystemEvent:
            return self.bus_emit_event(
                "analysis_result_changed",
                getattr(AnalysisEvent, event_name),
                AnalysisPayload(
                    doc_path=doc_path,
                    template_id=template_id,
                    run_id=run_id,
                    template=template or {},
                    result=result if isinstance(result, dict) else {"value": result},
                ),
            )

        system_events = [
            _make_bus_event("RESULT_READY"),
            _make_bus_event("RUN_COMPLETED"),
        ]

        return self.build_result(result, system_events=system_events)
