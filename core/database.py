import sqlite3
import json
import os
import logging

class Database:
    def __init__(self, project_filepath=None):
        self.project_filepath = project_filepath
        self._conn = None
        if project_filepath:
            self._init_db()

    def _init_db(self):
        try:
            if self._conn:
                self._conn.close()

            self._conn = sqlite3.connect(self.project_filepath)
            cursor = self._conn.cursor()

            # [PERF FIX] Enable WAL mode and relaxed synchronous mode for faster writes and reduced UI stutter
            cursor.execute('PRAGMA journal_mode = WAL;')
            cursor.execute('PRAGMA synchronous = NORMAL;')

            cursor.execute('''CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS pdfs (path TEXT PRIMARY KEY)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS document_maps (pdf_path TEXT PRIMARY KEY, json_map TEXT)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY, quote TEXT, note TEXT, color TEXT,
                is_custom INTEGER, pdf_path TEXT, page_num INTEGER,
                manual_font_size INTEGER, x REAL, y REAL, width REAL, height REAL
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT, label TEXT, color TEXT
            )''')
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Database initialization error: {e}")

    def set_project_filepath(self, filepath):
        self.project_filepath = filepath
        self._init_db()

    def create_project(self, filepath, project_name):
        try:
            filepath = filepath.strip()
            if filepath.endswith(".index.json"):
                filepath = filepath.replace(".index.json", "")
            if not filepath.lower().endswith(".pdfproj"):
                filepath += ".pdfproj"

            self.project_filepath = filepath
            self._init_db()
            cursor = self._conn.cursor()
            cursor.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("project_name", project_name))
            self._conn.commit()
            return filepath
        except Exception as e:
            print(f"Error creating project: {e}")
            return None

    def load_project(self, filepath):
        try:
            filepath = filepath.strip()
            if filepath.endswith(".index.json"):
                filepath = filepath.replace(".index.json", "")
                if not os.path.exists(filepath) and os.path.exists(filepath + ".pdfproj"):
                    filepath += ".pdfproj"

            if not os.path.exists(filepath):
                return False, None

            self.project_filepath = filepath
            self._init_db()

            cursor = self._conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = 'project_name'")
            row = cursor.fetchone()
            project_name = row[0] if row else os.path.basename(filepath).replace(".pdfproj", "")

            cursor.execute("SELECT path FROM pdfs")
            pdfs = [row[0] for row in cursor.fetchall()]
            return True, {"project_name": project_name, "pdfs": pdfs}
        except Exception as e:
            print(f"Error loading project: {e}")
            return False, None

    def migrate_from_json(self, filepath, data):
        try:
            if self._conn:
                self._conn.close()
            os.remove(filepath)
            self._init_db()

            pdfs = data.get("pdfs", [])
            cursor = self._conn.cursor()
            for p in pdfs:
                cursor.execute("INSERT OR IGNORE INTO pdfs (path) VALUES (?)", (p,))

            self.save_workspace_data(data.get("workspace_data", {"nodes": {}, "edges": []}))
            print("Migration complete!")
            return True
        except Exception as e:
            print(f"Migration error: {e}")
            return False

    def add_pdf(self, pdf_path):
        if self._conn:
            try:
                self._conn.execute("INSERT OR IGNORE INTO pdfs (path) VALUES (?)", (pdf_path,))
                self._conn.commit()
                return True
            except sqlite3.Error as e:
                print(f"Error adding PDF to DB: {e}")
        return False

    def get_pdfs(self):
        if self._conn:
            try:
                cursor = self._conn.cursor()
                cursor.execute("SELECT path FROM pdfs")
                return [row[0] for row in cursor.fetchall()]
            except sqlite3.Error as e:
                print(f"Error getting PDFs: {e}")
        return []

    def save_document_map(self, pdf_path, json_map_str):
        if not self._conn or not pdf_path:
            return False
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO document_maps (pdf_path, json_map) VALUES (?, ?)",
                (pdf_path, json_map_str)
            )
            self._conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"Error saving document map for {pdf_path}: {e}")
            return False

    def get_document_map(self, pdf_path):
        if not self._conn or not pdf_path:
            return None
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT json_map FROM document_maps WHERE pdf_path = ?", (pdf_path,))
            row = cursor.fetchone()
            return row[0] if row else None
        except sqlite3.Error as e:
            print(f"Error reading document map for {pdf_path}: {e}")
            return None

    def get_unmapped_pdfs(self):
        if not self._conn:
            return []
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT p.path FROM pdfs p LEFT JOIN document_maps m ON p.path = m.pdf_path WHERE m.pdf_path IS NULL"
            )
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"Error querying unmapped PDFs: {e}")
            return []

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
            cursor.execute("SELECT * FROM edges")
            for row in cursor.fetchall():
                edge_id, source_id, target_id, label, color = row
                edges.append({
                    "id": edge_id, "source": source_id, "target": target_id,
                    "label": label, "color": color
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
                (e.get("id"), e.get("source"), e.get("target"), e.get("label"), e.get("color"))
                for e in edges
            ]

            cursor.executemany("""
                INSERT INTO edges (edge_id, source_id, target_id, label, color)
                VALUES (?, ?, ?, ?, ?)
            """, edge_insert_data)

            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            print(f"Error saving workspace data: {e}")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
