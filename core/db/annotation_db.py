# core/db/annotation_db.py
import sqlite3
from core.db.base_db import BaseDB

class AnnotationDB(BaseDB):
    def upsert_highlight(self, highlight_data):
        if not self._conn: return
        try:
            self._conn.execute(
                """
                INSERT INTO highlights (id, doc_id, page_num, rect_coords, text_content, color)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    doc_id = excluded.doc_id, page_num = excluded.page_num,
                    rect_coords = excluded.rect_coords, text_content = excluded.text_content,
                    color = excluded.color
                """,
                (
                    highlight_data.get("id"), highlight_data.get("doc_id"),
                    highlight_data.get("page_num"), highlight_data.get("rect_coords"),
                    highlight_data.get("text_content", ""), highlight_data.get("color"),
                ),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error upserting highlight {highlight_data.get('id')}: {e}")

    def get_highlights(self):
        if not self._conn: return {}
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, doc_id, page_num, rect_coords, text_content, color FROM highlights")
            return {
                row[0]: {
                    "id": row[0], "doc_id": row[1], "page_num": row[2],
                    "rect_coords": row[3], "text_content": row[4], "color": row[5],
                }
                for row in cursor.fetchall()
            }
        except sqlite3.Error as e:
            print(f"Error reading highlights: {e}")
            return {}

    def get_highlight(self, highlight_id):
        return self.get_highlights().get(highlight_id)

    def get_unused_highlights(self, workspace_id):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT h.id, h.doc_id, h.page_num, h.rect_coords, h.text_content, h.color
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
                 "rect_coords": row[3], "text_content": row[4], "color": row[5]}
                for row in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            print(f"Error reading unused highlights for workspace {workspace_id}: {e}")
            return []

    def delete_highlight_record(self, highlight_id):
        if not self._conn: return
        try:
            self._conn.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error deleting highlight {highlight_id}: {e}")
    def update_highlight_text(self, highlight_id, new_text):
    if not self.pm._conn: return
    cursor = self.pm._conn.cursor()
    cursor.execute("UPDATE highlights SET text_content = ? WHERE id = ?", (new_text, highlight_id))
    self.pm._conn.commit()

def update_highlight_color(self, highlight_id, hex_color):
    if not self.pm._conn: return
    cursor = self.pm._conn.cursor()
    cursor.execute("UPDATE highlights SET color = ? WHERE id = ?", (hex_color, highlight_id))
    self.pm._conn.commit()