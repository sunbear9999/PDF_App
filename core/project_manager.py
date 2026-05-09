# core/project_manager.py
import sqlite3
import json
import os
import shutil
import tempfile
import fitz
import sys
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
        if getattr(sys, 'frozen', False):
            root_dir = sys._MEIPASS
        else:
            root_dir = os.path.abspath(os.path.dirname(__file__))
        self.templates_path = os.path.join(root_dir, "analysis_templates.json")
        self._ensure_default_templates()

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
            cursor.execute('''CREATE TABLE IF NOT EXISTS essays (
            id TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            last_edited DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        # Add this inside _init_db(self), right after the essays table creation
            cursor.execute('''CREATE TABLE IF NOT EXISTS citations (
            doc_id TEXT PRIMARY KEY,
            title TEXT,
            authors TEXT,
            year TEXT,
            journal TEXT,
            doi TEXT
             )''')
            self._conn.commit()
            

            # --- NEW ANALYSIS TABLES ---
            cursor.execute('''CREATE TABLE IF NOT EXISTS analysis_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT UNIQUE,
                prompt_instructions TEXT,
                json_schema TEXT
            )''')

        

            # 2. Recreate the table with text-based template_id and NO foreign keys
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS document_analyses (
                    doc_path TEXT,
                    template_id TEXT,
                    chunk_index INTEGER,
                    json_data TEXT
                )
            ''')
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Database initialization error: {e}")
        try:
            # 1. Safely add Tracking columns to existing nodes
            cursor.execute("PRAGMA table_info(nodes)")
            node_columns = [col[1] for col in cursor.fetchall()]
            
            if "node_origin" not in node_columns:
                try:
                    cursor.execute("ALTER TABLE nodes ADD COLUMN node_origin TEXT DEFAULT 'human'")
                except sqlite3.OperationalError:
                    pass
                    
            if "is_verified" not in node_columns:
                try:
                    cursor.execute("ALTER TABLE nodes ADD COLUMN is_verified INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass

            # 2. Create the AI Audit Log Table
            cursor.execute('''CREATE TABLE IF NOT EXISTS ai_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                prompt TEXT,
                response TEXT,
                model_used TEXT
            )''')
            
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Database initialization error (Tracking Features): {e}")
        if "node_origin" not in node_columns:
                try: cursor.execute("ALTER TABLE nodes ADD COLUMN node_origin TEXT DEFAULT 'human'")
                except: pass
        if "is_verified" not in node_columns:
                try: cursor.execute("ALTER TABLE nodes ADD COLUMN is_verified INTEGER DEFAULT 0")
                except: pass
        if "original_text" not in node_columns:
                try: cursor.execute("ALTER TABLE nodes ADD COLUMN original_text TEXT")
                except: pass
                
        cursor.execute('''CREATE TABLE IF NOT EXISTS ai_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                prompt TEXT,
                response TEXT,
                model_used TEXT
            )''')
    def upsert_citation(self, citation_data):
        if not self._conn: return
        try:
            self._conn.execute("""
                INSERT INTO citations (doc_id, title, authors, year, journal, doi)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    title = excluded.title,
                    authors = excluded.authors,
                    year = excluded.year,
                    journal = excluded.journal,
                    doi = excluded.doi
            """, (
                citation_data.get("doc_id"), citation_data.get("title", ""),
                citation_data.get("authors", ""), citation_data.get("year", ""),
                citation_data.get("journal", ""), citation_data.get("doi", "")
            ))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving citation for {citation_data.get('doc_id')}: {e}")
    def _ensure_default_templates(self):
        """Creates default editable templates if the user hasn't made any yet."""
        if not os.path.exists(self.templates_path):
            defaults = [
                {
                    "id": "default_argument",
                    "title": "Argument Structure",
                    "instructions": "Extract the core claims and supporting logic from this text.",
                    "schema": '{\n  "section_summary": "1 sentence",\n  "core_claims": [\n    {"claim": "string", "logic": "string"}\n  ]\n}'
                }
            ]
            self.save_analysis_templates(defaults)

    def get_analysis_templates(self):
        try:
            with open(self.templates_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def save_analysis_templates(self, templates_list):
        with open(self.templates_path, 'w', encoding='utf-8') as f:
            json.dump(templates_list, f, indent=4)

    def save_document_analysis(self, doc_path, template_id, chunk_index, json_data):
        if not self._conn: return
        self._conn.execute(
            "INSERT INTO document_analyses (doc_path, template_id, chunk_index, json_data) VALUES (?, ?, ?, ?)",
            (doc_path, template_id, chunk_index, json_data)
        )
        self._conn.commit()

    def get_document_analyses(self, doc_path, template_id):
        if not self._conn: return []
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT chunk_index, json_data FROM document_analyses WHERE doc_path = ? AND template_id = ? ORDER BY chunk_index",
            (doc_path, template_id)
        )
        return [{"chunk_index": r[0], "json_data": r[1]} for r in cursor.fetchall()]
    
    def clear_document_analyses(self, doc_path, template_id):
        if not self._conn: return
        self._conn.execute("DELETE FROM document_analyses WHERE doc_path = ? AND template_id = ?", (doc_path, template_id))
        self._conn.commit()
    def get_citation(self, doc_id):
        if not self._conn: return {}
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT title, authors, year, journal, doi FROM citations WHERE doc_id = ?", (doc_id,))
            row = cursor.fetchone()
            if row:
                return {"doc_id": doc_id, "title": row[0], "authors": row[1], "year": row[2], "journal": row[3], "doi": row[4]}
            return {}
        except sqlite3.Error as e:
            print(f"Error reading citation for {doc_id}: {e}")
            return {}

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
    def log_ai_interaction(self, prompt, response, model):
        """Silently logs AI interactions for the LLM Log."""
        if not self._conn: return
        try:
            self._conn.execute(
                "INSERT INTO ai_audit_log (prompt, response, model_used) VALUES (?, ?, ?)",
                (prompt, response, model)
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Failed to log AI interaction: {e}")

    def set_node_verification(self, node_id, is_verified):
        """Toggles the human-verified status of an AI-generated note."""
        if not self._conn: return
        try:
            status_int = 1 if is_verified else 0
            self._conn.execute(
                "UPDATE nodes SET is_verified = ? WHERE id = ?",
                (status_int, node_id)
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Failed to update verification status: {e}")
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
    # ADD TO: core/project_manager.py
   # UPDATE THIS IN: core/project_manager.py
    def upsert_essay(self, essay_id, title, content):
        if not self._conn: return
        try:
            # Bulletproof check: Ensure the table exists on the legacy DB before writing
            self._conn.execute('''CREATE TABLE IF NOT EXISTS essays (
                id TEXT PRIMARY KEY,
                title TEXT,
                content TEXT,
                last_edited DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            
            self._conn.execute(
                """
                INSERT INTO essays (id, title, content, last_edited)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    last_edited = CURRENT_TIMESTAMP
                """,
                (essay_id, title, content)
            )
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving essay {essay_id}: {e}")

    def get_all_essays(self):
        if not self._conn: return []
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, title, content FROM essays ORDER BY last_edited DESC")
            return [{"id": row[0], "title": row[1], "content": row[2]} for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Error reading essays: {e}")
            return []
            
    def get_essay(self, essay_id):
        if not self._conn: return None
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT id, title, content FROM essays WHERE id = ?", (essay_id,))
            row = cursor.fetchone()
            if row:
                return {"id": row[0], "title": row[1], "content": row[2]}
            return None
        except sqlite3.Error as e:
            print(f"Error loading essay {essay_id}: {e}")
            return None

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
                
                # ---> ADD THIS LINE TO SAVE THE TAGS <---
                cursor.execute("UPDATE doc_tags SET doc_id = ? WHERE doc_id = ?", (new_path, old_path))
                
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
            try:
                cursor.execute(
                    "SELECT id, highlight_id, workspace_id, quote, note_text, color, is_custom, "
                    "pdf_path, page_num, manual_font_size, x, y, width, height, node_origin, is_verified, original_text "
                    "FROM nodes WHERE workspace_id = ?", (workspace_id,)
                )
                for row in cursor.fetchall():
                    node_id, highlight_id, ws_id, quote, note, color, is_custom, pdf_path, page_num, font_size, x, y, w, h, origin, verified, orig_text = row
                    nodes[node_id] = {
                        "highlight_id": highlight_id, "workspace_id": ws_id or 1,
                        "quote": quote, "note": note, "color": color, "is_custom": bool(is_custom),
                        "pdf_path": pdf_path, "page_num": page_num, "manual_font_size": font_size,
                        "x": x, "y": y, "width": w, "height": h,
                        "node_origin": origin or "human", "is_verified": int(verified or 0),
                        "original_text": orig_text if orig_text is not None else note
                    }
            except sqlite3.OperationalError:
                cursor.execute(
                    "SELECT id, highlight_id, workspace_id, quote, note_text, color, is_custom, "
                    "pdf_path, page_num, manual_font_size, x, y, width, height "
                    "FROM nodes WHERE workspace_id = ?", (workspace_id,)
                )
                for row in cursor.fetchall():
                    node_id, highlight_id, ws_id, quote, note, color, is_custom, pdf_path, page_num, font_size, x, y, w, h = row
                    nodes[node_id] = {
                        "highlight_id": highlight_id, "workspace_id": ws_id or 1,
                        "quote": quote, "note": note, "color": color, "is_custom": bool(is_custom),
                        "pdf_path": pdf_path, "page_num": page_num, "manual_font_size": font_size,
                        "x": x, "y": y, "width": w, "height": h,
                        "node_origin": "human", "is_verified": 0, "original_text": note
                    }

            edges = []
            cursor.execute("SELECT edge_id, source_id, target_id, label, color, weight FROM edges WHERE workspace_id = ?", (workspace_id,))
            for row in cursor.fetchall():
                edge_id, source_id, target_id, label, color, weight = row
                edges.append({
                    "id": edge_id, "source": source_id, "target": target_id,
                    "label": label, "color": color, "weight": weight
                })
            return {"nodes": nodes, "edges": edges}
        except sqlite3.Error as e:
            return {"nodes": {}, "edges": []}

    def save_workspace_data(self, workspace_data, workspace_id=1):
        if not self._conn: return
        try:
            cursor = self._conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            cursor.execute("DELETE FROM nodes WHERE workspace_id = ?", (workspace_id,))
            cursor.execute("DELETE FROM edges WHERE workspace_id = ?", (workspace_id,))

            nodes = workspace_data.get("nodes", {})
            node_insert_data = [
                (
                    n_id, d.get("highlight_id"), workspace_id, d.get("quote"), d.get("note"),
                    d.get("color"), int(d.get("is_custom", 0)), d.get("pdf_path"),
                    d.get("page_num"), d.get("manual_font_size"), d.get("x"), d.get("y"),
                    d.get("width"), d.get("height"), d.get("node_origin", "human"), 
                    int(d.get("is_verified", 0)), d.get("original_text", d.get("note", "")) # <--- 17th ITEM
                )
                for n_id, d in nodes.items()
            ]

            cursor.executemany("""
                INSERT INTO nodes (
                    id, highlight_id, workspace_id, quote, note_text, color,
                    is_custom, pdf_path, page_num, manual_font_size, x, y, width, height, node_origin, is_verified, original_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            
            # 🔥 FIX: Replaced NOT IN with NOT EXISTS.
            # This safely processes highlights that were imported with NULL or missing IDs
            # without SQLite silently dropping them from the results.
            cursor.execute(
                """
                SELECT h.id, h.doc_id, h.page_num, h.rect_coords, h.text_content, h.color
                FROM highlights h
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM nodes n
                    WHERE n.workspace_id = ?
                    AND (n.highlight_id = h.id OR n.id = h.id)
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
        if not self._conn: return
        try:
            cursor = self._conn.cursor()
            
            # --- CRITICAL FIX: Check if an original backup already exists in the DB ---
            cursor.execute("SELECT original_text FROM nodes WHERE id = ?", (node_data.get("id"),))
            row = cursor.fetchone()
            
            # 1. If passed explicitly from the UI, use it.
            # 2. If it's already safely in the DB, KEEP IT (don't overwrite it).
            # 3. Otherwise, it's a brand new note, so back up the current text.
            if "original_text" in node_data:
                orig_text = node_data["original_text"]
            elif row and row[0] is not None:
                orig_text = row[0] 
            else:
                orig_text = node_data.get("note", "")
                
            cursor.execute(
                """
                INSERT OR REPLACE INTO nodes (
                    id, highlight_id, workspace_id, quote, note_text, color,
                    is_custom, pdf_path, page_num, manual_font_size, x, y, width, height, node_origin, is_verified, original_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_data.get("id"), node_data.get("highlight_id"), workspace_id,
                    node_data.get("quote"), node_data.get("note"), node_data.get("color"),
                    int(node_data.get("is_custom", 0)), node_data.get("pdf_path"),
                    node_data.get("page_num"), node_data.get("manual_font_size"),
                    node_data.get("x", 0.0), node_data.get("y", 0.0),
                    node_data.get("width", 150.0), node_data.get("height", 80.0),
                    node_data.get("node_origin", "human"), int(node_data.get("is_verified", 0)),
                    orig_text # <--- Safely injected here
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
    def generate_llm_log(self, export_path):
        """Generates a Markdown audit trail of all AI interactions and calculates the Human-to-AI ratio."""
        if not self._conn: return False
        
        try:
            cursor = self._conn.cursor()
            
            # 1. Tally the Evidence Pedigree (Bulletproofed SQL)
            cursor.execute("""
                SELECT COUNT(*) FROM nodes 
                WHERE node_origin = 'human' 
                AND id NOT LIKE '%AINote|%' 
                AND (highlight_id IS NULL OR highlight_id NOT LIKE '%AINote|%')
            """)
            n_h = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(*) FROM nodes 
                WHERE (node_origin = 'ai' OR id LIKE '%AINote|%' OR highlight_id LIKE '%AINote|%') 
                AND is_verified = 1
            """)
            n_v = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(*) FROM nodes 
                WHERE (node_origin = 'ai' OR id LIKE '%AINote|%' OR highlight_id LIKE '%AINote|%') 
                AND is_verified = 0
            """)
            n_ai_unverified = cursor.fetchone()[0]
            
            # 2. Calculate the Metrics
            # ... (leave the rest exactly as it is) ...
            
            # 2. Calculate the Metrics
            total_ai_notes = n_v + n_ai_unverified
            verification_score = (n_v / total_ai_notes * 100) if total_ai_notes > 0 else 100.0
            
            # Ratio calculation (prevent division by zero)
            denominator = max(1, n_ai_unverified) 
            integrity_ratio = (n_h + n_v) / denominator
            
            # 3. Fetch the Audit Log
            cursor.execute("SELECT timestamp, prompt, response, model_used FROM ai_audit_log ORDER BY timestamp ASC")
            logs = cursor.fetchall()
            
            # 4. Generate the Markdown Report
            with open(export_path, 'w', encoding='utf-8') as f:
                f.write(f"# 🛡️ LLM Usage Log\n")
                f.write(f"**Project:** {self.project_name}\n\n")
                
                f.write(f"## 📊 Cognitive Agency Dashboard\n")
                f.write(f"* **Independent Human Notes ($N_h$):** {n_h}\n")
                f.write(f"* **Human-Verified AI Notes ($N_v$):** {n_v}\n")
                f.write(f"* **Unverified AI Notes ($N_{{ai\\_unverified}}$):** {n_ai_unverified}\n")
                f.write(f"---\n")
                f.write(f"### **Human-to-AI Ratio:** `{integrity_ratio:.2f}`\n")
                f.write(f"### **Verification Score:** `{verification_score:.1f}%`\n")
                f.write(f"---\n\n")
                
                f.write(f"## 📜 Verified Interaction Log\n")
                f.write(f"*This section contains the immutable, raw prompt-response pairs processed by local hardware.*\n\n")
                
                for idx, (timestamp, prompt, response, model) in enumerate(logs, 1):
                    f.write(f"### Interaction #{idx} ({timestamp})\n")
                    f.write(f"**Model:** `{model}` (Local Inference)\n\n")
                    f.write(f"**Prompt:**\n> {prompt.replace(chr(10), chr(10) + '> ')}\n\n")
                    f.write(f"**Raw AI Response:**\n```text\n{response}\n```\n")
                    f.write(f"---\n\n")
                    
            return True
        except Exception as e:
            print(f"Error generating llm log: {e}")
            return False
    def log_ai_interaction_threadsafe(self, prompt, response, model):
        """Opens a short-lived connection to safely log AI interactions from background QThreads."""
        if not self.project_filepath: return
        try:
            # Localized connection specifically for this thread
            conn = sqlite3.connect(self.project_filepath, timeout=10.0)
            conn.execute(
                "INSERT INTO ai_audit_log (prompt, response, model_used) VALUES (?, ?, ?)",
                (prompt, response, model)
            )
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            print(f"Failed to log AI interaction in background: {e}")
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
            llm_manager = self.main_window.shared_llm_manager
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

    def _halt_viewer_if_active(self, path):
        """Safely stops the background render thread if the file is currently open."""
        if hasattr(self, 'main_window') and self.main_window and self.main_window.current_file_path == path:
            viewer = getattr(self.main_window, 'viewer', None)
            if viewer:
                if viewer.worker and viewer.worker.isRunning():
                    viewer.worker.stop()
                    viewer.worker.wait()
                return viewer  # <-- CRITICAL FIX: Always return the viewer!
        return None

    def _save_single_doc(self, doc, path):
        if not doc or doc.is_closed:
            return
            
        # 1. Safely halt the background thread
        viewer = self._halt_viewer_if_active(path)

        temp_path = None
        try:
            # 2. Get a safe temporary file path
            temp_path = self._create_closed_temp_path(path, suffix=".tmp_save")
          
            # 3. Save WITHOUT garbage collection. 
            # This bypasses the C-level abort and safely appends your highlights!
            doc.save(temp_path)
            
            # 4. Completely close the PyMuPDF document to drop all OS file locks
            
            if viewer and viewer.doc:
                viewer.doc = None

            # 5. Safely swap the temp file with the real file
            self._safe_swap_file(temp_path, path)
            
            temp_path = None
            
            # 6. Mark as successfully saved
            self.dirty_docs.discard(path)
            doc.close() 
        except Exception as e:
            print(f"Primary save failed for {path}: {e}")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
        finally:
            # 7. Reopen from the fresh file on disk and restart the UI
            if path in self.open_docs or viewer: 
                try:
                    import fitz
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