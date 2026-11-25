"""Rebuildable page index and bounded reads of project-owned highlights."""

MAX_HIGHLIGHT_BATCH = 128
MAX_PAGE_BATCH = 64
COLUMNS = "h.id, h.doc_id, h.page_num, h.rect_coords, h.text_content, h.note_content, h.color"
PAGE_ROWS = """
    SELECT {owner}.id, {owner}.doc_id,
        json_extract(CASE WHEN span.type='object' THEN span.value ELSE '{{}}' END, '$.page_index')
    FROM {table}, json_each(CASE WHEN json_valid({owner}.rect_coords)
        THEN {owner}.rect_coords ELSE '{{}}' END, '$.reference_anchor.spans') AS span
    WHERE {owner}.id GLOB 'LinkedNote|*'
      AND json_type(CASE WHEN span.type='object' THEN span.value ELSE '{{}}' END, '$.page_index') = 'integer'
      AND json_extract(CASE WHEN span.type='object' THEN span.value ELSE '{{}}' END, '$.page_index') >= 0
"""


def ensure_indexes(conn):
    """Build on the first worker read; triggers cover legacy/raw SQL writes too."""
    conn.execute('SAVEPOINT highlight_page_index')
    try:
        _ensure_indexes(conn)
    except Exception:
        conn.execute('ROLLBACK TO highlight_page_index')
        raise
    finally:
        conn.execute('RELEASE highlight_page_index')


def _ensure_indexes(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='highlight_pages'").fetchone()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_highlights_source_id ON highlights(doc_id, id)")
    conn.execute("CREATE TABLE IF NOT EXISTS highlight_pages (highlight_id TEXT NOT NULL, doc_id TEXT NOT NULL, "
                 "page_num INTEGER NOT NULL, PRIMARY KEY(highlight_id, page_num))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_highlight_pages_source_page "
                 "ON highlight_pages(doc_id, page_num, highlight_id)")
    for action in ('INSERT', 'UPDATE OF id, doc_id, rect_coords'):
        name = 'insert' if action == 'INSERT' else 'update'
        cleanup = "DELETE FROM highlight_pages WHERE highlight_id=OLD.id;" if name == 'update' else ''
        rows = PAGE_ROWS.format(owner='NEW', table='(SELECT 1)')
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS highlight_pages_{name} AFTER {action} ON highlights BEGIN "
                     f"{cleanup} INSERT OR IGNORE INTO highlight_pages {rows}; END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS highlight_pages_delete AFTER DELETE ON highlights BEGIN "
                 "DELETE FROM highlight_pages WHERE highlight_id=OLD.id; END")
    if not exists:
        conn.execute("INSERT OR IGNORE INTO highlight_pages " + PAGE_ROWS.format(owner='h', table='highlights h'))


def record(row):
    return {"id": row[0], "doc_id": row[1], "page_num": row[2], "rect_coords": row[3],
            "text_content": row[4], "content": row[5] or "", "note_content": row[5] or "", "color": row[6]}


def query(conn, source_path, *, after_id=None, limit=MAX_HIGHLIGHT_BATCH, linked_only=False, page_indices=None):
    if not isinstance(limit, int) or not 1 <= limit <= MAX_HIGHLIGHT_BATCH:
        raise ValueError(f"Read between 1 and {MAX_HIGHLIGHT_BATCH} highlights at a time.")
    if not conn:
        return []
    clauses, args = ['h.doc_id=?'], [source_path]
    if after_id is not None:
        clauses.append('h.id>?')
        args.append(after_id)
    if linked_only:
        clauses.append("h.id GLOB 'LinkedNote|*'")
    if page_indices is not None:
        pages = list(dict.fromkeys(page_indices))
        if len(pages) > MAX_PAGE_BATCH or any(type(page) is not int or page < 0 for page in pages):
            raise ValueError(f"Supply at most {MAX_PAGE_BATCH} nonnegative page indices.")
        if not pages:
            return []
        slots = ','.join('?' for _ in pages)
        clauses.append(f'h.id IN (SELECT highlight_id FROM highlight_pages WHERE doc_id=? AND page_num IN ({slots}))')
        args.extend([source_path, *pages])
    rows = conn.execute(f"SELECT {COLUMNS} FROM highlights h WHERE {' AND '.join(clauses)} ORDER BY h.id LIMIT ?",
                        [*args, limit]).fetchall()
    return [record(row) for row in rows]
