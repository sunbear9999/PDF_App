# core/db/tag_db.py
import sqlite3
import os
from core.db.base_db import BaseDB

class TagDB(BaseDB):
    @staticmethod
    def _tag_from_row(row):
        return {
            "id": row[0], "name": row[1], "color": row[2],
            "parent_tag_id": row[3],
        }

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
            cursor.execute(
                "SELECT id, name, color, parent_tag_id FROM tags ORDER BY name COLLATE NOCASE"
            )
            return [self._tag_from_row(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Error reading tags: {e}")
            return []

    def query_tags(self, limit=200):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Choose a bounded tag page.')
        if not self._conn:
            return []
        rows = self._conn.execute(
            'SELECT id,name,color,parent_tag_id FROM tags ORDER BY name COLLATE NOCASE LIMIT ?',
            (limit,),
        ).fetchall()
        return [self._tag_from_row(row) for row in rows]

    def assign_tag_to_doc(self, doc_id, tag_id):
        if not self._conn: return
        try:
            self._conn.execute("INSERT OR IGNORE INTO doc_tags (doc_id, tag_id) VALUES (?, ?)", (doc_id, tag_id))
            self._conn.commit()
            self._sync_doc_tags_for_llm(doc_id)
        except sqlite3.Error as e:
            print(f"Error assigning tag {tag_id} to doc {doc_id}: {e}")

    def assign_tag_to_docs(self, doc_ids, tag_id):
        """Assign one tag to many sources in one transaction."""
        if not self._conn or tag_id is None:
            return 0
        paths = list(dict.fromkeys(path for path in doc_ids if path))
        changed = 0
        try:
            cursor = self._conn.cursor()
            for path in paths:
                cursor.execute(
                    "INSERT OR IGNORE INTO doc_tags (doc_id, tag_id) VALUES (?, ?)",
                    (path, tag_id),
                )
                changed += max(cursor.rowcount, 0)
            self._conn.commit()
            for path in paths:
                self._sync_doc_tags_for_llm(path)
            return changed
        except sqlite3.Error as exc:
            self._conn.rollback()
            print(f"Error assigning tag {tag_id} to documents: {exc}")
            return 0

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
                """SELECT t.id, t.name, t.color, t.parent_tag_id FROM tags t
                INNER JOIN doc_tags dt ON dt.tag_id = t.id WHERE dt.doc_id = ?
                ORDER BY t.name COLLATE NOCASE""", (doc_id,)
            )
            return [self._tag_from_row(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Error reading tags for doc {doc_id}: {e}")
            return []

    def get_doc_tag_map(self):
        """Return every document-tag assignment in one uncapped project query."""
        if not self._conn:
            return {}
        try:
            rows = self._conn.execute(
                """SELECT dt.doc_id, t.id, t.name, t.color, t.parent_tag_id
                   FROM doc_tags dt JOIN tags t ON t.id = dt.tag_id
                   ORDER BY dt.doc_id, t.name COLLATE NOCASE"""
            ).fetchall()
            result = {}
            for doc_id, tag_id, name, color, parent_tag_id in rows:
                result.setdefault(doc_id, []).append(
                    {
                        "id": tag_id, "name": name, "color": color,
                        "parent_tag_id": parent_tag_id,
                    }
                )
            return result
        except sqlite3.Error as exc:
            print(f"Error reading document tag assignments: {exc}")
            return {}

    def get_tags_for_node(self, node_id):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT t.id, t.name, t.color, t.parent_tag_id FROM tags t
                INNER JOIN node_tags nt ON nt.tag_id = t.id WHERE nt.node_id = ?
                ORDER BY t.name COLLATE NOCASE""", (node_id,)
            )
            return [self._tag_from_row(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
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

    def rename_tag(self, tag_id, new_name):
        if not self._conn: return
        try:
            self._conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error renaming tag {tag_id}: {e}")

    def update_tag_color(self, tag_id, color):
        if not self._conn: return
        try:
            self._conn.execute("UPDATE tags SET color = ? WHERE id = ?", (color, tag_id))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error updating tag color {tag_id}: {e}")

    def set_tag_parent(self, tag_id, parent_tag_id):
        """Set an explicit tag parent, rejecting missing tags and cycles."""
        if not self._conn:
            return False
        if parent_tag_id == tag_id:
            raise ValueError("A tag cannot be nested under itself.")
        self._require_tag(tag_id)
        if parent_tag_id is not None:
            self._require_tag(parent_tag_id)
            if self._would_create_cycle(tag_id, parent_tag_id):
                raise ValueError("That parent would create a tag hierarchy cycle.")
        self._conn.execute(
            "UPDATE tags SET parent_tag_id = ? WHERE id = ?",
            (parent_tag_id, tag_id),
        )
        self._conn.commit()
        return True

    def _require_tag(self, tag_id):
        row = self._conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if row is None:
            raise ValueError("The selected tag no longer exists.")

    def _would_create_cycle(self, tag_id, parent_tag_id):
        row = self._conn.execute(
            """WITH RECURSIVE ancestors(id, parent_tag_id) AS (
                   SELECT id, parent_tag_id FROM tags WHERE id = ?
                   UNION ALL
                   SELECT t.id, t.parent_tag_id FROM tags t
                   JOIN ancestors a ON t.id = a.parent_tag_id
               )
               SELECT 1 FROM ancestors WHERE id = ? LIMIT 1""",
            (parent_tag_id, tag_id),
        ).fetchone()
        return row is not None
