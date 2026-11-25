"""Keyword (FTS5) index rows for intelligence artifacts, replaced by rowid.

``artifact_id`` is an UNINDEXED FTS5 column, so ``DELETE ... WHERE
artifact_id=?`` scans the whole index. Doing that once per artifact write made
publishing quadratic: a 250-page source spent over a minute in it. A small
``artifact_id → rowid`` map lets each replacement delete by rowid instead.
"""
from __future__ import annotations

from typing import Any, Dict

FTS_TABLE = "intelligence_artifacts_fts"
MAP_TABLE = "intelligence_artifacts_fts_rowids"
MIGRATION_VERSION = 6  # 2-5 are taken by intelligence_db and the decision journal
MIGRATION_NOTE = "artifact keyword index replaced by rowid"


def ensure_table(conn, now: str) -> None:
    """Create the rowid map; backfill it once from an existing index."""
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {MAP_TABLE} ("
        "artifact_id TEXT PRIMARY KEY, fts_rowid INTEGER NOT NULL)"
    )
    done = conn.execute(
        "SELECT 1 FROM intelligence_schema_migrations WHERE version=?", (MIGRATION_VERSION,)
    ).fetchone()
    if done:
        return
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO {MAP_TABLE} (artifact_id, fts_rowid) "
            f"SELECT artifact_id, MAX(rowid) FROM {FTS_TABLE} GROUP BY artifact_id"
        )
    except Exception:
        return  # SQLite without FTS5: keyword search uses LIKE, nothing to map.
    conn.execute(
        "INSERT OR IGNORE INTO intelligence_schema_migrations "
        "(version, description, applied_at) VALUES (?, ?, ?)",
        (MIGRATION_VERSION, MIGRATION_NOTE, now),
    )


def upsert(conn, values: Dict[str, Any], *, replace_existing: bool = True) -> None:
    artifact_id = values.get("artifact_id")
    if replace_existing:
        row = conn.execute(
            f"SELECT fts_rowid FROM {MAP_TABLE} WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if row:
            conn.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid=?", (row[0],))
    cur = conn.execute(
        f"""
        INSERT INTO {FTS_TABLE} (
            artifact_id, source_id, artifact_type, exact_text,
            normalized_text, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            values.get("source_id"),
            values.get("artifact_type"),
            values.get("exact_text") or "",
            values.get("normalized_text") or "",
            values.get("metadata_json") or "{}",
        ),
    )
    conn.execute(
        f"INSERT OR REPLACE INTO {MAP_TABLE} (artifact_id, fts_rowid) VALUES (?, ?)",
        (artifact_id, cur.lastrowid),
    )


def purge_orphans(conn) -> None:
    conn.execute(
        f"DELETE FROM {FTS_TABLE} "
        "WHERE artifact_id NOT IN (SELECT artifact_id FROM intelligence_artifacts)"
    )
    conn.execute(
        f"DELETE FROM {MAP_TABLE} "
        "WHERE artifact_id NOT IN (SELECT artifact_id FROM intelligence_artifacts)"
    )


def purge_ids(conn, artifact_ids) -> None:
    """Remove known artifacts without scanning the complete FTS index."""
    ids = [identity for identity in artifact_ids if identity]
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT fts_rowid FROM {MAP_TABLE} WHERE artifact_id IN ({marks})", ids
    ).fetchall()
    rowids = [row[0] for row in rows]
    if rowids:
        row_marks = ",".join("?" for _ in rowids)
        conn.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid IN ({row_marks})", rowids)
    conn.execute(f"DELETE FROM {MAP_TABLE} WHERE artifact_id IN ({marks})", ids)
