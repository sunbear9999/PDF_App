"""Separate current reading results from retained extraction history."""


def counts(connection, source_id=None):
    if connection is None:
        return {}
    rows = connection.execute(
        "SELECT artifact_type, status, COUNT(*) FROM intelligence_artifacts "
        "WHERE (? IS NULL OR source_id=?) GROUP BY artifact_type, status", (source_id, source_id)).fetchall()
    by_type, by_status = {}, {}
    for kind, status, count in rows:
        by_type[kind] = by_type.get(kind, 0) + count
        by_status[status] = by_status.get(status, 0) + count
    active = ("status='complete' AND review_state IN ('unreviewed','verified','corrected') "
              "AND json_extract(metadata_json,'$.migration_conflict') IS NULL")
    current = dict(connection.execute(
        "SELECT artifact_type,COUNT(*) FROM intelligence_artifacts "
        "WHERE (? IS NULL OR source_id=?) AND " + active + " GROUP BY artifact_type",
        (source_id, source_id)).fetchall())
    canonical = connection.execute(
        "SELECT COUNT(DISTINCT m.parent_artifact_id) FROM intelligence_artifacts m "
        "JOIN intelligence_artifacts e ON e.artifact_id=m.parent_artifact_id "
        "WHERE m.artifact_type='entity_mention' AND e.artifact_type='entity' "
        "AND (? IS NULL OR m.source_id=?) "
        "AND m.status='complete' AND e.status='complete' "
        "AND m.review_state IN ('unreviewed','verified','corrected') "
        "AND e.review_state IN ('unreviewed','verified','corrected') "
        "AND json_extract(m.metadata_json,'$.migration_conflict') IS NULL "
        "AND json_extract(e.metadata_json,'$.migration_conflict') IS NULL", (source_id, source_id)).fetchone()[0]
    return {"total": sum(by_type.values()), "by_type": by_type, "by_status": by_status,
            "current_total": sum(current.values()), "current_by_type": current,
            "canonical_entities": canonical}
