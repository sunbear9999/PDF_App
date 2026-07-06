"""
core/engine/steps/section_subgraph_merge_step.py

Pure data-transformation step (no LLM call). Deterministically merges each
section synthesis's compact subgraph (n/e fields — the same compact shape
the chunk/master passes already use) into one concatenated master graph via
AnalysisRuntime.normalize_graph_object() — reusing the exact per-object
normalization the pipeline already applies elsewhere, just called once per
section instead of once for the whole document. AnalysisRuntime.
compact_master_input() then renders the merged graph back to compact text
for the final polish pass's prompt — also reused unchanged.

Replaces the old "merge_syntheses" PYTHON_SCRIPT step (which only built
section_syntheses_compact) — this step keeps that behavior AND adds the
graph merge, so most of the master graph is now assembled deterministically
before the final LLM pass ever runs, instead of that pass building the whole
graph from scratch over a flat quote list.
"""
from __future__ import annotations

from typing import Any, Dict, List

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class SectionSubgraphMergeStep(BaseStep):
    step_type = "SECTION_SUBGRAPH_MERGE"
    label = "Section Subgraph Merge"
    category = "Analysis"
    description = (
        "Deterministically normalize and concatenate each section synthesis's "
        "compact subgraph into one merged master graph, and rebuild the "
        "narrative section-summary text for the final polish pass."
    )
    input_schema = {
        "all_section_syntheses": {"type": "array", "label": "List of per-section synthesis result dicts"},
        "contract": {"type": "object", "label": "Analysis Contract"},
        "max_chars": {"type": "integer", "label": "Max chars for the compact-rendered graph text"},
    }
    output_schema = {
        "merged_master_graph": {"type": "json", "label": "Concatenated, normalized section subgraphs + compact_text"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.engine.analysis_runtime import AnalysisRuntime
        from core.utils.json_utils import extract_and_heal_json

        syntheses = inputs.get("all_section_syntheses") or []
        if isinstance(syntheses, str):
            _, syntheses = extract_and_heal_json(syntheses)
        if not isinstance(syntheses, list):
            syntheses = []

        contract = inputs.get("contract") or context.state.get("analysis_contract") or {}
        max_chars = int(inputs.get("max_chars") or 20000)
        runtime = AnalysisRuntime(context.project_manager, context.prompt_manager, context.ontology_registry)

        sections = [_coerce_section(s) for s in syntheses]
        sections = [s for s in sections if s]

        all_entities: List[Dict[str, Any]] = []
        all_relations: List[Dict[str, Any]] = []
        for idx, section in enumerate(sections):
            norm = runtime.normalize_graph_object(section, f"section{idx}", contract)
            all_entities.extend(norm.get("entities") or [])
            all_relations.extend(norm.get("relations") or [])

        merged_graph = {"summary": "", "entities": all_entities, "relations": all_relations}
        compact_text = runtime.compact_master_input([merged_graph], max_chars, contract)

        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
        system_events = [
            self.bus_emit_event(
                "analysis_result_changed",
                AnalysisEvent.GRAPH_PLAN_READY,
                AnalysisPayload(
                    run_id=str(context.state.get("analysis_run_id") or ""),
                    result={
                        "message": (
                            f"Merged {len(all_entities)} nodes and {len(all_relations)} "
                            "edges from all sections. Connecting and polishing final graph..."
                        ),
                    },
                ),
            )
        ]

        return self.build_result(
            {
                "entities": all_entities,
                "relations": all_relations,
                "compact_text": compact_text,
            },
            state_updates={"section_syntheses_compact": _build_compact_summary(sections)},
            system_events=system_events,
        )


def _coerce_section(s) -> Dict[str, Any]:
    from core.utils.json_utils import extract_and_heal_json
    if isinstance(s, str):
        success, parsed = extract_and_heal_json(s)
        return parsed if success and isinstance(parsed, dict) else {}
    return s if isinstance(s, dict) else {}


def _build_compact_summary(sections: List[Dict[str, Any]]) -> str:
    lines = []
    for i, s in enumerate(sections):
        sid = s.get("section_id", f"section_{i}")
        summary = str(s.get("summary") or "")[:300]
        themes = ", ".join(str(t) for t in (s.get("key_themes") or [])[:4])
        qids = ", ".join(str(q) for q in (s.get("key_quote_ids") or [])[:6])
        lines.append(f"[{sid}] {summary}\n  themes: {themes}\n  key_quotes: {qids}")
    return "\n\n".join(lines) if lines else "(no section syntheses)"
