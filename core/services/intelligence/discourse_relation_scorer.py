"""Discourse-marker relation scoring shared by the RELATE step and Graph Seeds.

Scores a candidate (source, target) proposition pair as supports / attacks /
elaborates from discourse markers plus lexical overlap. It is a heuristic
*proposal* signal, not proof of an evidentiary relation; callers decide how
confident a proposal must be before it is published as a suggestion.
"""
from __future__ import annotations

import re
from typing import Tuple

SCORER_ID = "discourse_markers"
SCORER_VERSION = "1.1"
UNRELATED = "unrelated"

_ATTACK_RE = re.compile(
    r"\b(however|but|contrary|contradict\w*|refut\w*|disput\w*|reject\w*|"
    r"challeng\w*|oppos\w*|although|whereas|nevertheless|despite|counter\w*)\b", re.I)
_SUPPORT_RE = re.compile(
    r"\b(support\w*|confirm\w*|demonstrat\w*|show\w*|evidence|prove[sd]?|"
    r"corroborat\w*|consistent with|in line with|found that|indicates?)\b", re.I)
_ELAB_RE = re.compile(
    r"\b(because|therefore|thus|hence|since|furthermore|moreover|in addition|"
    r"specifically|that is|in other words|for example|namely)\b", re.I)


def score_relation(
    src_text: str, tgt_text: str, src_label: str, tgt_label: str
) -> Tuple[str, float]:
    """Return ``(relation_type, confidence)``; ``UNRELATED`` when no marker fires."""
    combined = f"{src_text} {tgt_text}"
    overlap = lexical_overlap(src_text, tgt_text)
    markers = {
        "attacks": len(_ATTACK_RE.findall(combined)),
        "supports": len(_SUPPORT_RE.findall(combined)),
        "elaborates": len(_ELAB_RE.findall(combined)),
    }
    if not any(markers.values()):
        # Role priors and shared vocabulary alone are not evidence of a relation.
        return UNRELATED, 0.0
    scores = {
        "attacks": float(markers["attacks"]),
        "supports": markers["supports"] + (0.5 if src_label == "evidence" and tgt_label == "claim" else 0.0),
        "elaborates": markers["elaborates"] + (0.4 if src_label == "reasoning" else 0.0),
    }
    relation = max(scores, key=scores.get)
    total = sum(scores.values()) or 1.0
    confidence = min(0.95, 0.55 + scores[relation] / (total + 1) + 0.1 * overlap)
    return relation, round(confidence, 3)


def lexical_overlap(a: str, b: str) -> float:
    wa = set(re.findall(r"[a-z0-9]{4,}", a.lower()))
    wb = set(re.findall(r"[a-z0-9]{4,}", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)
