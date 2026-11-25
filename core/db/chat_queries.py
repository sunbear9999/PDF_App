"""Keyset pages of persisted chat; called by a worker through ProjectManager."""
import json
from core.db import browse_cursors as browse_cursor

MAX_MESSAGES = 12


def page(pm, tab_name, cursor='', limit=MAX_MESSAGES):
    if type(limit) is not int or not 1 <= limit <= MAX_MESSAGES:
        raise ValueError('Choose between 1 and 12 chat messages.')
    conn = pm._conn
    binding = [tab_name, limit, 'chat_history_v1']
    position = browse_cursor.unpack(cursor, binding, browse_cursor.revision(pm, 'chat'))
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_messages_tab_id ON chat_messages(tab_name, id)')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(chat_messages)')}
    trace = 'trace_id' if 'trace_id' in columns else 'NULL'
    payload = 'ui_payload' if 'ui_payload' in columns else 'NULL'
    before = position.get('before', 2**63-1)
    rows = conn.execute(f'SELECT id,role,content,ui_format,{trace},{payload} FROM chat_messages '
        'WHERE tab_name=? AND id<? ORDER BY id DESC LIMIT ?', (tab_name, before, limit+1)).fetchall()
    chosen = rows[:limit]
    user_ids = [row[0] for row in chosen if row[1] == 'user']
    if user_ids:
        chosen = [row for row in chosen if row[0] >= min(user_ids)]
    items = [{'id': row[0], 'role': row[1], 'content': row[2], 'ui_format': row[3],
              'trace_id': row[4], 'ui_payload': row[5]} for row in reversed(chosen)]
    position = {'before': chosen[-1][0]} if len(rows) > len(chosen) else None
    continued = starts_with_continuation(items)
    return {'items': items, 'continued': continued,
            'next_cursor': browse_cursor.pack(position, binding, browse_cursor.revision(pm, 'chat'))}


def starts_with_continuation(items):
    if not items:
        return False
    raw = items[0].get('ui_payload')
    try:
        payloads = json.loads(raw) if isinstance(raw, str) else raw or []
        return isinstance(payloads, list) and any(isinstance(item, dict) and item.get('attach_to_previous') for item in payloads)
    except ValueError:
        return False
