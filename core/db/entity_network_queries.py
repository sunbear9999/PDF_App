"""Keyset-paged evidence-backed entity assertions; no aggregate-edge truth shortcuts."""
from dataclasses import replace
import json
from core.db.intelligence_artifact_queries import compile_filters
from core.models.artifact_families import artifact_types_for_families

ACTIVE = "status='complete' AND review_state IN ('unreviewed','verified','corrected')"


def page(db, query, filters, after=''):
    # Scope/search apply to the evidence, not global canonical entity rows.
    evidence_query = replace(query, artifact_types=[], timeline_only=False, current_only=True,
        statuses=['complete'], review_states=['unreviewed', 'verified', 'corrected'])
    clauses, params = compile_filters(evidence_query, artifact_types_for_families, 'a')
    clauses.extend(["s.artifact_type='entity'", "t.artifact_type='entity'", 's.' + ACTIVE.replace(' AND review_state', ' AND s.review_state'),
        't.' + ACTIVE.replace(' AND review_state', ' AND t.review_state'),
        "r.review_state IN ('unreviewed','verified','corrected')", 'r.assertion_id>?',
        'r.assertion_id NOT IN (SELECT value FROM json_each(?))'])
    params.extend([after, json.dumps(filters.excluded_assertion_ids)])
    if filters.focus_entity_id:
        clauses.append('(r.source_artifact_id=? OR r.target_artifact_id=?)')
        params.extend([filters.focus_entity_id] * 2)
    if not filters.include_candidates:
        clauses.extend(["r.review_state IN ('verified','corrected')",
                        "r.edge_type NOT IN ('co_occurs_with','mentioned_in')", "r.edge_type NOT LIKE '%candidate%'"])
    db._conn.execute('CREATE INDEX IF NOT EXISTS idx_network_assertion_source ON intelligence_edge_assertions(source_artifact_id,assertion_id)')
    db._conn.execute('CREATE INDEX IF NOT EXISTS idx_network_assertion_target ON intelligence_edge_assertions(target_artifact_id,assertion_id)')
    rows = db._conn.execute('SELECT r.assertion_id,r.edge_id,r.source_artifact_id,r.target_artifact_id,r.edge_type,'
        'r.evidence_artifact_id,r.confidence,r.review_state,r.created_by,r.metadata_json FROM intelligence_edge_assertions r '
        'JOIN intelligence_artifacts a ON a.artifact_id=r.evidence_artifact_id '
        'JOIN intelligence_artifacts s ON s.artifact_id=r.source_artifact_id '
        'JOIN intelligence_artifacts t ON t.artifact_id=r.target_artifact_id WHERE ' + ' AND '.join(clauses)
        + ' ORDER BY r.assertion_id LIMIT ?', (*params, query.limit + 1)).fetchall()
    names = ['assertion_id', 'edge_id', 'source_artifact_id', 'target_artifact_id', 'edge_type',
             'evidence_artifact_id', 'confidence', 'review_state', 'created_by', 'metadata_json']
    return [dict(zip(names, row)) for row in rows[:query.limit]], len(rows) > query.limit
