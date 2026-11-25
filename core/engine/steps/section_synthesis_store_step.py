"""
core/engine/steps/section_synthesis_store_step.py

Parses the raw per-section synthesis LLM result, emits a
SECTION_SYNTHESIS_READY progress event so the GUI can show real section
progress (the previously-dead handler in analysis_tab.py), and passes the
parsed dict through unchanged so all_section_syntheses aggregation is
unaffected — this step is purely an observability tap on the FOREACH loop,
not a data transform.
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class SectionSynthesisStoreStep(BaseStep):
    step_type = "SECTION_SYNTHESIS_STORE"
    label = "Section Synthesis Store"
    category = "Analysis"
    description = (
        "Parse a section synthesis LLM result, emit a SECTION_SYNTHESIS_READY "
        "progress event, and pass the parsed dict through unchanged."
    )
    input_schema = {
        "section_synthesis_result": {"type": "string", "label": "Raw section synthesis JSON from LLM"},
        "section_number": {"type": "integer", "label": "1-based section number"},
        "total_sections": {"type": "integer", "label": "Total section count"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
        from core.utils.json_utils import extract_and_heal_json

        raw = inputs.get("section_synthesis_result")
        if isinstance(raw, dict):
            parsed = raw
        elif isinstance(raw, str):
            success, parsed = extract_and_heal_json(raw)
            if not success:
                parsed = {}
        else:
            parsed = {}

        summary = str(parsed.get("summary") or "")[:200] if isinstance(parsed, dict) else ""

        system_events = [
            self.bus_emit_event(
                "analysis_result_changed",
                AnalysisEvent.SECTION_SYNTHESIS_READY,
                AnalysisPayload(
                    run_id=str(context.state.get("analysis_run_id") or ""),
                    result={
                        "section_number": inputs.get("section_number"),
                        "total_sections": inputs.get("total_sections"),
                        "summary": summary,
                    },
                ),
            )
        ]
        return self.build_result(parsed, system_events=system_events)
