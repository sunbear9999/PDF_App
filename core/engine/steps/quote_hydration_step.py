"""
core/engine/steps/quote_hydration_step.py

Looks up every quote_id referenced in the assembled graph and attaches the
exact original quote text (and page/chunk metadata) from the EvidenceDB.
Flags missing quote_ids without inventing text.
Emits GRAPH_HYDRATED after completion.
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class QuoteHydrationStep(BaseStep):
    step_type = "QUOTE_HYDRATION"
    label = "Quote Hydration"
    category = "Analysis"
    description = (
        "Resolve quote_ids in the assembled graph to exact original text from the "
        "evidence DB.  Flags any unresolvable quote_ids."
    )
    input_schema = {
        "assembled_graph": {"type": "object", "label": "Graph JSON with source_quote_id fields"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
        from core.utils.json_utils import extract_and_heal_json

        run_id = str(context.state.get("analysis_run_id") or "")

        # --- parse graph ---------------------------------------------------
        raw = inputs.get("assembled_graph") or context.state.get("assembled_graph") or {}
        if isinstance(raw, str):
            ok, parsed = extract_and_heal_json(raw)
            graph = parsed if ok and isinstance(parsed, dict) else {}
        elif isinstance(raw, dict):
            graph = raw
        else:
            graph = {}

        # --- load evidence lookup ------------------------------------------
        quotes_lookup = {}
        if context.project_manager and run_id:
            try:
                for eq in context.project_manager.get_evidence_quotes_for_run(run_id):
                    quotes_lookup[eq.quote_id] = eq
            except Exception:
                pass

        # --- hydrate entity nodes ------------------------------------------
        missing_ids = []
        entities = list(graph.get("entities") or [])
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            props = entity.get("properties") or {}
            quote_id = (
                props.get("source_quote_id")
                or entity.get("source_quote_id")
                or entity.get("quote_id")
                or ""
            )
            if not quote_id:
                continue
            eq = quotes_lookup.get(str(quote_id))
            if eq:
                entity["exact_text"] = eq.exact_text
                entity["page"] = eq.page_num
                if not isinstance(props, dict):
                    entity["properties"] = props = {}
                props["exact_text"] = eq.exact_text
                props["page_num"] = eq.page_num
                props["chunk_id"] = eq.chunk_id
                props["quote_verified"] = True
            else:
                missing_ids.append(str(quote_id))

        hydrated = dict(graph)
        hydrated["entities"] = entities
        hydrated["hydration_missing_ids"] = missing_ids

        # --- emit event ----------------------------------------------------
        system_events = [
            self.bus_emit_event(
                "analysis_result_changed",
                AnalysisEvent.GRAPH_HYDRATED,
                AnalysisPayload(
                    run_id=run_id,
                    result={
                        "hydrated_entity_count": len(entities),
                        "missing_quote_ids": missing_ids,
                    },
                ),
            )
        ]

        return self.build_result(hydrated, system_events=system_events)
