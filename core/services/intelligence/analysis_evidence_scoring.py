"""Candidate scoring kept separate from evidence packet orchestration."""
from __future__ import annotations

import json
from typing import Any, Dict, Tuple
from core.services.intelligence.argument_role_policy import ROLES




def score_evidence_candidate(
    row: Dict[str, Any], *, focus_overlap: int, mode_overlap: int,
    contract_overlap: int, method_intent: bool,
) -> Tuple[float, str, bool]:
    text = str(row.get("exact_text") or "")
    metadata = row.get("metadata") or {}
    class_label = str(
        metadata.get("argument_role")
        or metadata.get("class_label") or "unknown"
    )
    # Ranking consumes canonical classifications; it is not another classifier.
    suggested_role = class_label if class_label in ROLES else 'unknown'
    score = 1.0 + focus_overlap * 5.0 + mode_overlap * 1.5
    score += min(3, contract_overlap) * 0.5
    score += {
        "claim": 1.1, "reasoning": 1.3, "evidence": 1.2,
        "counterclaim": 0.9, "limitation": 1.0,
    }.get(class_label, 0.2)
    score += min(float(metadata.get("class_confidence") or 0.0), 1.0)
    if suggested_role == "methodology" and method_intent:
        score += 5.0
    if suggested_role == "limitation":
        score += 1.0
    word_count = len(text.split())
    if 8 <= word_count <= 65:
        score += 1.0
    elif word_count > 110:
        score -= 1.0
    return score, suggested_role, False


def analysis_mode_terms(mode_id: str) -> set[str]:
    return {
        "argument_map": {
            "claim", "argue", "because", "therefore", "method",
            "approach", "evidence", "result", "however", "limitation",
        },
        "argument_map_enriched": {
            "claim", "reason", "evidence", "method", "result", "limitation",
        },
        "claim_tracking": {
            "support", "contradict", "qualify", "repeat", "evidence",
            "finding", "however",
        },
    }.get(mode_id, {"claim", "evidence", "reason", "finding", "result"})


def empty_evidence_package(focus_signature: str) -> Dict[str, Any]:
    return {
        "context": json.dumps({"sections": []}), "package": {"sections": []},
        "selected": [], "alias_to_artifact_id": {},
        "alias_to_selection_artifact_id": {}, "selection_artifact_ids": [],
        "focus_signature": focus_signature, "reused_count": 0,
        "selected_count": 0, "section_count": 0,
    }
