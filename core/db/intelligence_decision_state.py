"""Targeted snapshots for atomic, revision-checked knowledge decisions."""
import hashlib
import json


def mention_ids(db, parent):
    return [row[0] for row in db._conn.execute("SELECT artifact_id FROM intelligence_artifacts WHERE artifact_type='entity_mention' AND parent_artifact_id=? AND status='complete' AND review_state NOT IN ('rejected','superseded') ORDER BY artifact_id", (parent,))]


def assertion(db, identity):
    cur = db._conn.execute("SELECT * FROM intelligence_edge_assertions WHERE assertion_id=?", (identity,))
    row = cur.fetchone()
    return dict(zip([col[0] for col in cur.description], row)) if row else None


def rows_for(db, table, column, identities):
    cur = db._conn.execute(f"SELECT * FROM {table} WHERE {column} IN (SELECT value FROM json_each(?)) ORDER BY {column}", (json.dumps(identities),))
    keys = [col[0] for col in cur.description]
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def capture(db, scope):
    artifacts = {identity: db.get_artifact(identity) for identity in scope.get('artifacts', [])}
    rules = []
    for kind, entity_type, key in scope.get('rules', []):
        cur = db._conn.execute("SELECT * FROM intelligence_entity_rules WHERE rule_type=? AND entity_type=? AND source_key=? ORDER BY rule_id", (kind, entity_type, key))
        keys = [col[0] for col in cur.description]
        rules.extend(dict(zip(keys, row)) for row in cur.fetchall())
    assertions = rows_for(db, 'intelligence_edge_assertions', 'assertion_id', scope.get('assertions', []))
    for identity in scope.get('mention_edges', []):
        assertions.extend(row for row in rows_for(db, 'intelligence_edge_assertions', 'source_artifact_id', [identity]) if row['edge_type'] in {'mentions','mention_of'})
    for identity in scope.get('edge_sources', []):
        assertions.extend(rows_for(db, 'intelligence_edge_assertions', 'source_artifact_id', [identity]))
    for identity in scope.get('edge_targets', []):
        assertions.extend(rows_for(db, 'intelligence_edge_assertions', 'target_artifact_id', [identity]))
    assertions = sorted({item['assertion_id']:item for item in assertions}.values(),key=lambda item:item['assertion_id'])
    return {'artifacts': artifacts, 'rules': rules, 'assertions': assertions,
            'aliases': {identity: db.resolve_artifact_alias(identity) for identity in scope.get('aliases', [])},
            'parents': {identity: mention_ids(db, identity) for identity in scope.get('parents', [])}}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def restore(service, scope, before, after):
    db = service.db
    for identity, row in before['artifacts'].items():
        if row == after['artifacts'].get(identity):
            continue
        if row:
            changes = {key: row.get(key) for key in ('parent_artifact_id','metadata','confidence','review_state','status')}
            service.revise_artifact_fields(identity, changes, provenance_confirmed=True, created_by='user:undo')
        elif db.get_artifact(identity):
            service.revise_artifact_fields(identity, {'review_state':'rejected'}, created_by='user:undo')
    for kind, entity_type, key in scope.get('rules', []):
        db._conn.execute("DELETE FROM intelligence_entity_rules WHERE rule_type=? AND entity_type=? AND source_key=?", (kind, entity_type, key))
    for row in before['rules']:
        db._conn.execute("INSERT INTO intelligence_entity_rules VALUES (?,?,?,?,?,?,?,?)", tuple(row[key] for key in ('rule_id','rule_type','entity_type','source_key','target_artifact_id','other_key','metadata_json','created_at')))
    for identity in scope.get('aliases', []):
        db._conn.execute("DELETE FROM intelligence_artifact_aliases WHERE old_artifact_id=?", (identity,))
        if row := before['aliases'].get(identity):
            db.record_artifact_alias(identity, row['new_artifact_id'], row['status'], row.get('reason') or '')
    old = {row['assertion_id']: row for row in before['assertions']}
    for row in after['assertions']:
        db.set_edge_assertion_review_state(row['assertion_id'], old.get(row['assertion_id'], {}).get('review_state','rejected'))
    db._bump_knowledge_revision(None, 'relation')
