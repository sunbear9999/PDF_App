# core/project_manager.py
import sqlite3
import json
import os
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
        """Initializes the SQLite Database schema."""
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
        self._conn.commit()

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
                # MIGRATE LEGACY JSON TO SQLITE
                print("Legacy JSON project detected. Migrating to SQLite...")
                with open(filepath, 'r') as f:
                    data = json.load(f)
                
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
        pass # SQLite auto-saves to disk on commit, this is just a pass-through now

    def add_pdf(self, pdf_path):
        if pdf_path not in self.pdfs:
            self.pdfs.append(pdf_path)
            if self._conn:
                self._conn.execute("INSERT OR IGNORE INTO pdfs (path) VALUES (?)", (pdf_path,))
                self._conn.commit()
            return True
        return False

    def get_workspace_data(self):
        if not self._conn: return {"nodes": {}, "edges": []}
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
        cursor.execute("SELECT * FROM edges")
        for row in cursor.fetchall():
            edge_id, source_id, target_id, label, color = row
            edges.append({
                "id": edge_id, "source": source_id, "target": target_id, 
                "label": label, "color": color
            })
            
        return {"nodes": nodes, "edges": edges}

    def save_workspace_data(self, workspace_data):
        if not self._conn: return
        cursor = self._conn.cursor()
        
        cursor.execute("DELETE FROM nodes")
        cursor.execute("DELETE FROM edges")
        
        nodes = workspace_data.get("nodes", {})
        for n_id, d in nodes.items():
            cursor.execute("""
                INSERT INTO nodes (node_id, quote, note, color, is_custom, pdf_path, page_num, manual_font_size, x, y, width, height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (n_id, d.get("quote"), d.get("note"), d.get("color"), int(d.get("is_custom", 0)), 
                  d.get("pdf_path"), d.get("page_num"), d.get("manual_font_size"),
                  d.get("x"), d.get("y"), d.get("width"), d.get("height")))
                  
        edges = workspace_data.get("edges", [])
        for e in edges:
            cursor.execute("""
                INSERT INTO edges (edge_id, source_id, target_id, label, color)
                VALUES (?, ?, ?, ?, ?)
            """, (e.get("id"), e.get("source"), e.get("target"), e.get("label"), e.get("color")))
            
        self._conn.commit()

    def set_active_file(self, filepath):
        self.active_file = filepath

    def mark_dirty(self, filepath):
        if filepath:
            self.dirty_docs.add(filepath)

    def _clear_cache(self):
        try:
            for doc in self.open_docs.values():
                if not doc.is_closed: doc.close()
            self.open_docs = {}
            self.dirty_docs = set()
            self.active_file = None
        except Exception as e: print(f"Error clearing cache: {e}")

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
                evict_candidate = None
                for path in self.open_docs:
                    if path != self.active_file:
                        evict_candidate = path
                        break
                
                if not evict_candidate: break 
                    
                doc = self.open_docs.pop(evict_candidate)
                if evict_candidate in self.dirty_docs:
                    self._save_single_doc(doc, evict_candidate)
                
                if not doc.is_closed: doc.close()
        except Exception as e:
            print(f"Error evicting docs: {e}")

    def _save_single_doc(self, doc, path):
        if not doc or doc.is_closed: return
        try: 
            temp_path = path + ".tmp_save"
            doc.save(temp_path, garbage=3, deflate=True) 
            
            if os.path.exists(temp_path):
                os.replace(temp_path, path)
                
            self.dirty_docs.discard(path)
        except Exception as e: 
            print(f"Primary save failed for {path}: {e}")
            try:
                pdf_bytes = doc.write()
                with open(path, 'wb') as f: f.write(pdf_bytes)
                self.dirty_docs.discard(path)
            except Exception as fallback_e:
                print(f"Full save fallback failed for {path}: {fallback_e}")

    def save_all_docs(self):
        try:
            for path, doc in list(self.open_docs.items()):
                if path in self.dirty_docs and path != "workspace":
                    self._save_single_doc(doc, path)
            self.dirty_docs.discard("workspace")
        except Exception as e:
            print(f"Error during save_all_docs: {e}")