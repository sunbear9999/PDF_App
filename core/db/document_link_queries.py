"""Indexed, source-local smart-link observations. Called on a worker."""
import json


def sources(db, source_id, pages):
    if not db._conn or not pages:
        return []
    placeholders = ",".join("?" for _ in pages)
    rows = db._conn.execute(
        "SELECT artifact_id,source_id,locator_json,metadata_json FROM intelligence_artifacts "
        "WHERE source_id=? AND json_extract(locator_json,'$.page') IN (" + placeholders + ") "
        "AND artifact_type IN ('note_reference','in_text_citation','toc_entry','cross_reference') "
        "AND status='complete' AND review_state NOT IN ('rejected','superseded')",
        (source_id, *pages)).fetchall()
    return [{"artifact_id": r[0], "source_id": r[1], "locator": json.loads(r[2] or "{}")}
            for r in rows if not json.loads(r[3] or "{}").get("migration_conflict")]


def entity_mentions(db, source_id, pages):
    """Entity mentions on these pages (sentence-relative offsets; see mention_coordinates)."""
    if not db._conn or not pages:
        return []
    placeholders = ",".join("?" for _ in pages)
    rows = db._conn.execute(
        "SELECT artifact_id,parent_artifact_id,locator_json,metadata_json,exact_text FROM intelligence_artifacts "
        "WHERE source_id=? AND artifact_type='entity_mention' AND json_extract(locator_json,'$.page') IN ("
        + placeholders + ") AND status='complete' AND review_state NOT IN ('rejected','superseded')",
        (source_id, *pages)).fetchall()
    return [{"artifact_id": r[0], "parent_artifact_id": r[1], "locator": json.loads(r[2] or "{}"),
             "metadata": json.loads(r[3] or "{}"), "exact_text": r[4]} for r in rows]


def entity_positions(db, source_id, canonical_ids):
    """Ids and positions of this source's mentions of these entities (no full rows: a frequent
    name has thousands of mentions)."""
    if not db._conn or not canonical_ids:
        return []
    placeholders = ",".join("?" for _ in canonical_ids)
    rows = db._conn.execute(
        "SELECT artifact_id,parent_artifact_id,locator_json FROM intelligence_artifacts "
        "WHERE parent_artifact_id IN (" + placeholders + ") AND source_id=? AND +artifact_type='entity_mention' "
        "AND +status='complete' AND review_state NOT IN ('rejected','superseded')",
        (*canonical_ids, source_id)).fetchall()
    return [{"artifact_id": r[0], "parent_artifact_id": r[1], "locator": json.loads(r[2] or "{}")} for r in rows]
