"""
plugins/locallaws/law_tags_db.py

Plugin-local persistent storage for:
  • Tags (name + color) — independent of any project
  • Per-DB tag assignments (file_id ↔ tag)
  • Canonical index — deduplication across law databases
  • Cross-reference table — records borrowed from another DB

All operations are thread-safe.  The file lives at
  plugins/locallaws/dbs/law_tags.db
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Dict, List, Optional


class LocalLawsTagDB:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._lock = threading.RLock()
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tags (
                        id    INTEGER PRIMARY KEY AUTOINCREMENT,
                        name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        color TEXT NOT NULL DEFAULT '#607d8b'
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS db_tags (
                        file_id TEXT    NOT NULL,
                        tag_id  INTEGER NOT NULL,
                        PRIMARY KEY (file_id, tag_id),
                        FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
                    )
                """)
                # Canonical index: one row per unique law/case across all DBs.
                # canonical_key examples:
                #   caselaw : "cl:<cluster_id>"
                #   municipal: "mun:<state_lc>:<city_lc>:<header_lc>"
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS canonical_index (
                        canonical_key   TEXT PRIMARY KEY,
                        file_id         TEXT NOT NULL,
                        row_index       INTEGER NOT NULL DEFAULT -1
                    )
                """)
                # Cross-refs: when file B borrows a record that already lives in file A.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cross_refs (
                        borrower_file_id  TEXT NOT NULL,
                        canonical_key     TEXT NOT NULL,
                        source_file_id    TEXT NOT NULL,
                        source_row_index  INTEGER NOT NULL DEFAULT -1,
                        PRIMARY KEY (borrower_file_id, canonical_key)
                    )
                """)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Tag CRUD
    # ------------------------------------------------------------------

    def create_tag(self, name: str, color: str = "#607d8b") -> Optional[int]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)",
                    (name, color),
                )
                row = conn.execute(
                    "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
                ).fetchone()
                return row[0] if row else None
            finally:
                conn.close()

    def ensure_tag(self, name: str, color: str = "#607d8b") -> Optional[int]:
        """Return existing tag_id for *name* or create it."""
        return self.create_tag(name, color)

    def delete_tag(self, tag_id: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
                conn.commit()
            finally:
                conn.close()

    def rename_tag(self, tag_id: int, new_name: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))
                conn.commit()
            finally:
                conn.close()

    def update_tag_color(self, tag_id: int, color: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("UPDATE tags SET color = ? WHERE id = ?", (color, tag_id))
                conn.commit()
            finally:
                conn.close()

    def get_all_tags(self) -> List[Dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, name, color FROM tags ORDER BY name COLLATE NOCASE"
                ).fetchall()
                return [{"id": r[0], "name": r[1], "color": r[2]} for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # DB-level tag assignments
    # ------------------------------------------------------------------

    def assign_tag_to_db(self, file_id: str, tag_id: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO db_tags (file_id, tag_id) VALUES (?, ?)",
                    (file_id, tag_id),
                )
                conn.commit()
            finally:
                conn.close()

    def remove_tag_from_db(self, file_id: str, tag_id: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM db_tags WHERE file_id = ? AND tag_id = ?",
                    (file_id, tag_id),
                )
                conn.commit()
            finally:
                conn.close()

    def set_tags_for_db(self, file_id: str, tag_ids: List[int]) -> None:
        """Replace the entire tag set for *file_id*."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM db_tags WHERE file_id = ?", (file_id,))
                for tid in tag_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO db_tags (file_id, tag_id) VALUES (?, ?)",
                        (file_id, tid),
                    )
                conn.commit()
            finally:
                conn.close()

    def get_tags_for_db(self, file_id: str) -> List[Dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """SELECT t.id, t.name, t.color FROM tags t
                       JOIN db_tags d ON d.tag_id = t.id
                       WHERE d.file_id = ?
                       ORDER BY t.name COLLATE NOCASE""",
                    (file_id,),
                ).fetchall()
                return [{"id": r[0], "name": r[1], "color": r[2]} for r in rows]
            finally:
                conn.close()

    def get_dbs_for_tag(self, tag_id: int) -> List[str]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT file_id FROM db_tags WHERE tag_id = ?", (tag_id,)
                ).fetchall()
                return [r[0] for r in rows]
            finally:
                conn.close()

    def get_dbs_for_tags(self, tag_names: List[str], logic: str = "AND") -> List[str]:
        """Return file_ids that match all (AND) or any (OR) of *tag_names*."""
        if not tag_names:
            return []
        with self._lock:
            conn = self._connect()
            try:
                ph = ",".join("?" * len(tag_names))
                tag_rows = conn.execute(
                    f"SELECT id FROM tags WHERE name IN ({ph}) COLLATE NOCASE",
                    tag_names,
                ).fetchall()
                tag_ids = [r[0] for r in tag_rows]
                if not tag_ids:
                    return []
                id_ph = ",".join("?" * len(tag_ids))
                if logic.upper() == "AND":
                    rows = conn.execute(
                        f"""SELECT file_id FROM db_tags WHERE tag_id IN ({id_ph})
                            GROUP BY file_id HAVING COUNT(DISTINCT tag_id) = ?""",
                        tag_ids + [len(tag_ids)],
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT DISTINCT file_id FROM db_tags WHERE tag_id IN ({id_ph})",
                        tag_ids,
                    ).fetchall()
                return [r[0] for r in rows]
            finally:
                conn.close()

    def get_all_tagged_file_ids(self) -> List[str]:
        """Return all file_ids that have at least one tag."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT file_id FROM db_tags"
                ).fetchall()
                return [r[0] for r in rows]
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Canonical index (cross-DB deduplication)
    # ------------------------------------------------------------------

    def lookup_canonical(self, canonical_key: str) -> Optional[Dict]:
        """Return {file_id, row_index} if this key is already indexed, else None."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT file_id, row_index FROM canonical_index WHERE canonical_key = ?",
                    (canonical_key,),
                ).fetchone()
                return {"file_id": row[0], "row_index": row[1]} if row else None
            finally:
                conn.close()

    def register_canonical(
        self, canonical_key: str, file_id: str, row_index: int = -1
    ) -> None:
        """Mark *canonical_key* as owned by *file_id*."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO canonical_index
                       (canonical_key, file_id, row_index) VALUES (?, ?, ?)""",
                    (canonical_key, file_id, row_index),
                )
                conn.commit()
            finally:
                conn.close()

    def add_cross_ref(
        self,
        borrower_file_id: str,
        canonical_key: str,
        source_file_id: str,
        source_row_index: int = -1,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO cross_refs
                       (borrower_file_id, canonical_key, source_file_id, source_row_index)
                       VALUES (?, ?, ?, ?)""",
                    (borrower_file_id, canonical_key, source_file_id, source_row_index),
                )
                conn.commit()
            finally:
                conn.close()

    def get_cross_refs(self, file_id: str) -> List[Dict]:
        """Return records borrowed by *file_id* from other DBs."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """SELECT canonical_key, source_file_id, source_row_index
                       FROM cross_refs WHERE borrower_file_id = ?""",
                    (file_id,),
                ).fetchall()
                return [
                    {
                        "canonical_key": r[0],
                        "source_file_id": r[1],
                        "source_row_index": r[2],
                    }
                    for r in rows
                ]
            finally:
                conn.close()

    def remove_cross_refs_for_db(self, file_id: str) -> None:
        """Remove all cross-refs whose borrower or source is *file_id*."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM cross_refs WHERE borrower_file_id = ? OR source_file_id = ?",
                    (file_id, file_id),
                )
                conn.execute(
                    "DELETE FROM canonical_index WHERE file_id = ?", (file_id,)
                )
                conn.commit()
            finally:
                conn.close()
