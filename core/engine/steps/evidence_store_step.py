"""
core/engine/steps/evidence_store_step.py

Parses raw chunk evidence JSON from the per-chunk LLM call, generates stable
quote_ids, stores EvidenceQuote records via ProjectManager, and emits a
CHUNK_EVIDENCE_READY bus event so the GUI can display streaming note bubbles.

Returns a dict {"chunk_id": N, "refs": [...compact_refs...]} — never a bare
list — so the parent FOREACH uses append (not extend) when aggregating.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class EvidenceStoreStep(BaseStep):
    step_type = "EVIDENCE_STORE"
    label = "Evidence Store"
    category = "Analysis"
    description = (
        "Parse chunk evidence JSON, store exact quotes in the project DB with stable "
        "quote_ids, and emit a streaming CHUNK_EVIDENCE_READY event."
    )
    input_schema = {
        "chunk_evidence": {"type": "string", "label": "Raw chunk evidence JSON from LLM"},
        "chunk_id": {"type": "integer", "label": "Chunk index (0-based)"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.engine.analysis_runtime import AnalysisRuntime
        from core.events.domains.analysis_events import AnalysisEvent, AnalysisPayload
        from core.models.analysis_models import EvidenceQuote
        from core.utils.json_utils import extract_and_heal_json

        run_id = str(context.state.get("analysis_run_id") or "")
        doc_path = str(context.state.get("analysis_doc_path") or "")
        chunk_id = int(inputs.get("chunk_id") or 0)

        # --- parse LLM output -----------------------------------------------
        raw = inputs.get("chunk_evidence") or ""
        if isinstance(raw, dict):
            parsed = raw
        elif isinstance(raw, str):
            success, parsed = extract_and_heal_json(raw)
            if not success:
                parsed = {}
        else:
            parsed = {}

        quotes_data: List[Dict[str, Any]] = []
        for key in ("quotes", "q", "evidence", "items"):
            val = parsed.get(key) if isinstance(parsed, dict) else None
            if isinstance(val, list):
                quotes_data = val
                break

        # --- validate exact text against source chunk -----------------------
        chunk_text = ""
        item = context.state.get("item")
        if isinstance(item, dict):
            chunk_text = str(item.get("text") or "")

        if chunk_text and quotes_data:
            try:
                runtime = AnalysisRuntime(
                    context.project_manager,
                    context.prompt_manager,
                )
                validated = runtime.validate_chunk_observation_quotes(
                    {"quotes": quotes_data}, chunk_text
                )
                quotes_data = validated.get("quotes", quotes_data)
            except Exception:
                pass  # validation is best-effort

        # --- store each verified quote --------------------------------------
        compact_refs: List[Dict[str, Any]] = []
        now = datetime.utcnow().isoformat()

        for q in quotes_data:
            if not isinstance(q, dict):
                continue
            exact_text = str(
                q.get("exact_text") or q.get("x") or q.get("quote") or ""
            ).strip()
            if not exact_text:
                continue

            quote_id = hashlib.sha1(
                f"{run_id}:{exact_text}".encode()
            ).hexdigest()[:16]
            snippet = exact_text[:100]

            eq = EvidenceQuote(
                quote_id=quote_id,
                run_id=run_id,
                doc_path=doc_path,
                chunk_id=chunk_id,
                exact_text=exact_text,
                snippet=snippet,
                page_num=_safe_int(q.get("page_num") or q.get("page") or q.get("p")),
                note_text=str(q.get("note") or q.get("n") or "")[:200],
                importance_score=_clamp(q.get("importance") or q.get("i") or 0.5),
                confidence_score=_clamp(q.get("confidence", 0.5)),
                suggested_node_type=str(q.get("node_type") or q.get("type") or ""),
                quote_role=str(q.get("role") or q.get("r") or "supports"),
                tags_json=json.dumps(q.get("tags") or []),
                created_at=now,
            )

            if context.project_manager:
                try:
                    context.project_manager.save_evidence_quote(eq)
                except Exception:
                    pass

            compact_refs.append({
                "quote_id": quote_id,
                "snippet": snippet,
                "note": eq.note_text,
                "importance": eq.importance_score,
                "page_num": eq.page_num,
                "chunk_id": chunk_id,
                "role": eq.quote_role,
            })

        # --- emit streaming event -------------------------------------------
        system_events = [
            self.bus_emit_event(
                "analysis_result_changed",
                AnalysisEvent.CHUNK_EVIDENCE_READY,
                AnalysisPayload(
                    doc_path=doc_path,
                    run_id=run_id,
                    result={
                        "chunk_id": chunk_id,
                        "chunk_number": chunk_id + 1,
                        "compact_refs": compact_refs,
                        "doc_path": doc_path,
                    },
                ),
            )
        ]

        # Return a dict (not list) so FOREACH uses append, not extend
        return self.build_result(
            {"chunk_id": chunk_id, "refs": compact_refs},
            system_events=system_events,
        )


def _safe_int(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _clamp(val) -> float:
    try:
        return max(0.0, min(1.0, float(val)))
    except (TypeError, ValueError):
        return 0.5
