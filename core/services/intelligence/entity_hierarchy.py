"""Subgroups ("Yucatec Maya" under "Maya") and connections chosen instead of a merge.

A subgroup stays its own entity with its own mentions and type; a ``subgroup_of`` link puts it
under a broader entity, whose list row then shows its own mentions and its subgroups. An entity
has at most one broader entity; cycles are refused. "Connect instead" records a typed,
confirmed connection between two entities that are related but not the same (a person and the
town named after them), on a sentence naming both when there is one.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.services.intelligence.entity_user_links import current, pointing_at, set_link

EDGE_TYPE = "subgroup_of"
CREATOR = "user:entity_hierarchy"
CONNECT_CREATOR = "user:entity_connection"
MAX_DEPTH = 32


def parents(db, entity_ids) -> Dict[str, Dict[str, Any]]:
    return current(db, entity_ids, EDGE_TYPE)


def subgroups(db, entity_ids):
    return pointing_at(db, entity_ids, EDGE_TYPE)


def _entity(db, identity):
    row = db.get_artifact(identity) if identity else None
    if not row or row.get("artifact_type") != "entity" or row.get("status") != "complete":
        raise ValueError("Choose a current entity.")
    return row


def set_parent(service, child_id: str, parent_id: Optional[str]) -> Dict[str, Any]:
    """Put ``child_id`` under ``parent_id`` (None takes it out of its group)."""
    db = service.db
    _entity(db, child_id)
    if parent_id:
        _entity(db, parent_id)
        ancestor, depth = parent_id, 0
        while ancestor and depth < MAX_DEPTH:          # the parent may not sit under the child
            if ancestor == child_id:
                raise ValueError("That would put an entity inside its own subgroup.")
            ancestor = (parents(db, [ancestor]).get(ancestor) or {}).get("artifact_id")
            depth += 1
    return set_link(service, child_id, parent_id, EDGE_TYPE, CREATOR, command="entity_subgroup")


def shared_sentence(db, first_id: str, second_id: str) -> Optional[str]:
    row = db._conn.execute(
        "SELECT json_extract(a.metadata_json,'$.evidence_artifact_id') FROM intelligence_artifacts a "
        "JOIN intelligence_artifacts b ON json_extract(b.metadata_json,'$.evidence_artifact_id')="
        "json_extract(a.metadata_json,'$.evidence_artifact_id') WHERE a.artifact_type='entity_mention' "
        "AND b.artifact_type='entity_mention' AND a.parent_artifact_id=? AND b.parent_artifact_id=? "
        "AND json_extract(a.metadata_json,'$.evidence_artifact_id') IS NOT NULL LIMIT 1",
        (first_id, second_id)).fetchone()
    return row[0] if row else None


def connect(service, first_id: str, second_id: str, edge_type: str) -> Dict[str, Any]:
    """A confirmed typed connection between two different entities (one undoable decision)."""
    from core.services.intelligence.connection_review import CONNECTION_TYPES
    db = service.db
    _entity(db, first_id)
    _entity(db, second_id)
    if first_id == second_id:
        raise ValueError("Choose two different entities.")
    if edge_type not in CONNECTION_TYPES:
        raise ValueError("Choose a registered connection type.")
    return set_link(service, first_id, second_id, edge_type, CONNECT_CREATOR, single=False,
                    evidence=shared_sentence(db, first_id, second_id), command="entity_connect")


def decided_pairs(db):
    """Pairs a person already related (subgroup or confirmed connection): never suggested as duplicates."""
    from core.services.intelligence.connection_review import CONNECTION_TYPES
    kinds = [EDGE_TYPE, *CONNECTION_TYPES]
    rows = db._conn.execute(
        "SELECT source_artifact_id, target_artifact_id FROM intelligence_edge_assertions WHERE review_state IN "
        f"('verified','corrected') AND edge_type IN ({','.join('?' for _ in kinds)})", kinds).fetchall()
    return {tuple(sorted(row)) for row in rows}


SUBGROUP_MENTION_PREVIEW = 20


def hydrate(db, items, columns) -> None:
    """Add ``parent_entity`` and ``subgroups`` (each with a preview of its own mentions) to entity rows."""
    from core.db.mention_snippets import attach
    ids = [item["artifact_id"] for item in items]
    above, below = parents(db, ids), subgroups(db, ids)
    full = {row["artifact_id"]: row for row in db.list_artifacts_by_ids(
        [child["artifact_id"] for group in below.values() for child in group])}
    below = {parent: [{**full[c["artifact_id"]], "mention_count": c["mention_count"]} for c in group if c["artifact_id"] in full]
             for parent, group in below.items()}
    children = [child for group in below.values() for child in group]
    for child in children:
        rows = db._conn.execute(
            f"SELECT {columns} FROM intelligence_artifacts WHERE artifact_type='entity_mention' AND parent_artifact_id=? "
            "AND status='complete' AND review_state IN ('unreviewed','verified','corrected') "
            "ORDER BY updated_at DESC, artifact_id DESC LIMIT ?", (child["artifact_id"], SUBGROUP_MENTION_PREVIEW)).fetchall()
        child["mentions"] = [db._artifact_row_to_dict(row) for row in rows]
        child["mentions_truncated"] = int(child.get("mention_count") or 0) > len(child["mentions"])
    attach(db, [m for child in children for m in child["mentions"]])
    for item in items:
        meta = dict(item.get("metadata") or {})
        if item["artifact_id"] in above:
            meta["parent_entity"] = {k: above[item["artifact_id"]][k] for k in ("artifact_id", "exact_text")}
        if item["artifact_id"] in below:
            item["subgroups"] = below[item["artifact_id"]]
        item["metadata"] = meta
