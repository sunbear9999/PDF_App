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
        existing = self.get_analysis_templates()
        self.save_analysis_templates(self._merge_default_analysis_templates(existing))

    def get_analysis_templates(self):
        try:
            with open(self.manager.templates_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                return self._merge_default_analysis_templates(loaded if isinstance(loaded, list) else [])
        except Exception:
            return self._default_analysis_templates()

    def save_analysis_templates(self, templates_list):
        with open(self.manager.templates_path, 'w', encoding='utf-8') as f:
            json.dump(templates_list, f, indent=4)

    def _default_analysis_templates(self):
        return [
            {
                "id": "default_argument_map",
                "title": "Argument Map",
                "instructions": (
                    "Build an argument map for the document. In each chunk, identify explicit claims, "
                    "the reasoning that connects those claims to evidence, and direct quotations that can "
                    "serve as source-backed evidence. Preserve quote text exactly. During the final pass, "
                    "deduplicate repeated claims and link recurring thesis/reasoning chains across chunks."
                ),
                "schema": json.dumps({
                    "graph_artifacts": "claims, reasoning nodes, exact quote evidence, and typed relations",
                    "workspace_goal": "quote -> reasoning -> claim chains that can be sent to a board",
                }, indent=2),
                "node_types": ["entity.claim", "entity.reasoning", "entity.quote"],
                "relation_types": ["relation.supports", "relation.contradicts", "relation.reasons", "relation.derived_from"],
                "allow_text_nodes": False,
                "chunk_prompt_key": "Graph Analysis Chunk System",
                "master_prompt_key": "Graph Analysis Master System",
                "analysis_template_version": 4,
                "limits": {
                    "chunk_pages": 2,
                    "max_chunk_chars": 6000,
                    "max_master_chars": 10000,
                    "num_ctx": 6144,
                    "chunk_num_predict": 1200,
                    "master_num_predict": 1400,
                    "max_entities_per_chunk": 5,
                    "max_relations_per_chunk": 9,
                    "max_quotes_per_chunk": 3
                },
            }
        ]

    def _merge_default_analysis_templates(self, templates):
        templates = [t for t in (templates or []) if isinstance(t, dict)]
        for template in templates:
            self._upgrade_legacy_argument_template(template)
        by_id = {t.get("id"): t for t in templates if t.get("id")}
        changed = False
        for default in self._default_analysis_templates():
            if default["id"] not in by_id:
                templates.append(default)
                changed = True
            else:
                existing = by_id[default["id"]]
                if int(existing.get("analysis_template_version", 0) or 0) < int(default.get("analysis_template_version", 0) or 0):
                    for key in ["instructions", "schema", "node_types", "relation_types", "allow_text_nodes", "chunk_prompt_key", "master_prompt_key", "limits", "analysis_template_version"]:
                        existing[key] = default[key]
                    changed = True
                for key, value in default.items():
                    if key not in existing:
                        existing[key] = value
                        changed = True
        if changed and self.manager.templates_path:
            try:
                self.save_analysis_templates(templates)
            except Exception:
                pass
        return templates

    def _upgrade_legacy_argument_template(self, template):
        template_id = template.get("id")
        title = str(template.get("title", "")).lower()
        if template_id != "default_argument" and "argument structure" not in title:
            return
        default = self._default_analysis_templates()[0]
        template["id"] = template_id or "default_argument"
        template["title"] = template.get("title") or "Argument Map"
        for key in ["node_types", "relation_types", "allow_text_nodes", "chunk_prompt_key", "master_prompt_key", "limits"]:
            template[key] = default[key]
        template["analysis_template_version"] = default["analysis_template_version"]
        if "graph_artifacts" not in str(template.get("schema", "")):
            template["schema"] = default["schema"]

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
