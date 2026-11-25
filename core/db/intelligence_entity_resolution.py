"""Resolve explicit entity grouping rules transitively without resurrecting retired IDs."""
import json


def alias_target(conn, entity_type, key):
    row = conn.execute("SELECT target_artifact_id FROM intelligence_entity_rules WHERE rule_type='alias' AND entity_type=? AND source_key=? ORDER BY created_at DESC,rowid DESC LIMIT 1",(entity_type,key)).fetchone()
    return resolve_target(conn,row[0]) if row and row[0] else None


def resolve_target(conn, identity):
    seen = set()
    while identity:
        if identity in seen:
            raise ValueError('Entity alias cycle requires review before extraction can continue.')
        seen.add(identity)
        row = conn.execute("SELECT artifact_type,normalized_key,normalized_text,metadata_json,status,review_state FROM intelligence_artifacts WHERE artifact_id=?",(identity,)).fetchone()
        if not row or row[0] != 'entity':
            raise ValueError('Entity grouping points to a missing canonical entity. Review the grouping first.')
        entity_type = json.loads(row[3] or '{}').get('entity_type')
        following = conn.execute("SELECT target_artifact_id FROM intelligence_entity_rules WHERE rule_type='alias' AND entity_type=? AND source_key=? ORDER BY created_at DESC,rowid DESC LIMIT 1",(entity_type,row[1] or row[2] or identity)).fetchone()
        if following and following[0]:
            identity = following[0]
        else:
            if row[4] != 'complete' or row[5] in {'rejected','superseded'}:
                raise ValueError('Entity grouping destination is retired. Review the grouping before extraction.')
            return identity


def group_keys(conn, entity_type, identity):
    row = conn.execute('SELECT normalized_key,normalized_text FROM intelligence_artifacts WHERE artifact_id=?',(identity,)).fetchone()
    keys = {row[0] or row[1] or identity} if row else {identity}
    for item in conn.execute("SELECT DISTINCT source_key FROM intelligence_entity_rules WHERE rule_type='alias' AND entity_type=?",(entity_type,)).fetchall():
        if alias_target(conn,entity_type,item[0]) == identity:
            keys.add(item[0])
    return sorted(keys)
