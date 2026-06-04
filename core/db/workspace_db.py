# core/db/workspace_db.py
import sqlite3
import json
from core.models.workspace_models import NodeModel, EdgeModel, WorkspaceModel
from core.db.base_db import BaseDB

class WorkspaceDB(BaseDB):
    def get_workspaces(self):
        if not self._conn:
            return [{"id": 1, "name": "Main Board"}]
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, name FROM workspaces ORDER BY id")
            return [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Error reading workspaces: {e}")
            return [{"id": 1, "name": "Main Board"}]

    def create_workspace(self, name):
        if not self._conn: return None
        try:
            cursor = self._conn.cursor()
            cursor.execute("INSERT INTO workspaces (name) VALUES (?)", (name,))
            self._conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            print(f"Error creating workspace '{name}': {e}")
            return None

    def rename_workspace(self, workspace_id, name):
        if not self._conn: return
        try:
            self._conn.execute("UPDATE workspaces SET name = ? WHERE id = ?", (name, workspace_id))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error renaming workspace {workspace_id}: {e}")

    def delete_workspace(self, workspace_id):
        if not self._conn or workspace_id == 1: return
        try:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            cursor.execute("DELETE FROM nodes WHERE workspace_id = ?", (workspace_id,))
            cursor.execute("DELETE FROM edges WHERE workspace_id = ?", (workspace_id,))
            cursor.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            print(f"Error deleting workspace {workspace_id}: {e}")

    def get_workspace_data(self, workspace_id=1) -> WorkspaceModel:
        workspace = WorkspaceModel(workspace_id=workspace_id)
        if not self._conn: return workspace
        try:
            cursor = self._conn.cursor()
            try:
                cursor.execute(
                    "SELECT id, highlight_id, workspace_id, quote, note_text, color, is_custom, "
                    "pdf_path, page_num, manual_font_size, x, y, width, height, node_origin, is_verified, original_text "
                    "FROM nodes WHERE workspace_id = ?", (workspace_id,)
                )
                for row in cursor.fetchall():
                    node = NodeModel(
                        id=row[0], highlight_id=row[1], workspace_id=row[2] or 1, quote=row[3], note=row[4],
                        color=row[5], is_custom=bool(row[6]), pdf_path=row[7], page_num=row[8], manual_font_size=row[9],
                        x=row[10], y=row[11], width=row[12], height=row[13], node_origin=row[14] or "human", 
                        is_verified=int(row[15] or 0), original_text=row[16] if row[16] is not None else row[4]
                    )
                    workspace.nodes.append(node)
            except sqlite3.OperationalError:
                cursor.execute(
                    "SELECT id, highlight_id, workspace_id, quote, note_text, color, is_custom, "
                    "pdf_path, page_num, manual_font_size, x, y, width, height "
                    "FROM nodes WHERE workspace_id = ?", (workspace_id,)
                )
                for row in cursor.fetchall():
                    node = NodeModel(
                        id=row[0], highlight_id=row[1], workspace_id=row[2] or 1, quote=row[3], note=row[4],
                        color=row[5], is_custom=bool(row[6]), pdf_path=row[7], page_num=row[8], manual_font_size=row[9],
                        x=row[10], y=row[11], width=row[12], height=row[13], node_origin="human", 
                        is_verified=0, original_text=row[4]
                    )
                    workspace.nodes.append(node)

            cursor.execute("SELECT edge_id, source_id, target_id, label, color, weight FROM edges WHERE workspace_id = ?", (workspace_id,))
            for row in cursor.fetchall():
                edge = EdgeModel(id=row[0], source=row[1], target=row[2], label=row[3], color=row[4], weight=row[5])
                workspace.edges.append(edge)
            return workspace
        except sqlite3.Error as e:
            print(f"Error reading workspace data: {e}")
            return workspace

    def sync_workspace(self, workspace: WorkspaceModel):
        if not self._conn: return
        try:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            incoming_node_ids = [n.id for n in workspace.nodes]
            node_insert_data = [
                (n.id, n.highlight_id, workspace.workspace_id, n.quote, n.note,
                 n.color, int(n.is_custom), n.pdf_path, n.page_num, n.manual_font_size,
                 n.x, n.y, n.width, n.height, n.node_origin, int(n.is_verified), n.original_text)
                for n in workspace.nodes
            ]
            if node_insert_data:
                cursor.executemany("""
                    INSERT OR REPLACE INTO nodes (
                        id, highlight_id, workspace_id, quote, note_text, color,
                        is_custom, pdf_path, page_num, manual_font_size, x, y, width, height, node_origin, is_verified, original_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, node_insert_data)

            if incoming_node_ids:
                placeholders = ",".join("?" for _ in incoming_node_ids)
                cursor.execute(f"DELETE FROM nodes WHERE workspace_id = ? AND id NOT IN ({placeholders})", [workspace.workspace_id] + incoming_node_ids)
            else:
                cursor.execute("DELETE FROM nodes WHERE workspace_id = ?", (workspace.workspace_id,))

            incoming_edge_ids = [e.id for e in workspace.edges]
            edge_insert_data = [(e.id, e.source, e.target, e.label, e.color, int(e.weight), workspace.workspace_id) for e in workspace.edges]
            if edge_insert_data:
                cursor.executemany("""
                    INSERT OR REPLACE INTO edges (edge_id, source_id, target_id, label, color, weight, workspace_id) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, edge_insert_data)

            if incoming_edge_ids:
                placeholders = ",".join("?" for _ in incoming_edge_ids)
                cursor.execute(f"DELETE FROM edges WHERE workspace_id = ? AND edge_id NOT IN ({placeholders})", [workspace.workspace_id] + incoming_edge_ids)
            else:
                cursor.execute("DELETE FROM edges WHERE workspace_id = ?", (workspace.workspace_id,))

            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            print(f"Error syncing workspace: {e}")

    def sync_workspace_delta(self, delta: WorkspaceModel):
        if not self._conn: return
        try:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN TRANSACTION")

            node_data = [
                (n.id, n.highlight_id, delta.workspace_id, n.quote, n.note,
                 n.color, int(n.is_custom), n.pdf_path, n.page_num, n.manual_font_size,
                 n.x, n.y, n.width, n.height, n.node_origin, int(n.is_verified), n.original_text)
                for n in delta.nodes
            ]
            if node_data:
                cursor.executemany("""
                    INSERT OR REPLACE INTO nodes (
                        id, highlight_id, workspace_id, quote, note_text, color,
                        is_custom, pdf_path, page_num, manual_font_size, x, y, width, height, node_origin, is_verified, original_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, node_data)

            if delta.deleted_node_ids:
                placeholders = ",".join("?" for _ in delta.deleted_node_ids)
                cursor.execute(f"DELETE FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})", delta.deleted_node_ids * 2)
                cursor.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", delta.deleted_node_ids)

            edge_data = [(e.id, e.source, e.target, e.label, e.color, int(e.weight), delta.workspace_id) for e in delta.edges]
            if edge_data:
                cursor.executemany("""
                    INSERT OR REPLACE INTO edges (edge_id, source_id, target_id, label, color, weight, workspace_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, edge_data)

            if delta.deleted_edge_ids:
                placeholders = ",".join("?" for _ in delta.deleted_edge_ids)
                cursor.execute(f"DELETE FROM edges WHERE edge_id IN ({placeholders})", delta.deleted_edge_ids)

            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            print(f"Error applying workspace delta: {e}")

    def set_node_verification(self, node_id, is_verified):
        if not self._conn: return
        try:
            status_int = 1 if is_verified else 0
            self._conn.execute("UPDATE nodes SET is_verified = ? WHERE id = ?", (status_int, node_id))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Failed to update verification status: {e}")

    def save_node_embedding_threadsafe(self, node_id, vector):
        if not self.manager.project_filepath: return
        try:
            conn = sqlite3.connect(self.manager.project_filepath, timeout=10.0)
            vector_str = json.dumps(vector)
            conn.execute("UPDATE nodes SET embedding_vector = ? WHERE id = ?", (vector_str, node_id))
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            print(f"Error saving node embedding in background: {e}")

    def save_node_embedding(self, node_id, vector):
        if not self._conn: return
        try:
            vector_str = json.dumps(vector)
            self._conn.execute("UPDATE nodes SET embedding_vector = ? WHERE id = ?", (vector_str, node_id))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving node embedding: {e}")

    def get_node_embeddings_batch(self, node_ids):
        if not self._conn or not node_ids: return {}
        try:
            placeholders = ",".join("?" for _ in node_ids)
            cursor = self._conn.cursor()
            cursor.execute(f"SELECT id, embedding_vector FROM nodes WHERE id IN ({placeholders})", tuple(node_ids))
            
            results = {}
            for row in cursor.fetchall():
                n_id, vec_str = row
                if vec_str:
                    results[n_id] = json.loads(vec_str)
            return results
        except sqlite3.Error:
            return {}