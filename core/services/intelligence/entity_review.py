"""Explicit canonical-entity decisions shared by every caller."""
import uuid
import json
from core.db.intelligence_entity_resolution import group_keys
from core.db import intelligence_decision_state as state
from core.models.intelligence_models import ArtifactRecord
from core.models.producer_models import human


def active(row, kind=None):
    if not row or (kind and row.get('artifact_type') != kind) or row.get('status') != 'complete' or row.get('review_state') in {'rejected','superseded'}:
        raise ValueError('Choose a current eligible artifact of the required type.')
    return row


def key(row):
    return row.get('normalized_key') or row.get('normalized_text') or row.get('artifact_id')


def prepare(service, source, command, payload):
    db = service.db
    active(source, 'entity')
    kind = (source.get('metadata') or {}).get('entity_type')
    if not kind:
        raise ValueError('Classify the entity before changing its grouping.')
    target = None
    rules = [('alias', kind, key(source)), ('never_merge', kind, key(source))]
    identities = list(dict.fromkeys(payload.get('mention_ids') or []))
    if command == 'entity_split':
        if not identities or not str(payload.get('display_name') or '').strip():
            raise ValueError('Choose mentions from this entity and a name for the new entity.')
        for identity in identities:
            row = active(db.get_artifact(identity), 'entity_mention')
            if row.get('parent_artifact_id') != source['artifact_id']:
                raise ValueError('Every selected mention must belong to the entity being split.')
    else:
        target = active(db.get_artifact(payload.get('target_id')), 'entity')
        if target['artifact_id'] == source['artifact_id'] or (target.get('metadata') or {}).get('entity_type') != kind:
            raise ValueError('Choose different entities with the same category.')
        source_keys = group_keys(db._conn,kind,source['artifact_id'])
        target_keys = group_keys(db._conn,kind,target['artifact_id'])
        rules += [('never_merge',kind,value) for value in sorted(set(source_keys+target_keys)-{key(source)})]
        if command == 'entity_merge':
            blocked = db._conn.execute("SELECT 1 FROM intelligence_entity_rules WHERE rule_type='never_merge' AND entity_type=? AND ((source_key IN (SELECT value FROM json_each(?)) AND other_key IN (SELECT value FROM json_each(?))) OR (source_key IN (SELECT value FROM json_each(?)) AND other_key IN (SELECT value FROM json_each(?))))", (kind,json.dumps(source_keys),json.dumps(target_keys),json.dumps(target_keys),json.dumps(source_keys))).fetchone()
            if blocked:
                raise ValueError('A Never merge decision protects this pair. Undo it before merging.')
            identities = state.mention_ids(db, source['artifact_id'])
    rules += [('mention_parent', kind, identity) for identity in identities]
    scope = {'artifacts':[source['artifact_id'], *([target['artifact_id']] if target else []), *identities],
             'rules':rules, 'aliases':[source['artifact_id']], 'mention_edges':identities,
             'edge_sources':[source['artifact_id'], *([target['artifact_id']] if target else [])],
             'edge_targets':[source['artifact_id'], *([target['artifact_id']] if target else [])],
             'parents':[source['artifact_id'], *([target['artifact_id']] if target else [])]}
    return scope, {'source':source, 'target':target, 'mentions':identities, 'entity_type':kind}


def apply(service, command, payload, prepared, scope):
    db = service.db
    source, target, kind = prepared['source'], prepared['target'], prepared['entity_type']
    if command == 'entity_never_merge':
        db.set_entity_rule('never_merge', kind, key(source), other_key=key(target), metadata={'created_by':'user'})
        db._bump_knowledge_revision(None, 'structure')
        return target['artifact_id']
    if command == 'entity_split':
        identity = 'entity:user:' + str(uuid.uuid4())
        scope['artifacts'].append(identity)
        scope['parents'].append(identity)
        scope['edge_sources'].append(identity)
        scope['edge_targets'].append(identity)
        service._publish(human(), ArtifactRecord(artifact_id=identity, artifact_type='entity', storage_ref_type='entity_resolution', storage_ref_id=identity,
            exact_text=payload['display_name'].strip(), normalized_key=identity, metadata={'stage':'entities','entity_type':kind,'user_created_split':True}, review_state='corrected', created_by='user'))
        target = db.get_artifact(identity)
    else:
        replace_rule(db, 'alias', kind, key(source), target['artifact_id'])
        service.revise_artifact_fields(target['artifact_id'],{'review_state':'corrected'})
        service.revise_artifact_fields(source['artifact_id'], {'review_state':'superseded'})
        db.record_artifact_alias(source['artifact_id'], target['artifact_id'], 'migrated', 'Explicit entity merge')
    for identity in prepared['mentions']:
        replace_rule(db, 'mention_parent', kind, identity, target['artifact_id'])
        service.revise_artifact_fields(identity, {'parent_artifact_id':target['artifact_id'], 'review_state':'corrected'}, provenance_confirmed=True)
        move_mention_edge(service, identity, source['artifact_id'], target['artifact_id'])
    invalidate_derived_relations(service,source,prepared['mentions'])
    db._bump_knowledge_revision(None, 'structure')
    return target['artifact_id']


def replace_rule(db, kind, entity_type, key_value, target):
    db._conn.execute('DELETE FROM intelligence_entity_rules WHERE rule_type=? AND entity_type=? AND source_key=?', (kind,entity_type,key_value))
    db.set_entity_rule(kind, entity_type, key_value, target_artifact_id=target, metadata={'created_by':'user'})


def verify_structural_edge(service, source, target, edge_type, evidence):
    edge = service.link_artifacts(source,target,edge_type,confidence=1.0,created_by='user',
        evidence_artifact_ids=evidence,producer_version='entity-review-v1')
    for row in service.db.list_edge_assertions(edge):
        if row['created_by'] == 'user':
            service.db.set_edge_assertion_review_state(row['assertion_id'],'verified')


def move_mention_edge(service, identity, previous, target):
    db = service.db
    evidence = set()
    for row in state.rows_for(db, 'intelligence_edge_assertions', 'source_artifact_id', [identity]):
        if row['edge_type'] in {'mentions','mention_of'}:
            db.set_edge_assertion_review_state(row['assertion_id'], 'rejected')
            if row.get('evidence_artifact_id'):
                evidence.add(row['evidence_artifact_id'])
    mention = db.get_artifact(identity)
    if value := (mention.get('metadata') or {}).get('evidence_artifact_id'):
        evidence.add(value)
    verify_structural_edge(service,identity,target,'mention_of',sorted(evidence) or [identity])
    for value in evidence:
        if not db.get_artifact(value):
            continue
        verify_structural_edge(service,target,value,'mentioned_in',[value])
        remaining = db._conn.execute("SELECT 1 FROM intelligence_artifacts WHERE artifact_type='entity_mention' AND parent_artifact_id=? AND status='complete' AND review_state NOT IN ('rejected','superseded') AND json_extract(metadata_json,'$.evidence_artifact_id')=? LIMIT 1",(previous,value)).fetchone()
        if not remaining:
            for row in state.rows_for(db,'intelligence_edge_assertions','source_artifact_id',[previous]):
                if row['edge_type']=='mentioned_in' and row['target_artifact_id']==value:
                    db.set_edge_assertion_review_state(row['assertion_id'],'rejected')


def invalidate_derived_relations(service, source, identities):
    db = service.db
    for column in ('source_artifact_id','target_artifact_id'):
        for row in state.rows_for(db,'intelligence_edge_assertions',column,[source['artifact_id']]):
            if row['created_by']=='entity_pipeline' and row['edge_type'] in {'co_occurs_with','affiliation_candidate'} and row['review_state']=='unreviewed':
                db.set_edge_assertion_review_state(row['assertion_id'],'superseded')
    sources = {db.get_artifact(identity).get('source_id') for identity in identities}
    for identity in sources - {None,''}:
        db.upsert_stage_status(identity,'entities',{'status':'stale','error_text':'Entity grouping changed; regenerate derived entity relations.'})
