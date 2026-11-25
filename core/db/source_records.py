"""Indexed source records shared by project reads and intelligence hydration."""
import json
import os

MAX_SOURCE_BATCH = 128
COLUMNS = "id, path, source_type, mime_type, title, metadata_json, state_json"


def record(row):
    def obj(value):
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {"id": row[0], "path": row[1], "source_type": row[2] or "pdf",
            "mime_type": row[3] or "", "title": row[4] or os.path.basename(row[1] or ""),
            "metadata": obj(row[5]), "state": obj(row[6])}


def by_ids(conn, source_ids):
    identities = list(dict.fromkeys(source_ids))
    if len(identities) > MAX_SOURCE_BATCH:
        raise ValueError(f"Read at most {MAX_SOURCE_BATCH} sources per batch.")
    if not conn or not identities:
        return {}
    slots = ",".join("?" for _ in identities)
    return {row[0]: record(row) for row in conn.execute(
        f"SELECT {COLUMNS} FROM sources WHERE id IN ({slots})", identities).fetchall()}


def by_path(conn, path):
    if not conn:
        return None
    row = conn.execute(f"SELECT {COLUMNS} FROM sources WHERE path=?", (path,)).fetchone()
    return record(row) if row else None


def page(conn, after_id='', limit=1):
    if type(limit) is not int or not 1 <= limit <= MAX_SOURCE_BATCH:
        raise ValueError('Choose a bounded source page.')
    if not conn:
        return []
    rows = conn.execute(f"SELECT {COLUMNS} FROM sources WHERE id>? AND "
        "COALESCE(json_extract(CASE WHEN json_valid(state_json) THEN state_json ELSE '{}' END, '$.is_removed'),0)=0 "
        "ORDER BY id LIMIT ?", (after_id, limit)).fetchall()
    return [record(row) for row in rows]
