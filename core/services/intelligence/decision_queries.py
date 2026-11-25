"""Bounded candidate and assertion review queries, independent of GUI projections."""
import json

from core.models.entity_categories import MEASURE_TYPES
from core.models.intelligence_models import ArtifactQuery, SourceScope
from core.db import intelligence_decision_state as state

# Structural ownership/navigation links are reviewed through their typed editors.
STRUCTURAL_EDGES = ('mentions','mention_of','mentioned_in','cites','part_of','located_in','annotates','comments_on',
                    'links_to','refers_to_note','refers_to','cites_source','subgroup_of')
# Observations, not proposals: "named in the same sentence" is shown in the Entity Network,
# where it can be confirmed as a real link; it would bury proposals in the review queue.
OBSERVED_EDGES = ('co_occurs_with',)


def candidates(service, artifact_id, command, *, text='', cursor='', target_id=None):
    row = service.db.get_artifact(artifact_id)
    if not row:
        raise ValueError('The reviewed artifact is no longer available.')
    migration = command == 'migration_resolve'
    query = ArtifactQuery(artifact_types=[row['artifact_type']], statuses=['complete'],
        review_states=['unreviewed'] if migration else ['unreviewed','verified','corrected'],
        source_scope=SourceScope(source_ids=[row['source_id']]) if migration else SourceScope(),
        # Merging needs the same type; a subgroup or a connection may cross types (a person and a town).
        entity_types=[] if migration or command in {'entity_subgroup','entity_connect'} else [(row.get('metadata') or {}).get('entity_type') or 'unknown'],
        exclude_artifact_ids=[artifact_id], text=text, cursor=cursor, limit=50)
    page = service.query_artifacts(query)
    selected = service.db.get_artifact(target_id) if target_id else None
    if selected and selected.get('status') == 'complete' and selected.get('artifact_type') == row['artifact_type'] and selected.get('review_state') in query.review_states:
        eligible = selected.get('source_id') == row.get('source_id') if migration else (not query.entity_types or (selected.get('metadata') or {}).get('entity_type') in query.entity_types)
        if eligible and target_id != artifact_id and target_id not in {item['artifact_id'] for item in page['items']}:
            page['items'].insert(0, selected)
    return {**page, 'row':row}


def relation_queue(service, spec, source_id, excluded, entity_types=()):
    clauses = ["a.review_state='unreviewed'", "a.assertion_id NOT IN (SELECT value FROM json_each(?))",
               # Links between two entities are reviewed in Review connections (sentence view).
               "NOT (COALESCE(s.artifact_type,'')='entity' AND COALESCE(t.artifact_type,'')='entity')"]
    params = [json.dumps(excluded)]
    # Dates and measures are not linked as names (Entities 4.7); older links to them are not proposed.
    for end in ('s', 't'):
        clauses.append(f"COALESCE(json_extract({end}.metadata_json,'$.entity_type'),'') NOT IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(sorted(MEASURE_TYPES)))
    if entity_types:
        clauses.append("(json_extract(s.metadata_json,'$.entity_type') IN (SELECT value FROM json_each(?)) OR "
                       "json_extract(t.metadata_json,'$.entity_type') IN (SELECT value FROM json_each(?)))")
        params.extend([json.dumps(list(entity_types))] * 2)
    if source_id:
        clauses.append('(s.source_id=? OR t.source_id=? OR e.source_id=?)')
        params.extend([source_id]*3)
    edge_types = spec.query.get('edge_types') or []
    if edge_types:
        clauses.append('a.edge_type IN (SELECT value FROM json_each(?))')
        params.append(json.dumps(edge_types))
    else:
        clauses.append('a.edge_type NOT IN (SELECT value FROM json_each(?))')
        params.append(json.dumps(list(STRUCTURAL_EDGES) + list(OBSERVED_EDGES)))
    # Missing/stale endpoints remain reviewable for rejection, never verification.
    sql = (' FROM intelligence_edge_assertions a LEFT JOIN intelligence_artifacts s ON s.artifact_id=a.source_artifact_id '
           'LEFT JOIN intelligence_artifacts t ON t.artifact_id=a.target_artifact_id '
           'LEFT JOIN intelligence_artifacts e ON e.artifact_id=a.evidence_artifact_id WHERE ' + ' AND '.join(clauses))
    conn = service.db._conn
    count = conn.execute('SELECT COUNT(*)' + sql, params).fetchone()[0]
    first = conn.execute('SELECT a.assertion_id' + sql + ' ORDER BY a.created_at,a.assertion_id LIMIT 1', params).fetchone()
    return (state.assertion(service.db, first[0]) if first else None), count
