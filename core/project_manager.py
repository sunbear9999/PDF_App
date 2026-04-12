# core/project_manager.py
import sqlite3
import json
import os
import shutil
import tempfile
import fitz

class ProjectManager:
    def __init__(self, max_cache_size=5):
        self.project_filepath = None
        self.project_name = "Untitled Project"
        self.pdfs = []
        
        self.open_docs = {} 
        self.dirty_docs = set()
        self.max_cache_size = max_cache_size
        self.active_file = None
        self._conn = None

    def _init_db(self):
        try:
            if self._conn:
                self._conn.close()
            self._conn = sqlite3.connect(self.project_filepath)
            self._conn.execute("PRAGMA foreign_keys = ON")
            cursor = self._conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS pdfs (path TEXT PRIMARY KEY)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS highlights (
                id TEXT PRIMARY KEY, doc_id TEXT, page_num INTEGER,
                rect_coords TEXT, text_content TEXT, color TEXT
            )''')
            self._ensure_nodes_table(cursor)
            cursor.execute('''CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL
            )''')
            cursor.execute("INSERT OR IGNORE INTO workspaces (id, name) VALUES (1, 'Main Board')")
            self._migrate_workspace_id_to_integer(cursor)
            cursor.execute('''CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT, label TEXT, color TEXT
            )''')
            # Migration: add weight and workspace_id columns if missing
            cursor.execute("PRAGMA table_info(edges)")
            edge_columns = [col[1] for col in cursor.fetchall()]
            if "weight" not in edge_columns:
                try:
                    cursor.execute("ALTER TABLE edges ADD COLUMN weight INTEGER DEFAULT 2")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        print(f"Error adding weight column: {e}")
            if "workspace_id" not in edge_columns:
                try:
                    cursor.execute("ALTER TABLE edges ADD COLUMN workspace_id INTEGER DEFAULT 1")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        print(f"Error adding workspace_id to edges: {e}")
            # Migration: add embedding_vector to nodes
            cursor.execute("PRAGMA table_info(nodes)")
            node_columns = [col[1] for col in cursor.fetchall()]
            if "embedding_vector" not in node_columns:
                try:
                    cursor.execute("ALTER TABLE nodes ADD COLUMN embedding_vector TEXT")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        print(f"Error adding embedding_vector column: {e}")

            cursor.execute('''CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE,
                color TEXT
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS doc_tags (
                doc_id TEXT,
                tag_id INTEGER,
                PRIMARY KEY (doc_id, tag_id),
                FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS node_tags (
                node_id TEXT,
                tag_id INTEGER,
                PRIMARY KEY (node_id, tag_id),
                FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )''')
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Database initialization error: {e}")

    def _ensure_nodes_table(self, cursor):
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nodes'")
        has_nodes_table = cursor.fetchone() is not None

        if not has_nodes_table:
            cursor.execute('''CREATE TABLE nodes (
                id TEXT PRIMARY KEY, highlight_id TEXT, workspace_id TEXT,
                quote TEXT, note_text TEXT, color TEXT, is_custom INTEGER,
                pdf_path TEXT, page_num INTEGER, manual_font_size INTEGER,
                x REAL, y REAL, width REAL, height REAL
            )''')
            return
    def save_node_embedding_threadsafe(self, node_id, vector):
        """Opens a short-lived, localized connection to bypass SQLite thread restrictions."""
        if not self.project_filepath: return
        try:
            # Connect specifically for this thread with a timeout to respect locking
            conn = sqlite3.connect(self.project_filepath, timeout=10.0)
            vector_str = json.dumps(vector)
            conn.execute("UPDATE nodes SET embedding_vector = ? WHERE id = ?", (vector_str, node_id))
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            print(f"Error saving node embedding in background: {e}")

    def save_node_embedding(self, node_id, vector):
        """Standard main-thread save."""
        if not self._conn: return
        try:
            vector_str = json.dumps(vector)
            self._conn.execute("UPDATE nodes SET embedding_vector = ? WHERE id = ?", (vector_str, node_id))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving node embedding: {e}")

    def get_node_embeddings_batch(self, node_ids):
        """Fetches multiple cached embeddings at once to speed up the physics engine."""
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

        cursor.execute("PRAGMA table_info(nodes)")
        columns = [col[1] for col in cursor.fetchall()]
        required_columns = {
            "id", "highlight_id", "workspace_id", "quote", "note_text", "color",
            "is_custom", "pdf_path", "page_num", "manual_font_size", "x", "y",
            "width", "height"
        }
        if required_columns.issubset(set(columns)):
            return

        cursor.execute('''CREATE TABLE nodes_v2 (
            id TEXT PRIMARY KEY, highlight_id TEXT, workspace_id TEXT,
            quote TEXT, note_text TEXT, color TEXT, is_custom INTEGER,
            pdf_path TEXT, page_num INTEGER, manual_font_size INTEGER,
            x REAL, y REAL, width REAL, height REAL
        )''')

        if columns:
            cursor.execute("SELECT node_id, quote, note, color, is_custom, pdf_path, page_num, manual_font_size, x, y, width, height FROM nodes")
            migrated_rows = []
            migrated_highlights = []
            for row in cursor.fetchall():
                node_id, quote, note, color, is_custom, pdf_path, page_num, manual_font_size, x, y, width, height = row
                highlight_id = node_id if not is_custom else None
                migrated_rows.append((
                    node_id,
                    highlight_id,
                    "default",
                    quote,
                    note,
                    color,
                    is_custom,
                    pdf_path,
                    page_num,
                    manual_font_size,
                    x,
                    y,
                    width,
                    height,
                ))
                if highlight_id:
                    migrated_highlights.append((highlight_id, pdf_path, page_num, None, quote, color))

            cursor.executemany(
                """
                INSERT INTO nodes_v2 (
                    id, highlight_id, workspace_id, quote, note_text, color,
                    is_custom, pdf_path, page_num, manual_font_size, x, y, width, height
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                migrated_rows,
            )
            cursor.executemany(
                """
                INSERT OR IGNORE INTO highlights (id, doc_id, page_num, rect_coords, text_content, color)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                migrated_highlights,
            )

        cursor.execute("DROP TABLE nodes")
        cursor.execute("ALTER TABLE nodes_v2 RENAME TO nodes")

    def _migrate_workspace_id_to_integer(self, cursor):
        """Migrate nodes.workspace_id column from TEXT to INTEGER (one-time migration)."""
        cursor.execute("PRAGMA table_info(nodes)")
        cols_info = {col[1]: col[2].upper() for col in cursor.fetchall()}
        if cols_info.get("workspace_id") == "INTEGER":
            return  # Already migrated
        # Recreate nodes table with INTEGER workspace_id
        cursor.execute('''CREATE TABLE IF NOT EXISTS nodes_wsid (
            id TEXT PRIMARY KEY, highlight_id TEXT, workspace_id INTEGER DEFAULT 1,
            quote TEXT, note_text TEXT, color TEXT, is_custom INTEGER,
            pdf_path TEXT, page_num INTEGER, manual_font_size INTEGER,
            x REAL, y REAL, width REAL, height REAL
        )''')
        cursor.execute("""
            INSERT OR IGNORE INTO nodes_wsid
            SELECT id, highlight_id,
                   CASE WHEN workspace_id IS NULL OR workspace_id = 'default' THEN 1
                        ELSE CAST(workspace_id AS INTEGER) END,
                   quote, note_text, color, is_custom,
                   pdf_path, page_num, manual_font_size, x, y, width, height
            FROM nodes
        """)
        cursor.execute("DROP TABLE nodes")
        cursor.execute("ALTER TABLE nodes_wsid RENAME TO nodes")

    def create_project(self, filepath):
        try:
            filepath = filepath.strip()
            if filepath.endswith(".index.json"):
                filepath = filepath.replace(".index.json", "")
            if not filepath.lower().endswith(".pdfproj"):
                filepath += ".pdfproj"
                
            self.project_filepath = filepath
            self.project_name = os.path.basename(filepath).replace(".pdfproj", "")
            self.pdfs = []
            self._clear_cache()
            
            if os.path.exists(filepath):
                os.remove(filepath)
            
            self._init_db()
            cursor = self._conn.cursor()
            cursor.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("project_name", self.project_name))
            self._conn.commit()
        except Exception as e:
            print(f"Error creating project: {e}")
    def _halt_viewer_if_active(self, path):
        """Safely stops the background render thread if the file is currently open in the UI."""
        if hasattr(self, 'main_window') and self.main_window and self.main_window.current_file_path == path:
            viewer = getattr(self.main_window, 'viewer', None)
            if viewer and viewer.worker and viewer.worker.isRunning():
                viewer.worker.stop()
                viewer.worker.wait()
                return viewer
        return None
    def load_project(self, filepath):
        try:
            filepath = filepath.strip()
            if filepath.endswith(".index.json"):
                filepath = filepath.replace(".index.json", "")
                if not os.path.exists(filepath) and os.path.exists(filepath + ".pdfproj"):
                    filepath += ".pdfproj"

            if not os.path.exists(filepath):
                return False
                
            self.project_filepath = filepath
            self.project_name = os.path.basename(filepath).replace(".pdfproj", "")
            self._clear_cache()
            
            try:
                self._init_db()
                cursor = self._conn.cursor()
                cursor.execute("SELECT path FROM pdfs")
                self.pdfs = [row[0] for row in cursor.fetchall()]
            except sqlite3.DatabaseError:
                print("Legacy JSON project detected. Migrating to SQLite...")
                with open(filepath, 'r') as f:
                    data = json.load(f)
                
                if self._conn:
                    self._conn.close()
                os.remove(filepath)
                self._init_db()     
                
                self.pdfs = data.get("pdfs", [])
                cursor = self._conn.cursor()
                for p in self.pdfs:
                    cursor.execute("INSERT OR IGNORE INTO pdfs (path) VALUES (?)", (p,))
                
                self.save_workspace_data(data.get("workspace_data", {"nodes": {}, "edges": []}))
                print("Migration complete!")
                
            return True
        except Exception as e:
            print(f"Error loading project: {e}")
            return False

    def save_project(self):
        """Safely persist .pdfproj by backing up to temp DB then swapping."""
        if not self._conn or not self.project_filepath:
            return
        tmp_db = None
        try:
            self._conn.commit()

            tmp_db = self._create_closed_temp_path(self.project_filepath, suffix=".tmp")
            tmp_conn = sqlite3.connect(tmp_db)
            try:
                self._conn.backup(tmp_conn)
                tmp_conn.commit()
            finally:
                tmp_conn.close()

            # Drop DB lock before swap on Windows
            self._conn.close()
            self._conn = None

            self._safe_swap_file(tmp_db, self.project_filepath)
            tmp_db = None

            self._init_db()
        except Exception as e:
            print(f"Error saving project safely: {e}")
            if tmp_db and os.path.exists(tmp_db):
                os.remove(tmp_db)

    def _safe_swap_file(self, tmp_file, original_file):
        """Windows-safe file swap with fallback."""
        try:
            os.replace(tmp_file, original_file)
            tmp_file = None
        except OSError:
            shutil.copy2(tmp_file, original_file)
            os.remove(tmp_file)
            tmp_file = None
        finally:
            if tmp_file and os.path.exists(tmp_file):
                os.remove(tmp_file)

    def _create_closed_temp_path(self, target_path, suffix=".tmp"):
        target_dir = os.path.dirname(os.path.abspath(target_path)) or os.getcwd()
        os.makedirs(target_dir, exist_ok=True)
        tf = tempfile.NamedTemporaryFile(dir=target_dir, suffix=suffix, delete=False)
        temp_path = tf.name
        tf.close()  # explicit lock release for Windows
        return temp_path

    def add_pdf(self, pdf_path):
        if pdf_path not in self.pdfs:
            self.pdfs.append(pdf_path)
            if self._conn:
                try:
                    self._conn.execute("INSERT OR IGNORE INTO pdfs (path) VALUES (?)", (pdf_path,))
                    self._conn.commit()
                except sqlite3.Error as e:
                    print(f"Error adding PDF to DB: {e}")
            return True
        return False
    def remove_pdf(self, pdf_path):
        """Safely closes handles and removes a PDF from the project, SQLite, and releases it."""
        # 1. Halt the UI background thread first
        self._halt_viewer_if_active(pdf_path)
        
        # 2. Force close PyMuPDF handle to break the OS file lock
        if pdf_path in self.open_docs:
            doc = self.open_docs.pop(pdf_path)
            if not doc.is_closed:
                doc.close()
        self.dirty_docs.discard(pdf_path)
        
        # ... (keep the rest of the remove_pdf code exactly as it is) ...
        
        # 2. Remove from active tracking list
        if pdf_path in self.pdfs:
            self.pdfs.remove(pdf_path)
            
        # 3. Wipe all traces from SQLite
        if self._conn:
            try:
                cursor = self._conn.cursor()
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute("DELETE FROM pdfs WHERE path = ?", (pdf_path,))
                cursor.execute("DELETE FROM highlights WHERE doc_id = ?", (pdf_path,))
                cursor.execute("DELETE FROM nodes WHERE pdf_path = ?", (pdf_path,))
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                print(f"Error removing PDF from DB: {e}")
                
        if self.active_file == pdf_path:
            self.active_file = None
            
        return True

    def rename_pdf(self, old_path, new_path):
        """Renames a PDF file safely, avoiding PyMuPDF lock crashes and updating the DB."""
        if old_path not in self.pdfs: 
            return False
        
        # 1. Force close and save if dirty to drop the lock
        if old_path in self.open_docs:
            doc = self.open_docs.pop(old_path)
            if old_path in self.dirty_docs:
                self._save_single_doc(doc, old_path)
            if not doc.is_closed:
                doc.close()
        self.dirty_docs.discard(old_path)
        
        # 2. Perform actual file system rename
        try:
            os.rename(old_path, new_path)
        except Exception as e:
            print(f"OS Rename failed: {e}")
            return False
            
        # 3. Update active list
        idx = self.pdfs.index(old_path)
        self.pdfs[idx] = new_path
        
        # 4. Update SQLite Cascade
        if self._conn:
            try:
                cursor = self._conn.cursor()
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute("UPDATE pdfs SET path = ? WHERE path = ?", (new_path, old_path))
                cursor.execute("UPDATE highlights SET doc_id = ? WHERE doc_id = ?", (new_path, old_path))
                cursor.execute("UPDATE nodes SET pdf_path = ? WHERE pdf_path = ?", (new_path, old_path))
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                print(f"Error renaming PDF in DB: {e}")
                return False
                
        if self.active_file == old_path:
            self.active_file = new_path
            
        return True

    def get_workspace_data(self, workspace_id=1):
        if not self._conn: return {"nodes": {}, "edges": []}
        try:
            cursor = self._conn.cursor()

            nodes = {}
            cursor.execute(
                "SELECT id, highlight_id, workspace_id, quote, note_text, color, is_custom, "
                "pdf_path, page_num, manual_font_size, x, y, width, height "
                "FROM nodes WHERE workspace_id = ?",
                (workspace_id,),
            )
            for row in cursor.fetchall():
                node_id, highlight_id, ws_id, quote, note, color, is_custom, pdf_path, page_num, font_size, x, y, w, h = row
                nodes[node_id] = {
                    "highlight_id": highlight_id, "workspace_id": ws_id or 1,
                    "quote": quote, "note": note, "color": color, "is_custom": bool(is_custom),
                    "pdf_path": pdf_path, "page_num": page_num, "manual_font_size": font_size,
                    "x": x, "y": y, "width": w, "height": h
                }

            edges = []
            try:
                cursor.execute(
                    "SELECT edge_id, source_id, target_id, label, color, weight "
                    "FROM edges WHERE workspace_id = ?",
                    (workspace_id,),
                )
            except sqlite3.OperationalError:
                # workspace_id column not yet added (first run before migration commits)
                cursor.execute("SELECT edge_id, source_id, target_id, label, color, weight FROM edges")
            for row in cursor.fetchall():
                edge_id, source_id, target_id, label, color, weight = row
                edges.append({
                    "id": edge_id, "source": source_id, "target": target_id,
                    "label": label, "color": color, "weight": weight
                })
            return {"nodes": nodes, "edges": edges}
        except sqlite3.Error as e:
            print(f"Error reading workspace data: {e}")
            return {"nodes": {}, "edges": []}

    def save_workspace_data(self, workspace_data, workspace_id=1):
        """OPTIMIZED: Uses bulk transactions for massive speedup when saving large workspaces.
        Only touches nodes/edges belonging to the given workspace_id."""
        if not self._conn: return
        try:
            cursor = self._conn.cursor()

            cursor.execute("BEGIN TRANSACTION")
            cursor.execute("DELETE FROM nodes WHERE workspace_id = ?", (workspace_id,))
            cursor.execute("DELETE FROM edges WHERE workspace_id = ?", (workspace_id,))

            nodes = workspace_data.get("nodes", {})
            node_insert_data = [
                (
                    n_id,
                    d.get("highlight_id"),
                    workspace_id,
                    d.get("quote"),
                    d.get("note"),
                    d.get("color"),
                    int(d.get("is_custom", 0)),
                    d.get("pdf_path"),
                    d.get("page_num"),
                    d.get("manual_font_size"),
                    d.get("x"),
                    d.get("y"),
                    d.get("width"),
                    d.get("height"),
                )
                for n_id, d in nodes.items()
            ]

            cursor.executemany("""
                INSERT INTO nodes (
                    id, highlight_id, workspace_id, quote, note_text, color,
                    is_custom, pdf_path, page_num, manual_font_size, x, y, width, height
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, node_insert_data)

            edges = workspace_data.get("edges", [])
            edge_insert_data = [
                (e.get("id"), e.get("source"), e.get("target"), e.get("label"), e.get("color"), int(e.get("weight", 2)), workspace_id)
                for e in edges
            ]
            cursor.executemany("""
                INSERT INTO edges (edge_id, source_id, target_id, label, color, weight, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, edge_insert_data)

            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            print(f"Error saving workspace data: {e}")

    def get_metadata(self, key, default=None):
        if not self._conn:
            return default
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
        except sqlite3.Error as e:
            print(f"Error reading metadata {key}: {e}")
            return default

    def set_metadata(self, key, value):
        if not self._conn:
            return
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving metadata {key}: {e}")

    def upsert_highlight(self, highlight_data):
        if not self._conn:
            return
        try:
            self._conn.execute(
                """
                INSERT INTO highlights (id, doc_id, page_num, rect_coords, text_content, color)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    doc_id = excluded.doc_id,
                    page_num = excluded.page_num,
                    rect_coords = excluded.rect_coords,
                    text_content = excluded.text_content,
                    color = excluded.color
                """,
                (
                    highlight_data.get("id"),
                    highlight_data.get("doc_id"),
                    highlight_data.get("page_num"),
                    highlight_data.get("rect_coords"),
                    highlight_data.get("text_content", ""),
                    highlight_data.get("color"),
                ),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error upserting highlight {highlight_data.get('id')}: {e}")

    def get_highlights(self):
        if not self._conn:
            return {}
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, doc_id, page_num, rect_coords, text_content, color FROM highlights")
            return {
                row[0]: {
                    "id": row[0],
                    "doc_id": row[1],
                    "page_num": row[2],
                    "rect_coords": row[3],
                    "text_content": row[4],
                    "color": row[5],
                }
                for row in cursor.fetchall()
            }
        except sqlite3.Error as e:
            print(f"Error reading highlights: {e}")
            return {}

    def get_highlight(self, highlight_id):
        return self.get_highlights().get(highlight_id)

    def get_unused_highlights(self, workspace_id):
        if not self._conn:
            return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT h.id, h.doc_id, h.page_num, h.rect_coords, h.text_content, h.color
                FROM highlights h
                WHERE h.id NOT IN (
                    SELECT COALESCE(n.highlight_id, n.id)
                    FROM nodes n
                    WHERE n.workspace_id = ?
                )
                ORDER BY h.doc_id, h.page_num, h.id
                """,
                (workspace_id,),
            )
            return [
                {
                    "id": row[0],
                    "doc_id": row[1],
                    "page_num": row[2],
                    "rect_coords": row[3],
                    "text_content": row[4],
                    "color": row[5],
                }
                for row in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            print(f"Error reading unused highlights for workspace {workspace_id}: {e}")
            return []

    def delete_node_record(self, node_id):
        if not self._conn:
            return
        try:
            self._conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error deleting node {node_id}: {e}")

    def delete_edge_record(self, edge_id):
        if not self._conn:
            return
        try:
            self._conn.execute("DELETE FROM edges WHERE edge_id = ?", (edge_id,))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error deleting edge {edge_id}: {e}")

    def delete_highlight_record(self, highlight_id):
        if not self._conn:
            return
        try:
            self._conn.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error deleting highlight {highlight_id}: {e}")

    # ------------------------------------------------------------------ workspaces

    def get_workspaces(self):
        """Return list of all workspaces as dicts with 'id' and 'name'."""
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
        """Insert a new workspace row and return its new id, or None on error."""
        if not self._conn:
            return None
        try:
            cursor = self._conn.cursor()
            cursor.execute("INSERT INTO workspaces (name) VALUES (?)", (name,))
            self._conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            print(f"Error creating workspace '{name}': {e}")
            return None

    def rename_workspace(self, workspace_id, name):
        if not self._conn:
            return
        try:
            self._conn.execute("UPDATE workspaces SET name = ? WHERE id = ?", (name, workspace_id))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error renaming workspace {workspace_id}: {e}")

    def delete_workspace(self, workspace_id):
        """Delete a workspace and all its nodes/edges. Workspace 1 is protected."""
        if not self._conn or workspace_id == 1:
            return
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

    def upsert_node_record(self, node_data, workspace_id):
        """Insert or replace a single node record into the given workspace."""
        if not self._conn:
            return
        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO nodes (
                    id, highlight_id, workspace_id, quote, note_text, color,
                    is_custom, pdf_path, page_num, manual_font_size, x, y, width, height
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_data.get("id"),
                    node_data.get("highlight_id"),
                    workspace_id,
                    node_data.get("quote"),
                    node_data.get("note"),
                    node_data.get("color"),
                    int(node_data.get("is_custom", 0)),
                    node_data.get("pdf_path"),
                    node_data.get("page_num"),
                    node_data.get("manual_font_size"),
                    node_data.get("x", 0.0),
                    node_data.get("y", 0.0),
                    node_data.get("width", 150.0),
                    node_data.get("height", 80.0),
                ),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error upserting node {node_data.get('id')}: {e}")

    # ------------------------------------------------------------------ tags

    def create_tag(self, name, color):
        """Insert a new tag if needed and return its id."""
        if not self._conn:
            return None
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)",
                (name, color),
            )
            cursor.execute("SELECT id FROM tags WHERE name = ?", (name,))
            row = cursor.fetchone()
            self._conn.commit()
            return row[0] if row else None
        except sqlite3.Error as e:
            print(f"Error creating tag '{name}': {e}")
            return None

    def delete_tag(self, tag_id):
        if not self._conn:
            return
        try:
            self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error deleting tag {tag_id}: {e}")

    def get_all_tags(self):
        if not self._conn:
            return []
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, name, color FROM tags ORDER BY name COLLATE NOCASE")
            return [
                {"id": row[0], "name": row[1], "color": row[2]}
                for row in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            print(f"Error reading tags: {e}")
            return []

    def assign_tag_to_doc(self, doc_id, tag_id):
        if not self._conn:
            return
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO doc_tags (doc_id, tag_id) VALUES (?, ?)",
                (doc_id, tag_id),
            )
            self._conn.commit()
            self._sync_doc_tags_for_llm(doc_id)
        except sqlite3.Error as e:
            print(f"Error assigning tag {tag_id} to doc {doc_id}: {e}")

    def remove_tag_from_doc(self, doc_id, tag_id):
        if not self._conn:
            return
        try:
            self._conn.execute(
                "DELETE FROM doc_tags WHERE doc_id = ? AND tag_id = ?",
                (doc_id, tag_id),
            )
            self._conn.commit()
            self._sync_doc_tags_for_llm(doc_id)
        except sqlite3.Error as e:
            print(f"Error removing tag {tag_id} from doc {doc_id}: {e}")

    def _sync_doc_tags_for_llm(self, doc_id):
        """Best-effort sync of document tags into Chroma metadata for tag filtering."""
        try:
            llm_manager = self.main_window.tabs["LLM Chat"].llm_manager
            llm_manager.sync_doc_tags(doc_id, self.get_tags_for_doc(doc_id))
        except Exception:
            # Keep SQLite tag updates resilient even if the UI/LLM layer is unavailable.
            pass

    def assign_tag_to_node(self, node_id, tag_id):
        if not self._conn:
            return
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO node_tags (node_id, tag_id) VALUES (?, ?)",
                (node_id, tag_id),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error assigning tag {tag_id} to node {node_id}: {e}")

    def remove_tag_from_node(self, node_id, tag_id):
        if not self._conn:
            return
        try:
            self._conn.execute(
                "DELETE FROM node_tags WHERE node_id = ? AND tag_id = ?",
                (node_id, tag_id),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error removing tag {tag_id} from node {node_id}: {e}")

    def get_tags_for_doc(self, doc_id):
        if not self._conn:
            return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT t.id, t.name, t.color
                FROM tags t
                INNER JOIN doc_tags dt ON dt.tag_id = t.id
                WHERE dt.doc_id = ?
                ORDER BY t.name COLLATE NOCASE
                """,
                (doc_id,),
            )
            return [
                {"id": row[0], "name": row[1], "color": row[2]}
                for row in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            print(f"Error reading tags for doc {doc_id}: {e}")
            return []

    def get_tags_for_node(self, node_id):
        if not self._conn:
            return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT t.id, t.name, t.color
                FROM tags t
                INNER JOIN node_tags nt ON nt.tag_id = t.id
                WHERE nt.node_id = ?
                ORDER BY t.name COLLATE NOCASE
                """,
                (node_id,),
            )
            return [
                {"id": row[0], "name": row[1], "color": row[2]}
                for row in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            print(f"Error reading tags for node {node_id}: {e}")
            return []

    def get_docs_for_tag(self, tag_id):
        if not self._conn:
            return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT dt.doc_id
                FROM doc_tags dt
                WHERE dt.tag_id = ?
                ORDER BY dt.doc_id COLLATE NOCASE
                """,
                (tag_id,),
            )
            return [
                {"doc_id": row[0], "doc_name": os.path.basename(row[0]) if row[0] else "Unknown Document"}
                for row in cursor.fetchall()
            ]
        except sqlite3.Error as e:
            print(f"Error reading documents for tag {tag_id}: {e}")
            return []

    def set_active_file(self, filepath):
        self.active_file = filepath

    def mark_dirty(self, filepath):
        if filepath:
            self.dirty_docs.add(filepath)

    def _clear_cache(self):
        try:
            for doc in self.open_docs.values():
                if not doc.is_closed: doc.close()
            self.open_docs.clear()
            self.dirty_docs.clear()
            self.active_file = None
        except Exception as e: 
            print(f"Error clearing cache: {e}")

    def get_doc(self, filepath):
        try:
            if filepath in self.open_docs:
                doc = self.open_docs.pop(filepath)
                self.open_docs[filepath] = doc
                return doc
            
            self._evict_old_docs()
            self.open_docs[filepath] = fitz.open(filepath)
            return self.open_docs[filepath]
        except Exception as e:
            print(f"Failed to open document {filepath}: {e}")
            return None

    def _evict_old_docs(self):
        try:
            while len(self.open_docs) >= self.max_cache_size:
                evict_candidate = next((p for p in self.open_docs if p != self.active_file), None)
                if not evict_candidate: break 
                    
                doc = self.open_docs.pop(evict_candidate)
                if evict_candidate in self.dirty_docs:
                    self._save_single_doc(doc, evict_candidate)
                
                if not doc.is_closed: doc.close()
        except Exception as e:
            print(f"Error evicting docs: {e}")

    def _save_single_doc(self, doc, path):
        if not doc or doc.is_closed:
            return
            
        # 1. HALT background thread before touching file locks
        viewer = self._halt_viewer_if_active(path)

        temp_path = None
        try:
            temp_path = self._create_closed_temp_path(path, suffix=".tmp_save")
            doc.save(temp_path, garbage=3, deflate=True)
            doc.close() # Now totally safe, no background thread is reading it
            self._safe_swap_file(temp_path, path)
            temp_path = None
            self.dirty_docs.discard(path)
        except Exception as e:
            print(f"Primary save failed for {path}: {e}")
            if not doc.is_closed:
                try:
                    pdf_bytes = doc.write()
                    doc.close()
                    temp_path = self._create_closed_temp_path(path, suffix=".tmp_write")
                    with open(temp_path, "wb") as f:
                        f.write(pdf_bytes)
                        f.flush()
                        os.fsync(f.fileno())
                    self._safe_swap_file(temp_path, path)
                    temp_path = None
                    self.dirty_docs.discard(path)
                except Exception as fallback_e:
                    print(f"Full save fallback failed for {path}: {fallback_e}")
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        finally:
            # 2. Reopen and RESUME the background thread with the fresh handle
            if path in self.open_docs or viewer: 
                try:
                    new_doc = fitz.open(path)
                    self.open_docs[path] = new_doc
                    if viewer:
                        viewer.swap_document_handle(new_doc)
                except Exception as reopen_e:
                    print(f"Failed to reopen {path} after save: {reopen_e}")
                    self.open_docs.pop(path, None)

    def save_all_docs(self):
        try:
            for path, doc in list(self.open_docs.items()):
                if path in self.dirty_docs and path != "workspace":
                    self._save_single_doc(doc, path)
            self.dirty_docs.discard("workspace")
        except Exception as e:
            print(f"Error during save_all_docs: {e}")