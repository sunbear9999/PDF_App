"""One effective bibliography assignment for projections and scoped queries."""
import json


def effective_target(alias):
    manual = f"json_extract({alias}.metadata_json, '$.assigned_bibliography_artifact_id')"
    active = "('unreviewed','verified','corrected')"
    target = ("t.artifact_type='citation_entry' AND t.status='complete' AND t.review_state IN " + active +
              f" AND t.source_id={alias}.source_id")
    return (f"CASE WHEN json_type({alias}.metadata_json, '$.assigned_bibliography_artifact_id') IS NOT NULL "
            f"THEN (SELECT t.artifact_id FROM intelligence_artifacts t WHERE t.artifact_id={manual} AND {target}) "
            "ELSE (SELECT e.target_artifact_id FROM intelligence_artifact_edges e "
            "JOIN intelligence_artifacts t ON t.artifact_id=e.target_artifact_id "
            "JOIN intelligence_edge_assertions claim ON claim.edge_id=e.edge_id "
            f"WHERE e.source_artifact_id={alias}.artifact_id AND e.edge_type='cites' AND {target} "
            f"AND claim.review_state IN {active} "
            "ORDER BY COALESCE(claim.confidence,0) DESC, e.target_artifact_id DESC LIMIT 1) END")


def assignments(db, source_id=None, artifact_ids=None):
    if not db._conn or artifact_ids == []:
        return {}
    clauses = ["c.artifact_type='in_text_citation'", "c.status='complete'"]
    params = []
    if source_id:
        clauses.append("c.source_id=?")
        params.append(source_id)
    if artifact_ids is not None:
        clauses.append("c.artifact_id IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(list(artifact_ids)))
    rows = db._conn.execute("SELECT c.artifact_id, " + effective_target("c") +
                           " FROM intelligence_artifacts c WHERE " + " AND ".join(clauses), params)
    return dict(rows.fetchall())
