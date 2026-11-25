"""Human links from an entity that add a role without changing the entity itself.

Used for "also cites this bibliography entry" (``cites_source``), "is a subgroup of this
entity" (``subgroup_of``) and typed connections chosen instead of a merge. Each change is
one journaled decision with undo; links are on canonical entity ids, which stay stable
across reprocessing, and are human-verified, so reruns keep them.
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any, Dict, List, Optional

from core.db import intelligence_decision_journal as journal
from core.db import intelligence_decision_state as state

ACTIVE = ("verified", "corrected")


def current(db, source_ids, edge_type) -> Dict[str, Dict[str, Any]]:
    """source id -> {artifact_id, exact_text, metadata} of its current single link target."""
    if not source_ids or not db._conn:
        return {}
    rows = db._conn.execute(
        "SELECT a.source_artifact_id, t.artifact_id, t.exact_text, t.metadata_json FROM intelligence_edge_assertions a "
        "JOIN intelligence_artifacts t ON t.artifact_id=a.target_artifact_id WHERE a.edge_type=? "
        "AND a.review_state IN ('verified','corrected') AND a.source_artifact_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY a.created_at", (edge_type, json.dumps(list(source_ids)))).fetchall()
    return {s: {"artifact_id": t, "exact_text": text or "", "metadata": json.loads(meta or "{}")} for s, t, text, meta in rows}


def pointing_at(db, target_ids, edge_type) -> Dict[str, List[Dict[str, Any]]]:
    """target id -> entities linking to it (with their stored mention counts)."""
    if not target_ids or not db._conn:
        return {}
    rows = db._conn.execute(
        "SELECT a.target_artifact_id, n.artifact_id, n.exact_text, n.metadata_json FROM intelligence_edge_assertions a "
        "JOIN intelligence_artifacts n ON n.artifact_id=a.source_artifact_id WHERE a.edge_type=? "
        "AND a.review_state IN ('verified','corrected') AND n.status='complete' "
        "AND a.target_artifact_id IN (SELECT value FROM json_each(?)) ORDER BY n.exact_text",
        (edge_type, json.dumps(list(target_ids)))).fetchall()
    found: Dict[str, List[Dict[str, Any]]] = {}
    for target, source, text, meta in rows:
        meta = json.loads(meta or "{}")
        found.setdefault(target, []).append({"artifact_id": source, "exact_text": text or "", "metadata": meta,
                                             "mention_count": meta.get("mention_count") or 0})
    return found


def _active_ids(db, source_id, edge_type, target_id=None):
    sql = ("SELECT assertion_id FROM intelligence_edge_assertions WHERE source_artifact_id=? AND edge_type=? "
           "AND review_state IN ('verified','corrected')")
    params = [source_id, edge_type]
    if target_id:
        sql += " AND target_artifact_id=?"
        params.append(target_id)
    return [row[0] for row in db._conn.execute(sql, params)]


def set_link(service, source_id: str, target_id: Optional[str], edge_type: str, creator: str, *,
             single: bool = True, evidence: Optional[str] = None, command: str = "") -> Dict[str, Any]:
    """Add a verified human link (replacing the previous one when ``single``), or clear it with None."""
    db = service.db
    lock = getattr(service.project_manager, "db_lock", None)
    with db._write_lock:
        with lock if lock is not None else nullcontext():
            with journal.transaction(db):
                existing = _active_ids(db, source_id, edge_type) if single else []
                before = state.capture(db, {"assertions": existing})
                for identity in existing:
                    db.set_edge_assertion_review_state(identity, "rejected")
                ids = list(existing)
                if target_id:
                    service.link_artifacts(source_id, target_id, edge_type, confidence=1.0, created_by=creator,
                                           metadata={"reason": {"rule": "user"}},
                                           evidence_artifact_ids=[evidence] if evidence else None)
                    sql = ("SELECT assertion_id FROM intelligence_edge_assertions WHERE source_artifact_id=? "
                           "AND target_artifact_id=? AND edge_type=? AND created_by=?")
                    for (created,) in db._conn.execute(sql, (source_id, target_id, edge_type, creator)).fetchall():
                        if created not in ids:
                            if created not in existing:
                                prior = state.capture(db, {"assertions": [created]})["assertions"]
                                before["assertions"].extend(prior)      # an earlier, rejected copy
                            ids.append(created)
                        db.set_edge_assertion_review_state(created, "verified")
                scope = {"assertions": ids}
                before["assertions"] = sorted({a["assertion_id"]: a for a in before["assertions"]}.values(),
                                              key=lambda a: a["assertion_id"])
                after = state.capture(db, scope)
                receipt = journal.save(db, source_id, "knowledge", {"scope": scope, "before": before, "after": after,
                                                                   "command": command or edge_type})
    return {"receipt": receipt, "row": db.get_artifact(source_id)}
