"""Optimistic, transactional review of assertions, entity identity and source migrations."""
import json
from contextlib import nullcontext
from core.models.review_decisions import ReviewDecisionKind, validate_payload
from core.db import intelligence_decision_journal as journal
from core.db import intelligence_decision_state as state
from core.services.intelligence import entity_review
from core.services.intelligence.review_migration import ReviewMigration
from core.services.intelligence.artifact_eligibility import is_current_artifact
from core.services.intelligence.link_explanations import explain

COMMANDS = frozenset(item.value for item in ReviewDecisionKind)


def prepare(service, artifact_id, command, payload=None):
    payload = validate_payload(command, payload)
    if command not in COMMANDS:
        raise ValueError('Choose a registered review decision.')
    row = service.db.get_artifact(artifact_id)
    if command.startswith('entity_'):
        scope, data = entity_review.prepare(service, row, command, payload)
    elif command.startswith('relation_'):
        scope, data = prepare_relation(service, artifact_id, command)
    else:
        scope, data = prepare_migration(service, row, payload)
    if command.startswith('entity_') or command.startswith('relation_'):
        for key in ('source','target'):
            row = data.get(key)
            if command.startswith('relation_') and (row or {}).get('artifact_type') != 'entity':
                continue  # argument units and sentences are their own passage
            related = representative_context(service,row,data.get('mentions') if key == 'source' else None)
            data[key+'_context'] = related
            if related and related['artifact_id'] not in scope['artifacts']:
                scope['artifacts'].append(related['artifact_id'])
    return scope, data


def preview(service, artifact_id, command, payload=None):
    scope, data = prepare(service, artifact_id, command, payload)
    before = state.capture(service.db, scope)
    assertion = dict(data.get('assertion') or {})
    if assertion:
        assertion['metadata'] = json.loads(assertion.pop('metadata_json','{}') or '{}')
    evidence = service.db.get_artifact(assertion.get('evidence_artifact_id')) if assertion.get('evidence_artifact_id') else None
    blocked = ''
    if command.startswith('relation_'):
        try:
            prepare_relation(service,artifact_id,'relation_verify')
        except ValueError as exc:
            blocked = str(exc)
    explanation = explain(assertion, data.get('source'), data.get('target')) if assertion else ''
    return {'can_confirm':not bool(blocked), 'blocked_reason':blocked, 'token':state.digest(before), 'command':command, 'source':data.get('source'), 'target':data.get('target'),
            'source_context':data.get('source_context'), 'target_context':data.get('target_context'), 'explanation':explanation,
            'assertion':assertion, 'evidence':evidence, 'affected_count':len(data.get('mentions') or []),
            'project_identity':[id(service.db), id(service.db._conn)]}


def execute(service, artifact_id, command, *, payload=None, expected_token=None, receipt=None, expected_project=None):
    db = service.db
    if not db or not db._conn:
        raise ValueError('Open a project before reviewing knowledge.')
    payload = validate_payload(command, {} if command == "undo" else payload)
    if command == 'migration_resolve' and not payload.get('correspondence_confirmed'):
        raise ValueError('Confirm that the replacement span is the same reviewed observation.')
    lock = getattr(service.project_manager, 'db_lock', None)
    with db._write_lock:
        with lock if lock is not None else nullcontext():
            if expected_project is not None and tuple(expected_project) != (id(service.db), id(db._conn)):
                raise ValueError('Project changed; the decision was cancelled.')
            with journal.transaction(db):
                if command == 'undo':
                    return undo(service, artifact_id, receipt)
                scope, data = prepare(service, artifact_id, command, payload)
                before = state.capture(db, scope)
                if expected_token is not None and state.digest(before) != expected_token:
                    raise ValueError('The reviewed records changed. Reload before deciding.')
                target = apply(service, artifact_id, command, payload or {}, data, scope)
                if command == 'entity_split':
                    before['artifacts'][target] = None
                    before['parents'][target] = []
                after = state.capture(db, scope)
                saved = journal.save(db, artifact_id, 'knowledge', {'scope':scope,'before':before,'after':after,'command':command})
                return {'row':db.get_artifact(artifact_id) or data.get('source'), 'receipt':saved, 'target_artifact_id':target,
                        'ok':True, 'moved_mentions':len(data.get('mentions') or [])}


def merge_group(service, source_ids, target_id):
    """Merge several entities into one as a single decision: one receipt, one undo.
    Separate decisions could not be undone in turn, because each later merge changes
    the shared target. Any refusal (different types, a never-merge rule) changes nothing."""
    db = service.db
    sources = [identity for identity in dict.fromkeys(source_ids) if identity != target_id]
    if not db or not db._conn or not sources:
        raise ValueError('Choose at least two entities to merge.')
    lock = getattr(service.project_manager, 'db_lock', None)
    with db._write_lock:
        with lock if lock is not None else nullcontext():
            with journal.transaction(db):
                union = _union([prepare(service, identity, 'entity_merge', {'target_id': target_id})[0] for identity in sources])
                before = state.capture(db, union)
                moved = 0
                for identity in sources:
                    scope, data = prepare(service, identity, 'entity_merge', {'target_id': target_id})
                    apply(service, identity, 'entity_merge', {'target_id': target_id}, data, scope)
                    moved += len(data.get('mentions') or [])
                after = state.capture(db, union)
                saved = journal.save(db, target_id, 'knowledge', {'scope': union, 'before': before, 'after': after,
                                                                  'command': 'entity_merge_group'})
                return {'row': db.get_artifact(target_id), 'receipt': saved, 'merged': len(sources), 'moved_mentions': moved}


def _union(scopes):
    merged = {}
    for scope in scopes:
        for key, values in scope.items():
            bucket = merged.setdefault(key, [])
            for value in values:
                value = list(value) if isinstance(value, tuple) else value
                if value not in bucket:
                    bucket.append(value)
    return merged


def apply(service, identity, command, payload, data, scope):
    if command.startswith('entity_'):
        return entity_review.apply(service, command, payload, data, scope)
    if command.startswith('relation_'):
        service.db.set_edge_assertion_review_state(identity, 'verified' if command == 'relation_verify' else 'rejected')
        return (data.get('target') or {}).get('artifact_id','')
    old, target = data['source'], data['target']
    ReviewMigration(service)._transfer(old, target)
    metadata = {**(old.get('metadata') or {}), 'migration_conflict':None}
    service.revise_artifact_fields(identity, {'metadata':metadata,'status':'superseded'})
    return target['artifact_id']


def undo(service, artifact_id, receipt):
    db = service.db
    saved = journal.load(db, receipt, artifact_id, 'knowledge')
    current = state.capture(db, saved['scope'])
    if state.digest(current) != state.digest(saved['after']):
        raise ValueError('Newer changes prevent undo. Reload and inspect history.')
    state.restore(service, saved['scope'], saved['before'], saved['after'])
    journal.finish_undo(db, receipt)
    return {'ok':True, 'row':db.get_artifact(artifact_id), 'receipt':None}


def prepare_relation(service, identity, command):
    db = service.db
    assertion = state.assertion(db, identity)
    if not assertion:
        raise ValueError('The relation assertion is no longer available.')
    source = db.get_artifact(assertion['source_artifact_id'])
    target = db.get_artifact(assertion['target_artifact_id'])
    identities = [assertion['source_artifact_id'], assertion['target_artifact_id']]
    if assertion.get('evidence_artifact_id'):
        identities.append(assertion['evidence_artifact_id'])
    if command == 'relation_verify':
        for artifact_id in identities:
            artifact = entity_review.active(db.get_artifact(artifact_id))
            if not is_current_artifact(db,artifact):
                raise ValueError('The relation uses an obsolete argument assessment. Reprocess or reject it.')
    return {'artifacts':identities, 'assertions':[identity]}, {'assertion':assertion,'source':source,'target':target}


def prepare_migration(service, source, payload):
    db = service.db
    if not source or not (source.get('metadata') or {}).get('migration_conflict'):
        raise ValueError('Choose an unresolved source migration.')
    target = entity_review.active(db.get_artifact(payload.get('target_id')))
    if source['artifact_id'] == target['artifact_id'] or source.get('source_id') != target.get('source_id') or source['artifact_type'] != target['artifact_type']:
        raise ValueError('Choose a different span of the same type from the same source.')
    if target.get('review_state') != 'unreviewed' or (target.get('metadata') or {}).get('migration_conflict'):
        raise ValueError('The candidate already has a review. Resolve its assessment separately before migrating.')
    producer = (target.get('metadata') or {}).get('producer') or {}
    revision = db.current_source_revision(target.get('source_id'))
    if revision and producer.get('source_revision') and revision != producer['source_revision']:
        raise ValueError('The candidate belongs to an older source revision. Reprocess the source first.')
    if source['artifact_type'] == 'in_text_citation':
        assignment = (source.get('metadata') or {}).get('assigned_bibliography_artifact_id')
        ReviewMigration(service).assignment_target(assignment,target.get('source_id'))
    identities = [source['artifact_id'], target['artifact_id']]
    # Citation reassignment changes assertion state as well as semantic fields.
    return {'artifacts':identities,'aliases':[source['artifact_id']],'edge_sources':[target['artifact_id']]}, {'source':source,'target':target}


def representative_context(service, row, selected=None):
    if not row:
        return None
    if selected:
        mention = service.db.get_artifact(selected[0])
    else:
        mentions = service.db.list_entity_mentions_for_canonical(row['artifact_id'],limit=1)
        mention = next(iter(mentions),None)
    if not mention:
        return None
    evidence = (mention.get('metadata') or {}).get('evidence_artifact_id')
    return (service.db.get_artifact(evidence) if evidence else None) or mention
