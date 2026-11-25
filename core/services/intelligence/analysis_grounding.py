"""Shared current-source and revision checks for analysis evidence consumers."""
from core.models.analysis_focus import focus_rows
from core.models.intelligence_models import ArtifactQuery, SourceScope
from core.services.intelligence.artifact_eligibility import active_assertions, is_current_artifact

SOURCE_EVIDENCE_TYPES = frozenset({'sentence', 'transcript_segment', 'table'})


def reviewed_relations(db, node_ids, evidence_scope=None, *, reviewed_only=True):
    scope = set(node_ids if evidence_scope is None else evidence_scope)
    result, evidence_cache = [], {}
    for edge in db.list_edges_for_artifacts(node_ids):
        active = active_assertions(edge)
        reviewed = [a for a in active if a.get('review_state') in {'verified', 'corrected'}]
        assertions = reviewed if reviewed or reviewed_only else active
        evidence_ids = set()
        for assertion in assertions:
            identity = assertion.get('evidence_artifact_id')
            if identity not in evidence_cache:
                evidence_cache[identity] = db.get_artifact(identity) if identity else None
            row = evidence_cache[identity]
            if identity in scope and is_current_artifact(db, row):
                evidence_ids.add(identity)
        if evidence_ids:
            result.append({
                'id': edge['edge_id'], 'source': edge['source_artifact_id'],
                'target': edge['target_artifact_id'], 'type': edge['edge_type'],
                'evidence_ids': sorted(evidence_ids), 'verified': bool(reviewed), 'suggested': not reviewed,
                'assertion_ids': [a['assertion_id'] for a in assertions
                                  if a.get('evidence_artifact_id') in evidence_ids],
            })
    return result


def scoped_focus_rows(db, rows, source_ids, focus):
    allowed = set(source_ids)
    current = [row for row in rows if row.get('source_id') in allowed
               and is_current_artifact(db, row)]
    edges = reviewed_focus_edges(db, current) if focus else []
    return focus_rows(current, focus, edges)


def reviewed_focus_edges(db, rows):
    """Resolve reviewed unit edges to their current source sentences for packets."""
    parents = {row['artifact_id']: row for row in rows
               if row.get('artifact_type') in SOURCE_EVIDENCE_TYPES}
    if not parents:
        return []
    aliases = {identity: identity for identity in parents}
    query = ArtifactQuery(source_scope=SourceScope(source_ids=sorted(
        {row['source_id'] for row in parents.values()})), artifact_types=['argument_unit'])
    for unit in db.iter_artifacts(query):
        parent = unit.get('parent_artifact_id')
        if parent in parents and is_current_artifact(db, unit, parent_cache=parents):
            aliases[unit['artifact_id']] = parent
    return [{**edge, 'source': aliases[edge['source']], 'target': aliases[edge['target']],
             'evidence_ids': list(dict.fromkeys(aliases[value] for value in edge['evidence_ids']))}
            for edge in reviewed_relations(db, set(aliases))]


def artifact_snapshot(row):
    return {'revision': row.get('revision') or 1, 'payload_hash': row.get('payload_hash')}


class AnalysisEvidenceScope:
    """Resolve only supplied canonical evidence, never an arbitrary database ID."""

    def __init__(self, db, allowed_ids, *, source_ids=(), snapshots=None):
        self.db = db
        self.allowed_ids = set(allowed_ids)
        self.source_ids = set(source_ids)
        self.snapshots = dict(snapshots or {})
        self._cache = {}

    def resolve(self, identity):
        if not self.db or identity not in self.allowed_ids:
            return None
        if identity not in self._cache:
            self._cache[identity] = self.db.get_artifact(identity)
        row = self._cache[identity]
        if not row or row.get('artifact_type') not in SOURCE_EVIDENCE_TYPES:
            return None
        if self.source_ids and row.get('source_id') not in self.source_ids:
            return None
        if not is_current_artifact(self.db, row):
            return None
        expected = self.snapshots.get(identity)
        if expected and any(expected.get(key) is not None and expected[key] != actual
                            for key, actual in artifact_snapshot(row).items()):
            return None
        return row
