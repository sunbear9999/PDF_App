# core/db/tag_db.py
import sqlite3
import os
from core.db.base_db import BaseDB

class TagDB(BaseDB):
    def create_tag(self, name, color):
        if not self._conn: return None
        try:
            cursor = self._conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)", (name, color))
            cursor.execute("SELECT id FROM tags WHERE name = ?", (name,))
            row = cursor.fetchone()
            self._conn.commit()
            return row[0] if row else None
        except sqlite3.Error as e:
            print(f"Error creating tag '{name}': {e}")
            return None

    def delete_tag(self, tag_id):
        if not self._conn: return
        try:
            self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error deleting tag {tag_id}: {e}")

    def get_all_tags(self):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, name, color FROM tags ORDER BY name COLLATE NOCASE")
            return [{"id": row[0], "name": row[1], "color": row[2]} for row in cursor.fetchall()]
        except sqlite3.Error as e:
            if self._is_thread_error(e):
                return self._read_tags_threadsafe("SELECT id, name, color FROM tags ORDER BY name COLLATE NOCASE")
            print(f"Error reading tags: {e}")
            return []

    def assign_tag_to_doc(self, doc_id, tag_id):
        if not self._conn: return
        try:
            self._conn.execute("INSERT OR IGNORE INTO doc_tags (doc_id, tag_id) VALUES (?, ?)", (doc_id, tag_id))
            self._conn.commit()
            self._sync_doc_tags_for_llm(doc_id)
        except sqlite3.Error as e:
            print(f"Error assigning tag {tag_id} to doc {doc_id}: {e}")

    def remove_tag_from_doc(self, doc_id, tag_id):
        if not self._conn: return
        try:
            self._conn.execute("DELETE FROM doc_tags WHERE doc_id = ? AND tag_id = ?", (doc_id, tag_id))
            self._conn.commit()
            self._sync_doc_tags_for_llm(doc_id)
        except sqlite3.Error as e:
            print(f"Error removing tag {tag_id} from doc {doc_id}: {e}")

    def _sync_doc_tags_for_llm(self, doc_id):
        try:
            llm_manager = self.manager.main_window.shared_llm_manager
            llm_manager.sync_doc_tags(doc_id, self.get_tags_for_doc(doc_id))
        except Exception:
            pass

    def assign_tag_to_node(self, node_id, tag_id):
        if not self._conn: return
        try:
            self._conn.execute("INSERT OR IGNORE INTO node_tags (node_id, tag_id) VALUES (?, ?)", (node_id, tag_id))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error assigning tag {tag_id} to node {node_id}: {e}")

    def remove_tag_from_node(self, node_id, tag_id):
        if not self._conn: return
        try:
            self._conn.execute("DELETE FROM node_tags WHERE node_id = ? AND tag_id = ?", (node_id, tag_id))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error removing tag {tag_id} from node {node_id}: {e}")

    def get_tags_for_doc(self, doc_id):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT t.id, t.name, t.color FROM tags t
                INNER JOIN doc_tags dt ON dt.tag_id = t.id WHERE dt.doc_id = ?
                ORDER BY t.name COLLATE NOCASE""", (doc_id,)
            )
            return [{"id": row[0], "name": row[1], "color": row[2]} for row in cursor.fetchall()]
        except sqlite3.Error as e:
            if self._is_thread_error(e):
                return self._read_tags_threadsafe(
                    """SELECT t.id, t.name, t.color FROM tags t
                    INNER JOIN doc_tags dt ON dt.tag_id = t.id WHERE dt.doc_id = ?
                    ORDER BY t.name COLLATE NOCASE""",
                    (doc_id,),
                )
            print(f"Error reading tags for doc {doc_id}: {e}")
            return []

    def get_tags_for_node(self, node_id):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT t.id, t.name, t.color FROM tags t
                INNER JOIN node_tags nt ON nt.tag_id = t.id WHERE nt.node_id = ?
                ORDER BY t.name COLLATE NOCASE""", (node_id,)
            )
            return [{"id": row[0], "name": row[1], "color": row[2]} for row in cursor.fetchall()]
        except sqlite3.Error as e:
            if self._is_thread_error(e):
                return self._read_tags_threadsafe(
                    """SELECT t.id, t.name, t.color FROM tags t
                    INNER JOIN node_tags nt ON nt.tag_id = t.id WHERE nt.node_id = ?
                    ORDER BY t.name COLLATE NOCASE""",
                    (node_id,),
                )
            print(f"Error reading tags for node {node_id}: {e}")
            return []

    def get_docs_for_tag(self, tag_id):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT DISTINCT dt.doc_id FROM doc_tags dt WHERE dt.tag_id = ? ORDER BY dt.doc_id COLLATE NOCASE", (tag_id,))
            return [{"doc_id": row[0], "doc_name": os.path.basename(row[0]) if row[0] else "Unknown Document"} for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Error reading documents for tag {tag_id}: {e}")
            return []

    def _is_thread_error(self, error):
        return "created in a thread" in str(error)

    def _read_tags_threadsafe(self, query, params=()):
        db_path = getattr(self.manager, "project_filepath", None)
        if not db_path:
            return []
        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            try:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return [{"id": row[0], "name": row[1], "color": row[2]} for row in cursor.fetchall()]
            finally:
                conn.close()
        except sqlite3.Error as e:
            print(f"Error reading tags with thread-safe connection: {e}")
            return []
