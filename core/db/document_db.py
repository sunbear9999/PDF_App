# core/db/document_db.py
import sqlite3
import json
import os
from core.db.base_db import BaseDB

class DocumentDB(BaseDB):
    def get_metadata(self, key, default=None):
        if not self._conn: return default
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
        except sqlite3.Error as e:
            print(f"Error reading metadata {key}: {e}")
            return default

    def set_metadata(self, key, value):
        if not self._conn: return
        try:
            self._conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, value))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving metadata {key}: {e}")

    def upsert_essay(self, essay_id, title, content):
        if not self._conn: return
        try:
            self._conn.execute('''CREATE TABLE IF NOT EXISTS essays (
                id TEXT PRIMARY KEY, title TEXT, content TEXT, last_edited DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
            self._conn.execute("""
                INSERT INTO essays (id, title, content, last_edited) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET title = excluded.title, content = excluded.content, last_edited = CURRENT_TIMESTAMP
            """, (essay_id, title, content))
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
            return {"id": row[0], "title": row[1], "content": row[2]} if row else None
        except sqlite3.Error as e:
            print(f"Error loading essay {essay_id}: {e}")
            return None

    def upsert_citation(self, citation_data):
        if not self._conn: return
        try:
            self._conn.execute("""
                INSERT INTO citations (doc_id, title, authors, year, journal, doi) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    title = excluded.title, authors = excluded.authors, year = excluded.year,
                    journal = excluded.journal, doi = excluded.doi
            """, (
                citation_data.get("doc_id"), citation_data.get("title", ""),
                citation_data.get("authors", ""), citation_data.get("year", ""),
                citation_data.get("journal", ""), citation_data.get("doi", "")
            ))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving citation for {citation_data.get('doc_id')}: {e}")

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

    def ensure_default_templates(self):
        if not os.path.exists(self.manager.templates_path):
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
            with open(self.manager.templates_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def save_analysis_templates(self, templates_list):
        with open(self.manager.templates_path, 'w', encoding='utf-8') as f:
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