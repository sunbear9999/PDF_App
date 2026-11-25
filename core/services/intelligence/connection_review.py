"""Quick review of entity connections: one sentence at a time, save as a typed link or reject.

A connection is one edge assertion between two entities with the sentence it rests on.
Saving keeps the original assertion (marked verified when its type is kept, corrected
when the user chose another type) and adds a human-made assertion of the chosen type on
the same evidence. Each save or reject is one journaled decision with its own undo.
"""
from __future__ import annotations

import json
import re
from contextlib import nullcontext
from typing import Any, Dict, Iterable, Optional

from core.db import intelligence_decision_journal as journal
from core.db import intelligence_decision_state as state
from core.models.entity_categories import MEASURE_TYPES, type_label
from core.services.intelligence.link_explanations import explain

# Types a user can save a connection as; plugins add their own.
CONNECTION_TYPES: Dict[str, tuple] = {
    "related_to": ("Related (unspecified)", None),
    "member_of": ("Member of / works for", None),
    "located_in_place": ("Located in", None),
    "family_of": ("Family relation", None),
    "allied_with": ("Allied / cooperates with", None),
    "opposes": ("Opposes / in conflict with", None),
    "created": ("Created / authored", None),
    "governs": ("Governs / rules", None),
}
# Machine links offered for quick review (observations and candidates between entities).
REVIEWABLE = ("co_occurs_with", "affiliation_candidate", "same_as_candidate")
USER_CREATOR = "user:connection_review"
MAX_HIGHLIGHTS = 24


def register_connection_type(key: str, label: str, plugin_id: Optional[str] = None) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,48}", key or ""):
        raise ValueError("Connection type keys are lowercase words joined by underscores.")
    CONNECTION_TYPES[key] = (label, plugin_id)


def remove_by_plugin(plugin_id: str) -> None:
    for key in [k for k, (_, owner) in CONNECTION_TYPES.items() if owner == plugin_id]:
        CONNECTION_TYPES.pop(key, None)


def connection_types() -> Dict[str, str]:
    return {key: label for key, (label, _) in CONNECTION_TYPES.items()}


def _filters(edge_types, entity_types, source_id, excluded):
    kinds = list(edge_types or REVIEWABLE + tuple(CONNECTION_TYPES))
    clauses = ["a.review_state='unreviewed'", "s.artifact_type='entity'", "t.artifact_type='entity'",
               "a.edge_type IN (SELECT value FROM json_each(?))", "a.assertion_id NOT IN (SELECT value FROM json_each(?))",
               "a.evidence_artifact_id IS NOT NULL"]
    params = [json.dumps(kinds), json.dumps(list(excluded or []))]
    for end in ("s", "t"):
        clauses.append(f"COALESCE(json_extract({end}.metadata_json,'$.entity_type'),'') NOT IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(sorted(MEASURE_TYPES)))
        clauses.append(f"{end}.status='complete' AND {end}.review_state IN ('unreviewed','verified','corrected')")
    if entity_types:
        clauses.append("(json_extract(s.metadata_json,'$.entity_type') IN (SELECT value FROM json_each(?)) OR "
                       "json_extract(t.metadata_json,'$.entity_type') IN (SELECT value FROM json_each(?)))")
        params.extend([json.dumps(list(entity_types))] * 2)
    if source_id:
        clauses.append("e.source_id=?")
        params.append(source_id)
    sql = (" FROM intelligence_edge_assertions a JOIN intelligence_artifacts s ON s.artifact_id=a.source_artifact_id "
           "JOIN intelligence_artifacts t ON t.artifact_id=a.target_artifact_id "
           "JOIN intelligence_artifacts e ON e.artifact_id=a.evidence_artifact_id WHERE " + " AND ".join(clauses))
    return sql, params


def next_connection(service, edge_types=(), entity_types=(), source_id="", excluded=()) -> Dict[str, Any]:
    """The next unreviewed connection under the filters, and how many remain."""
    conn = service.db._conn
    sql, params = _filters(edge_types, entity_types, source_id, excluded)
    remaining = conn.execute("SELECT COUNT(*)" + sql, params).fetchone()[0]
    first = conn.execute("SELECT a.assertion_id" + sql + " ORDER BY a.created_at, a.assertion_id LIMIT 1", params).fetchone()
    return {"card": card(service, first[0]) if first else None, "remaining": remaining}


def card(service, assertion_id: str) -> Dict[str, Any]:
    """Everything the simplified review shows: the sentence with both entities marked."""
    db = service.db
    assertion = state.assertion(db, assertion_id)
    if not assertion:
        raise ValueError("This connection is no longer available.")
    assertion = {**assertion, "metadata": json.loads(assertion.get("metadata_json") or "{}")}
    source, target = db.get_artifact(assertion["source_artifact_id"]), db.get_artifact(assertion["target_artifact_id"])
    sentence = db.get_artifact(assertion.get("evidence_artifact_id")) if assertion.get("evidence_artifact_id") else None
    text = (sentence or {}).get("exact_text") or ""
    return {"assertion_id": assertion_id, "edge_type": assertion["edge_type"], "review_state": assertion["review_state"],
            "sentence": text, "sentence_id": (sentence or {}).get("artifact_id"),
            "source_id": (sentence or {}).get("source_id"), "page": ((sentence or {}).get("locator") or {}).get("page"),
            "entities": [_entity(source), _entity(target)],
            "spans": highlight_spans(text, _surfaces(db, assertion, source, target)),
            "explanation": explain(assertion, source, target), "types": connection_types()}


def _entity(row):
    meta = (row or {}).get("metadata") or {}
    return {"artifact_id": (row or {}).get("artifact_id"), "name": (row or {}).get("exact_text") or "",
            "type": type_label(meta.get("entity_type"))}


def _surfaces(db, assertion, source, target):
    """Words to mark: the recorded surfaces, the mentions in this sentence, then the names."""
    found = list(((assertion.get("metadata") or {}).get("reason") or {}).get("surfaces") or [])
    evidence = assertion.get("evidence_artifact_id")
    if evidence:
        rows = db._conn.execute(
            "SELECT DISTINCT exact_text FROM intelligence_artifacts WHERE artifact_type='entity_mention' "
            "AND parent_artifact_id IN (?,?) AND json_extract(metadata_json,'$.evidence_artifact_id')=?",
            (assertion["source_artifact_id"], assertion["target_artifact_id"], evidence)).fetchall()
        found.extend(row[0] for row in rows if row[0])
    for row in (source, target):
        meta = (row or {}).get("metadata") or {}
        found.extend([(row or {}).get("exact_text") or "", *(meta.get("aliases") or [])])
    return [s for s in dict.fromkeys(s.strip() for s in found if s and s.strip())]


def highlight_spans(text: str, surfaces: Iterable[str]):
    """Non-overlapping [start, end) spans of whole-word occurrences, longest names first."""
    spans = []
    for surface in sorted(surfaces, key=len, reverse=True):
        for match in re.finditer(r"(?<!\w)" + re.escape(surface) + r"(?!\w)", text):
            if not any(match.start() < end and start < match.end() for start, end in spans):
                spans.append((match.start(), match.end()))
            if len(spans) >= MAX_HIGHLIGHTS:
                return sorted(spans)
    return sorted(spans)


def save_connection(service, assertion_id: str, edge_type: str) -> Dict[str, Any]:
    if edge_type not in CONNECTION_TYPES and edge_type not in REVIEWABLE:
        raise ValueError("Choose a registered connection type.")
    return _decide(service, assertion_id, edge_type)


def _decide(service, assertion_id, edge_type):
    db = service.db
    lock = getattr(service.project_manager, "db_lock", None)
    with db._write_lock:
        with lock if lock is not None else nullcontext():
            with journal.transaction(db):
                original = state.assertion(db, assertion_id)
                if not original or original["review_state"] != "unreviewed":
                    raise ValueError("This connection was already reviewed. Reload to continue.")
                ids = [assertion_id]
                if edge_type == original["edge_type"]:
                    before = state.capture(db, {"assertions": ids})
                    db.set_edge_assertion_review_state(assertion_id, "verified")
                else:
                    service.link_artifacts(original["source_artifact_id"], original["target_artifact_id"], edge_type,
                                           confidence=1.0, metadata={"reason": {"rule": "user", "from": original["edge_type"]}},
                                           created_by=USER_CREATOR, evidence_artifact_ids=[original["evidence_artifact_id"]])
                    created = db._conn.execute(
                        "SELECT assertion_id FROM intelligence_edge_assertions WHERE source_artifact_id=? AND target_artifact_id=? "
                        "AND edge_type=? AND evidence_artifact_id=? AND created_by=?",
                        (original["source_artifact_id"], original["target_artifact_id"], edge_type,
                         original["evidence_artifact_id"], USER_CREATOR)).fetchone()[0]
                    before = state.capture(db, {"assertions": ids})   # the new assertion did not exist before
                    ids.append(created)
                    db.set_edge_assertion_review_state(created, "verified")
                    db.set_edge_assertion_review_state(assertion_id, "corrected")
                scope = {"assertions": ids}
                after = state.capture(db, scope)
                receipt = journal.save(db, assertion_id, "knowledge", {"scope": scope, "before": before, "after": after,
                                                                       "command": "connection_save"})
    _notify(service, assertion_id, receipt)
    return {"receipt": receipt, "assertion_id": assertion_id}


def _notify(service, assertion_id, receipt):
    from core.events.domains.intelligence_events import IntelligenceEvent, KnowledgeReviewPayload
    signal = getattr(getattr(service.project_manager, "bus", None), "intelligence_state_changed", None)
    if signal is not None:
        signal.emit(IntelligenceEvent.KNOWLEDGE_REVIEWED, KnowledgeReviewPayload(assertion_id, "", (receipt or {}).get("decision_id") or ""))
