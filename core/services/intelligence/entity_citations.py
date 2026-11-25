"""An entity that is also a way of citing a source (two roles at once).

"LCN" is an organisation, and in this book every mention of it cites the LCN
bibliography entry. The entity keeps its type; a human-made ``cites_source`` link
from the entity to the bibliography entry adds the second role. Its mentions then
count as references to that entry (Citations tab, hover card, inspector). The link
is on the canonical entity, whose id is stable across reruns, and is human-verified,
so reprocessing keeps it. Setting or clearing it is one decision with undo.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.models.intelligence_models import ArtifactQuery, SourceScope
from core.services.intelligence.entity_user_links import set_link

EDGE_TYPE = "cites_source"
CREATOR = "user:entity_citation"
MAX_ENTRY_CHOICES = 50



def cited_entries(db, entity_ids) -> Dict[str, Dict[str, Any]]:
    """entity id -> the bibliography entry it cites (current links only)."""
    if not entity_ids or not db._conn:
        return {}
    rows = db._conn.execute(
        "SELECT a.source_artifact_id, e.artifact_id, e.exact_text FROM intelligence_edge_assertions a "
        "JOIN intelligence_artifacts e ON e.artifact_id=a.target_artifact_id WHERE a.edge_type=? "
        "AND a.review_state IN ('verified','corrected') AND a.source_artifact_id IN (SELECT value FROM json_each(?))",
        (EDGE_TYPE, json.dumps(list(entity_ids)))).fetchall()
    return {source: {"artifact_id": entry, "exact_text": text or ""} for source, entry, text in rows}


def citing_entities(db, entry_ids) -> Dict[str, List[Dict[str, Any]]]:
    """bibliography entry id -> entities whose mentions cite it, with their mention counts."""
    if not entry_ids or not db._conn:
        return {}
    rows = db._conn.execute(
        "SELECT a.target_artifact_id, n.artifact_id, n.exact_text, json_extract(n.metadata_json,'$.mention_count') "
        "FROM intelligence_edge_assertions a JOIN intelligence_artifacts n ON n.artifact_id=a.source_artifact_id "
        "WHERE a.edge_type=? AND a.review_state IN ('verified','corrected') "
        "AND a.target_artifact_id IN (SELECT value FROM json_each(?))", (EDGE_TYPE, json.dumps(list(entry_ids)))).fetchall()
    found: Dict[str, List[Dict[str, Any]]] = {}
    for entry, entity, text, count in rows:
        found.setdefault(entry, []).append({"artifact_id": entity, "exact_text": text or "", "mention_count": count or 0})
    return found


def entry_choices(service, text: str = "", source_id: str = "") -> List[Dict[str, Any]]:
    """Bibliography entries to choose from, searched by text."""
    query = ArtifactQuery(artifact_types=["citation_entry"], text=text or "", limit=MAX_ENTRY_CHOICES,
                          source_scope=SourceScope(source_ids=[source_id] if source_id else []),
                          statuses=["complete"], review_states=["unreviewed", "verified", "corrected"],
                          current_only=True, order_by="alpha")
    return service.db.query_artifacts(query).get("items") or []


def set_cited_entry(service, entity_id: str, entry_id: Optional[str]) -> Dict[str, Any]:
    """Make ``entity_id`` cite ``entry_id`` (or stop citing anything when ``entry_id`` is None)."""
    db = service.db
    entity = db.get_artifact(entity_id)
    if not entity or entity.get("artifact_type") != "entity":
        raise ValueError("Choose an entity.")
    if entry_id:
        entry = db.get_artifact(entry_id)
        if not entry or entry.get("artifact_type") != "citation_entry":
            raise ValueError("Choose a bibliography entry.")
    result = set_link(service, entity_id, entry_id, EDGE_TYPE, CREATOR, command="entity_cites_source")
    return {**result, "cited": cited_entries(db, [entity_id]).get(entity_id)}
