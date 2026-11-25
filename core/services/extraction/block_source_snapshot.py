"""Bind cached raw blocks to the file actually supplied to their producer."""
import hashlib
import json

SNAPSHOT_NAME = 'block_source_snapshot'
SNAPSHOT_VERSION = '1.0'


def blocks_digest(rows):
    content = [{key: row.get(key) for key in ('block_id', 'doc_id', 'page_num', 'exact_text', 'bbox')}
               for row in rows]
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def store(cache, doc_id, rows, source_hash):
    cache.set(doc_id, SNAPSHOT_NAME, SNAPSHOT_VERSION, 'snapshot',
              {'source_hash': source_hash, 'blocks_hash': blocks_digest(rows),
               'producers': {row['block_id']: row.get('provenance') for row in rows},
               'layout': {row['block_id']: {key: row.get(key) for key in
                   ('furniture_spans', 'line_x0', 'structure')} for row in rows}})


def read(cache, doc_id, rows):
    snapshot = cache.get(doc_id, SNAPSHOT_NAME, 'snapshot', SNAPSHOT_VERSION) or {}
    if not snapshot.get('source_hash') or snapshot.get('blocks_hash') != blocks_digest(rows):
        raise ValueError('These extracted blocks have no verified input snapshot. Run source extraction again.')
    return snapshot
