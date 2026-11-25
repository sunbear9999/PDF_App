"""RELATE — deterministic-first relation scoring for argument maps.

Scores candidate claim/evidence/reasoning pairs as supports / attacks /
elaborates using discourse markers + lexical overlap (no LLM). High-confidence
pairs are persisted immediately as ``intelligence_artifact_edges``
(``created_by="relate_deterministic"``); low-confidence pairs are emitted in
state so a following prompt-managed ``LLM_QUERY`` can adjudicate them and an
``ARTIFACT_WRITE`` persist the result — keeping LLM escalation rules-compliant
and minimal (only the ambiguous pairs reach the model).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext
from core.services.intelligence.discourse_relation_scorer import (
    score_relation as _score,
)

_HIGH_CONF = 0.72


class RelateStep(BaseStep):
    step_type = "RELATE"
    label = "Score Relations"
    category = "Intelligence"
    description = "Score claim/evidence relation pairs (supports/attacks/elaborates); persist confident edges, defer ambiguous ones to the LLM."
    input_schema = {
        "pairs": {"type": "json", "label": "Candidate pairs"},
        "edge_source": {"type": "string", "label": "Edge createdby label"},
        "persist": {"type": "boolean", "label": "Persist confident edges"},
    }
    output_schema = {"persisted_edges": "integer", "low_confidence_pairs": "json"}

    def execute(self, context: StepContext, inputs: dict):
        service = getattr(context, "artifact_service", None)
        pairs = _json_list(inputs.get("pairs"))
        persist = bool(inputs.get("persist", True))
        created_by = str(inputs.get("edge_source") or "relate_deterministic")

        persisted = 0
        low_conf: List[Dict[str, Any]] = []
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            src_id = pair.get("source_id") or pair.get("source")
            tgt_id = pair.get("target_id") or pair.get("target")
            src_text = str(pair.get("source_text") or "")
            tgt_text = str(pair.get("target_text") or "")
            relation, confidence = _score(src_text, tgt_text,
                                          str(pair.get("source_label") or ""),
                                          str(pair.get("target_label") or ""))
            if confidence >= _HIGH_CONF and src_id and tgt_id and persist and service:
                service.create_edge(
                    str(src_id), str(tgt_id), relation,
                    confidence=confidence, created_by=created_by,
                    metadata={"scorer": "discourse_markers"},
                )
                persisted += 1
            else:
                low_conf.append({
                    "source_id": src_id, "target_id": tgt_id,
                    "source_text": src_text[:400], "target_text": tgt_text[:400],
                    "candidate_relation": relation, "confidence": round(confidence, 3),
                })

        summary = {"persisted_edges": persisted, "low_confidence_pairs": low_conf}
        return self.build_result(
            json.dumps(summary),
            state_updates={"relate_low_confidence_pairs": low_conf, "relate_summary": summary},
        )


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else ([parsed] if parsed else [])
        except Exception:
            return []
    return []
