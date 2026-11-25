# core/db/annotation_db.py
import sqlite3
from contextlib import nullcontext
from core.db.base_db import BaseDB
from core.db import highlight_queries

class AnnotationDB(BaseDB):
    def upsert_highlight(self, highlight_data, *, commit=True):
        if not self._conn: return
        try:
            self._conn.execute(
                """
                INSERT INTO highlights (id, doc_id, page_num, rect_coords, text_content, note_content, color)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    doc_id = excluded.doc_id, page_num = excluded.page_num,
                    rect_coords = excluded.rect_coords, text_content = excluded.text_content,
                    note_content = excluded.note_content,
                    color = excluded.color
                """,
                (
                    highlight_data.get("id"), highlight_data.get("doc_id"),
                    highlight_data.get("page_num"), highlight_data.get("rect_coords"),
                    highlight_data.get("text_content", ""),
                    highlight_data.get("note_content", highlight_data.get("content", "")),
                    highlight_data.get("color"),
                ),
            )
            if commit:
                self._conn.commit()
        except sqlite3.Error as e:
            if not commit:
                raise
            print(f"Error upserting highlight {highlight_data.get('id')}: {e}")

    def get_highlights(self):
        if not self._conn: return {}
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, doc_id, page_num, rect_coords, text_content, note_content, color FROM highlights")
            return {
                row[0]: {
                    "id": row[0], "doc_id": row[1], "page_num": row[2],
                    "rect_coords": row[3], "text_content": row[4],
                    "content": row[5] or "", "note_content": row[5] or "",
                    "color": row[6],
                }
                for row in cursor.fetchall()
            }
        except sqlite3.Error as e:
            print(f"Error reading highlights: {e}")
            return {}

    def get_highlight(self, highlight_id):
        if not self._conn:
            return None
        row = self._conn.execute(f"SELECT {highlight_queries.COLUMNS} FROM highlights h WHERE h.id=?",
                                 (highlight_id,)).fetchone()
        return highlight_queries.record(row) if row else None

    def query_highlights(self, source_path, **filters):
        """Call off Qt: first read builds/backfills derived indexes atomically."""
        lock = getattr(self.manager, 'db_lock', None)
        with lock if lock is not None else nullcontext():
            conn = self._conn
            if conn is not None and getattr(self, '_indexed_connection', None) is not conn:
                nested = conn.in_transaction
                highlight_queries.ensure_indexes(conn)
                # A caller can roll its outer transaction back, including DDL.
                if not nested:
                    self._indexed_connection = conn
            return highlight_queries.query(conn, source_path, **filters)

    def iter_highlights(self, source_path, *, cancelled=lambda: False, **filters):
        after_id = None
        while not cancelled():
            rows = self.query_highlights(source_path, after_id=after_id, **filters)
            if not rows:
                return
            yield from rows
            after_id = rows[-1]['id']

    def get_unused_highlights(self, workspace_id):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT h.id, h.doc_id, h.page_num, h.rect_coords, h.text_content, h.note_content, h.color
                FROM highlights h
                WHERE NOT EXISTS (
                    SELECT 1 FROM nodes n
                    WHERE n.workspace_id = ? AND (n.highlight_id = h.id OR n.id = h.id)
                )
                ORDER BY h.doc_id, h.page_num, h.id
                """, (workspace_id,)
            )
            return [
                {"id": row[0], "doc_id": row[1], "page_num": row[2],
                 "rect_coords": row[3], "text_content": row[4],
                 "content": row[5] or "", "note_content": row[5] or "",
                 "color": row[6]}
                for row in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            print(f"Error reading unused highlights for workspace {workspace_id}: {e}")
            return []

    def delete_highlight_record(self, highlight_id, *, commit=True):
        if not self._conn: return
        try:
            self._conn.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
            if commit:
                self._conn.commit()
        except sqlite3.Error as e:
            if not commit:
                raise
            print(f"Error deleting highlight {highlight_id}: {e}")
    def update_highlight_text(self, highlight_id, new_text):
        self.update_highlight_note(highlight_id, new_text)

    def update_highlight_note(self, highlight_id, new_text, *, commit=True):
        if not self._conn: return
        self._conn.execute("UPDATE highlights SET note_content = ? WHERE id = ?", (new_text, highlight_id))
        if commit:
            self._conn.commit()

    def update_highlight_color(self, highlight_id, hex_color, *, commit=True):
        if not self._conn: return
        self._conn.execute("UPDATE highlights SET color = ? WHERE id = ?", (hex_color, highlight_id))
        if commit:
            self._conn.commit()
