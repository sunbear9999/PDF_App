"""Deterministic entity relations observed inside one sentence."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from core.models.entity_categories import MEASURE_TYPES
from core.models.intelligence_models import EntityMention

# Conservative, high-precision affiliation cue: "<Person> {of|at|from} <Org>".
AFFILIATION_RE = re.compile(r"\b(of|at|from|with)\b", re.I)
# 2.1: no co-occurrence links with dates, quantities or measures; every link records its reason.
EDGE_VERSION = "2.1"


def write_relations(artifact_service: Any, mentions: List[EntityMention],
                    canonical_ids: Dict[tuple, str], records: List[Dict[str, Any]]) -> None:
    by_sentence: Dict[str, List[EntityMention]] = {}
    for mention in mentions:
        if mention.sentence_id:
            by_sentence.setdefault(mention.sentence_id, []).append(mention)
    sentence_rows = {str(row.get("storage_ref_id") or row.get("artifact_id") or ""): row for row in records or []}
    for sentence_id, group in by_sentence.items():
        row = sentence_rows.get(sentence_id) or {}
        evidence = [row["artifact_id"]] if row.get("artifact_id") else []
        # Titles ("Justice") and measures are named beside almost everything: not connections.
        canon = [item for item in _canonical(group, canonical_ids) if item[1].entity_type not in MEASURE_TYPES | {"TITLE"}]
        _co_occurrence(artifact_service, canon, evidence)
        cue = AFFILIATION_RE.search(row.get("exact_text", "") if row else "")
        if cue:
            _affiliation(artifact_service, canon, evidence, cue.group(0))


def _reason(rule, first, second, **extra) -> Dict[str, Any]:
    """Structured reason shown wherever the link is reviewed."""
    return {"rule": rule, "surfaces": [first.surface_text, second.surface_text],
            "mention_ids": [m.metadata.get("artifact_id") for m in (first, second) if m.metadata.get("artifact_id")],
            **extra}


def _canonical(group, canonical_ids) -> List[Tuple[str, EntityMention]]:
    canon, seen = [], set()
    for mention in group:
        cid = mention.metadata.get("canonical_artifact_id") or canonical_ids.get(
            (mention.entity_type, mention.normalized_key))
        if cid and cid not in seen:
            seen.add(cid)
            canon.append((cid, mention))
    return canon


def _co_occurrence(artifact_service, canon, evidence) -> None:
    """One undirected edge per unordered pair."""
    for i in range(len(canon)):
        for j in range(i + 1, len(canon)):
            (low, first), (high, second) = sorted([canon[i], canon[j]], key=lambda item: item[0])
            artifact_service.link_artifacts(
                low, high, "co_occurs_with", confidence=0.6, created_by="entity_pipeline",
                metadata={"reason": _reason("same_sentence", first, second)},
                evidence_artifact_ids=evidence, producer_version=EDGE_VERSION)


def _affiliation(artifact_service, canon, evidence, cue) -> None:
    persons = [(cid, mention) for cid, mention in canon if mention.entity_type == "PERSON"]
    orgs = [(cid, mention) for cid, mention in canon if mention.entity_type == "ORG"]
    for person, person_mention in persons:
        for org, org_mention in orgs:
            artifact_service.link_artifacts(
                person, org, "affiliation_candidate", confidence=0.55,
                metadata={"suggested": True, "cue": "same_sentence",
                          "reason": _reason("affiliation_cue", person_mention, org_mention, cue_text=cue)},
                created_by="entity_pipeline", evidence_artifact_ids=evidence, producer_version=EDGE_VERSION)
