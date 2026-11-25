"""Bounded revision/proposal reads with project and write-generation guards."""
import json
from core.db import browse_cursors as browse_cursor


def query(service, artifact_id, cursor='', limit=25):
    if type(limit) is not int or not 1 <= limit <= 25:
        raise ValueError('Choose between 1 and 25 history records.')
    pm, conn = service.project_manager, service.db._conn
    binding = [artifact_id, limit, 'artifact_history_v1']
    position = browse_cursor.unpack(cursor, binding, browse_cursor.revision(pm, 'history'))
    if position.get('kind') == 'proposals':
        return proposals(conn, pm, artifact_id, position, binding, limit)
    before = position.get('before', 2**63-1)
    rows = conn.execute('SELECT revision_id, revision, snapshot_json, review_state, created_by, created_at '
        'FROM intelligence_artifact_revisions WHERE artifact_id=? AND revision<? ORDER BY revision DESC LIMIT ?',
        (artifact_id, before, limit+1)).fetchall()
    items = [{'title': f'Revision {row[1]} · {row[5]} · {row[4]}', 'preview': row[3] or '',
              'snapshot': json.loads(row[2]), 'revision': row[1]} for row in rows[:limit]]
    if len(rows) > limit:
        position = {'before': rows[limit-1][1]}
    elif conn.execute('SELECT 1 FROM intelligence_artifact_proposals WHERE artifact_id=? LIMIT 1', (artifact_id,)).fetchone():
        position = {'kind': 'proposals', 'after': ''}
    else:
        position = None
    return {'items': items, 'next_cursor': browse_cursor.pack(position, binding, browse_cursor.revision(pm, 'history'))}


def proposals(conn, pm, artifact_id, position, binding, limit):
    rows = conn.execute('SELECT payload_hash, proposal_json, detected_at FROM intelligence_artifact_proposals '
        'WHERE artifact_id=? AND payload_hash>? ORDER BY payload_hash LIMIT ?',
        (artifact_id, position.get('after', ''), limit+1)).fetchall()
    items = [{'title': f'New machine result · {row[2]}', 'preview': 'Suggested; your correction remains active',
              'snapshot': json.loads(row[1])} for row in rows[:limit]]
    position = {'kind': 'proposals', 'after': rows[limit-1][0]} if len(rows) > limit else None
    return {'items': items, 'next_cursor': browse_cursor.pack(position, binding, browse_cursor.revision(pm, 'history'))}
