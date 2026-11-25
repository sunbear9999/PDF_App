"""Project-bound cursors for bounded Notes/history readers."""
import base64
import json

TABLES = ('sources', 'highlights', 'intelligence_artifacts', 'intelligence_artifact_revisions',
          'intelligence_artifact_proposals', 'intelligence_artifact_aliases', 'chat_messages',
          'tags', 'doc_tags', 'node_tags', 'entities')
COLLECTIONS = {'notes': (1, {'sources', 'highlights', 'intelligence_artifacts', 'intelligence_artifact_aliases',
                            'tags', 'doc_tags', 'node_tags'}),
               'history': (2, {'intelligence_artifacts', 'intelligence_artifact_revisions', 'intelligence_artifact_proposals'}),
               'chat': (3, {'chat_messages'}),
               'inbox': (4, {'sources', 'highlights', 'intelligence_artifacts', 'intelligence_artifact_aliases', 'entities'})}


def ensure_revision(pm, conn):
    schema = conn.execute('PRAGMA schema_version').fetchone()[0]
    if getattr(pm, '_browse_revision_schema', None) == (id(conn), schema):
        return
    nested = conn.in_transaction
    conn.execute('SAVEPOINT browse_revision_setup')
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS browse_revisions (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)')
        conn.executemany('INSERT OR IGNORE INTO browse_revisions VALUES (?,0)', [(value[0],) for value in COLLECTIONS.values()])
        existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in TABLES:
            if table not in existing:
                continue
            groups = ','.join(str(identity) for identity, tables in COLLECTIONS.values() if table in tables)
            for action in ('INSERT', 'UPDATE', 'DELETE'):
                conn.execute(f'DROP TRIGGER IF EXISTS browse_{table}_{action.lower()}')
                conn.execute(f'CREATE TRIGGER IF NOT EXISTS browse_{table}_{action.lower()} AFTER {action} ON {table} '
                             f'BEGIN UPDATE browse_revisions SET revision=revision+1 WHERE id IN ({groups}); END')
    except Exception:
        conn.execute('ROLLBACK TO browse_revision_setup')
        raise
    finally:
        conn.execute('RELEASE browse_revision_setup')
    if not nested:
        pm._browse_revision_schema = (id(conn), conn.execute('PRAGMA schema_version').fetchone()[0])


def revision(pm, collection='notes'):
    conn = getattr(pm, '_conn', None)
    if conn is None:
        raise ValueError('Open a project before browsing.')
    ensure_revision(pm, conn)
    identity = COLLECTIONS[collection][0]
    return [id(conn), collection, conn.execute('SELECT revision FROM browse_revisions WHERE id=?', (identity,)).fetchone()[0]]


def unpack(cursor, binding, current):
    if not cursor:
        return {}
    try:
        if len(cursor) > 8192:
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if value['binding'] != binding or value['revision'] != current:
            raise ValueError()
        position = value['position']
        if not isinstance(position, dict):
            raise ValueError()
        return position
    except (ValueError, TypeError, KeyError):
        raise ValueError('The collection changed. Refresh before continuing.') from None


def pack(position, binding, current):
    if position is None:
        return ''
    return base64.urlsafe_b64encode(json.dumps({'position': position, 'binding': binding,
        'revision': current}, sort_keys=True).encode()).decode()
