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
            cursor = self._conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS pdfs (path TEXT PRIMARY KEY)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY, quote TEXT, note TEXT, color TEXT, 
                is_custom INTEGER, pdf_path TEXT, page_num INTEGER, 
                manual_font_size INTEGER, x REAL, y REAL, width REAL, height REAL
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT, label TEXT, color TEXT
            )''')
            # Migration: add weight column if missing
            cursor.execute("PRAGMA table_info(edges)")
            edge_columns = [col[1] for col in cursor.fetchall()]
            if "weight" not in edge_columns:
                try:
                    cursor.execute("ALTER TABLE edges ADD COLUMN weight INTEGER DEFAULT 2")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        print(f"Error adding weight column: {e}")
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Database initialization error: {e}")

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

    def get_workspace_data(self):
        if not self._conn: return {"nodes": {}, "edges": []}
        try:
            cursor = self._conn.cursor()
            
            nodes = {}
            cursor.execute("SELECT * FROM nodes")
            for row in cursor.fetchall():
                node_id, quote, note, color, is_custom, pdf_path, page_num, font_size, x, y, w, h = row
                nodes[node_id] = {
                    "quote": quote, "note": note, "color": color, "is_custom": bool(is_custom),
                    "pdf_path": pdf_path, "page_num": page_num, "manual_font_size": font_size,
                    "x": x, "y": y, "width": w, "height": h
                }
                
            edges = []
            cursor.execute("PRAGMA table_info(edges)")
            edge_columns = [col[1] for col in cursor.fetchall()]
            has_weight = "weight" in edge_columns
            cursor.execute("SELECT * FROM edges")
            for row in cursor.fetchall():
                if has_weight:
                    edge_id, source_id, target_id, label, color, weight = row
                else:
                    edge_id, source_id, target_id, label, color = row
                    weight = 2
                edges.append({
                    "id": edge_id, "source": source_id, "target": target_id, 
                    "label": label, "color": color, "weight": weight
                })
            return {"nodes": nodes, "edges": edges}
        except sqlite3.Error as e:
            print(f"Error reading workspace data: {e}")
            return {"nodes": {}, "edges": []}

    def save_workspace_data(self, workspace_data):
        """OPTIMIZED: Uses bulk transactions for massive speedup when saving large workspaces"""
        if not self._conn: return
        try:
            cursor = self._conn.cursor()
            
            cursor.execute("BEGIN TRANSACTION")
            cursor.execute("DELETE FROM nodes")
            cursor.execute("DELETE FROM edges")
            
            nodes = workspace_data.get("nodes", {})
            node_insert_data = [
                (n_id, d.get("quote"), d.get("note"), d.get("color"), int(d.get("is_custom", 0)), 
                 d.get("pdf_path"), d.get("page_num"), d.get("manual_font_size"),
                 d.get("x"), d.get("y"), d.get("width"), d.get("height"))
                for n_id, d in nodes.items()
            ]
            
            cursor.executemany("""
                INSERT INTO nodes (node_id, quote, note, color, is_custom, pdf_path, page_num, manual_font_size, x, y, width, height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, node_insert_data)
                  
            edges = workspace_data.get("edges", [])
            edge_insert_data = [
                (e.get("id"), e.get("source"), e.get("target"), e.get("label"), e.get("color"), int(e.get("weight", 2)))
                for e in edges
            ]
            cursor.executemany("""
                INSERT INTO edges (edge_id, source_id, target_id, label, color, weight)
                VALUES (?, ?, ?, ?, ?, ?)
            """, edge_insert_data)
            
            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            print(f"Error saving workspace data: {e}")

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
        temp_path = None
        try:
            temp_path = self._create_closed_temp_path(path, suffix=".tmp_save")
            doc.save(temp_path, garbage=3, deflate=True)
            self._safe_swap_file(temp_path, path)
            temp_path = None
            self.dirty_docs.discard(path)
        except Exception as e:
            print(f"Primary save failed for {path}: {e}")
            try:
                pdf_bytes = doc.write()
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
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

    def save_all_docs(self):
        try:
            for path, doc in list(self.open_docs.items()):
                if path in self.dirty_docs and path != "workspace":
                    self._save_single_doc(doc, path)
            self.dirty_docs.discard("workspace")
        except Exception as e:
            print(f"Error during save_all_docs: {e}")