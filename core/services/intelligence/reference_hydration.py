"""Bounded, headless reference reads; alias validation is shared with navigation."""
from contextlib import nullcontext
from core.db.intelligence_artifact_aliases import resolve_current_id
from core.models.artifact_reference import ArtifactRef
from core.services.intelligence.artifact_references import project_reference

MAX_REFERENCE_BATCH = 32
MAX_MIGRATION_DEPTH = 32


class ReferenceReader:
    def __init__(self, db, references):
        self.db, self.rows, self.aliases = db, {}, {}
        visited = set()
        pending = {ref.artifact_id for ref in references if ref.artifact_id}
        for _ in range(MAX_MIGRATION_DEPTH):
            if not pending or db is None:
                break
            visited.update(pending)
            self.rows.update(db.get_artifacts_by_ids(pending))
            aliases = db.get_artifact_aliases(pending)
            self.aliases.update(aliases)
            pending = {alias['new_artifact_id'] for alias in aliases.values()
                       if alias['status'] == 'migrated' and alias.get('new_artifact_id')}
            pending.difference_update(visited)
        self.truncated = pending

    def get_artifact(self, identity):
        return self.rows.get(identity)

    def resolve_artifact_alias(self, identity):
        return self.aliases.get(identity)

    def resolve_current_artifact_id(self, identity):
        def signature(key):
            if key in self.truncated:
                raise ValueError("Artifact migration chain exceeds the read limit; review its correspondence.")
            row = self.rows.get(key)
            return (row['source_id'], row['artifact_type']) if row else None
        return resolve_current_id(self.db._conn, identity, get_artifact=signature,
                                  get_alias=self.resolve_artifact_alias)


def resolve_references(service, references, *, compact=False):
    if not isinstance(references, (list, tuple)) or len(references) > MAX_REFERENCE_BATCH:
        raise ValueError(f"Supply at most {MAX_REFERENCE_BATCH} references per batch.")
    refs = [ArtifactRef.from_value(value) for value in references]
    db = service.db
    lock = getattr(service.project_manager, 'db_lock', None)
    with db._write_lock if db else nullcontext():
        with lock if lock is not None else nullcontext():
            return _resolve(service, db, refs, compact)


def _resolve(service, db, refs, compact):
    reader = ReferenceReader(db, refs)
    sources = _sources(service.project_manager, {row['source_id'] for row in reader.rows.values()})
    results = [project_reference(reader, ref, sources) for ref in refs]
    if compact:
        for result in results:
            result.pop('reference', None)
            row = result.get('row')
            if row:
                result['row'] = {key: row.get(key) for key in ('artifact_id', 'source_id', 'revision', 'locator')}
                result['row']['locator'] = {key: row['locator'][key] for key in
                    ('page', 'start', 'end', 'char_start', 'char_end') if key in (row.get('locator') or {})}
    return results


def _sources(pm, identities):
    lookup = getattr(pm, 'get_source_records', None)
    if lookup:
        output = {}
        pending = list(identities)
        for offset in range(0, len(pending), MAX_REFERENCE_BATCH):
            output.update(lookup(pending[offset:offset + MAX_REFERENCE_BATCH]))
        return output
    # Compatibility for injected project providers; one discovery read per batch.
    return {row['id']: row for row in pm.list_sources(include_removed=True)} if hasattr(pm, 'list_sources') else {}
