"""Independent bounded network explorer over current assertions and source evidence."""
from dataclasses import asdict, replace
import json

from core.services.intelligence.link_explanations import explain
from core.db import browse_cursors
from core.db.intelligence_artifact_queries import coerce_query, fingerprint, revision_token
from core.db.entity_network_queries import page
from core.db.event_projection_queries import read_artifacts, resolve_entity
from core.models.intelligence_models import ArtifactQuery
from core.models.network_projection import coerce_network_filter


def query_network(service, query=None, filters=None, *, cancelled=lambda: False):
    db = service.db
    if db is None:
        raise ValueError('Open a project before exploring entity connections.')
    query, filters = coerce_query(query or ArtifactQuery()), coerce_network_filter(filters)
    if type(query.limit) is not int or not 1 <= query.limit <= 200:
        raise ValueError('Choose between 1 and 200 connection assertions.')
    if filters.focus_entity_id:
        identity = resolve_entity(db, filters.focus_entity_id, {filters.focus_entity_id: filters.focus_entity_id})
        filters = replace(filters, focus_entity_id=identity)
    revision = [id(db), id(db._conn), revision_token(db)]
    binding = fingerprint(query, json.dumps(['network_v1', asdict(filters)], sort_keys=True))
    try:
        position = browse_cursors.unpack(query.cursor, binding, revision)
    except ValueError as exc:
        return {'items': [], 'next_cursor': '', 'invalidated': True, 'reason': str(exc)}
    if cancelled():
        return {'items': [], 'next_cursor': '', 'cancelled': True}
    assertions, more = page(db, query, filters, position.get('after', ''))
    records = read_artifacts(db, {row[key] for row in assertions
        for key in ('source_artifact_id', 'target_artifact_id', 'evidence_artifact_id')})
    items = [project(row, records) for row in assertions]
    if cancelled() or revision != [id(db), id(db._conn), revision_token(db)]:
        return {'items': [], 'next_cursor': '', 'invalidated': True, 'reason': 'Knowledge changed during network reading.'}
    nodes = {row[key] for row in assertions for key in ('source_artifact_id', 'target_artifact_id')}
    return {'items': items, 'network': items, 'artifacts': list(records.values()),
        'nodes': [records[key] for key in sorted(nodes)], 'focus_entity_id': filters.focus_entity_id,
        'next_cursor': browse_cursors.pack({'after': assertions[-1]['assertion_id']}, binding, revision) if more else '',
        'page_count': len(items), 'snapshot_revision': revision[-1],
        'diagnostics': ['Each connection has current source evidence. Co-occurrence and candidate links never imply an accepted semantic relationship. The diagram shows this page only.']}


def project(row, records):
    evidence = records[row['evidence_artifact_id']]
    source, target = records[row['source_artifact_id']], records[row['target_artifact_id']]
    kind = row['edge_type']
    observed = kind in {'co_occurs_with', 'mentioned_in'}
    accepted = not observed and 'candidate' not in kind and row['review_state'] in {'verified', 'corrected'}
    status = 'Co-occurrence observation' if observed else 'Accepted relationship' if accepted else 'Candidate relationship'
    metadata = json.loads(row['metadata_json'] or '{}')
    reason = explain({**row, 'metadata': metadata}, source, target)
    return {**evidence, 'assertion_id': row['assertion_id'], 'edge_id': row['edge_id'],
        'source_entity_id': source['artifact_id'], 'target_entity_id': target['artifact_id'],
        'source_label': source['exact_text'], 'target_label': target['exact_text'],
        'relation_type': kind, 'relation_state': status, 'assertion_review_state': row['review_state'],
        'relation_explanation': str(reason)[:4000], 'relation_confidence': row['confidence'],
        'display_text': f"{source['exact_text']} → {target['exact_text']}: {kind}",
        'explanation': status + ': ' + str(reason)[:4000]}
