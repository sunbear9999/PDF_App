"""Bulk edge reads through the intelligence facade, without per-artifact scans."""
import json


def edges_for_artifacts(db, artifact_ids):
    allowed = set(str(value) for value in artifact_ids if value)
    if not db._conn or not allowed:
        return []
    ids, result = sorted(allowed), {}
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        marks = ','.join('?' for _ in batch)
        rows = db._conn.execute(f'''
            SELECT edge_id, source_artifact_id, target_artifact_id, edge_type,
                   confidence, created_by, metadata_json, created_at
            FROM intelligence_artifact_edges WHERE source_artifact_id IN ({marks})
            ORDER BY edge_id''', batch).fetchall()
        for row in rows:
            if row[2] not in allowed:
                continue
            result[row[0]] = {
                'edge_id': row[0], 'source_artifact_id': row[1],
                'target_artifact_id': row[2], 'edge_type': row[3],
                'confidence': row[4], 'created_by': row[5],
                'metadata': json.loads(row[6] or '{}'), 'created_at': row[7],
                'assertions': db.list_edge_assertions(row[0]),
            }
    return [result[key] for key in sorted(result)]
