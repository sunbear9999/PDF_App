# core/db/document_db.py
import sqlite3
import json
import os
from core.db.base_db import BaseDB
from core.models.ontology_model import RelationType
from core.ontology.registry import OntologyRegistry, RelationTrait

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
            self._ensure_citation_columns()
            normalized = self._normalize_citation_data(citation_data or {})
            self._conn.execute("""
                INSERT INTO citations (
                    doc_id, title, authors, year, journal, doi, vol_issue,
                    publisher, url, item_type, fields_json, source_provider,
                    source_item_key, source_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    title = excluded.title, authors = excluded.authors, year = excluded.year,
                    journal = excluded.journal, doi = excluded.doi, vol_issue = excluded.vol_issue,
                    publisher = excluded.publisher, url = excluded.url, item_type = excluded.item_type,
                    fields_json = excluded.fields_json, source_provider = excluded.source_provider,
                    source_item_key = excluded.source_item_key, source_updated_at = excluded.source_updated_at
            """, (
                normalized.get("doc_id"), normalized.get("title", ""),
                normalized.get("authors", ""), normalized.get("year", ""),
                normalized.get("journal", ""), normalized.get("doi", ""),
                normalized.get("vol_issue", ""), normalized.get("publisher", ""),
                normalized.get("url", ""), normalized.get("item_type", ""),
                normalized.get("fields_json", "{}"), normalized.get("source_provider", ""),
                normalized.get("source_item_key", ""), normalized.get("source_updated_at", ""),
            ))
            self._conn.commit()
        except sqlite3.Error as e:
            print(f"Error saving citation for {(citation_data or {}).get('doc_id')}: {e}")

    def delete_citation(self, doc_id):
        if not self._conn or not doc_id:
            return
        try:
            self._conn.execute("DELETE FROM citations WHERE doc_id = ?", (doc_id,))
            self._conn.commit()
        except Exception as e:
            print(f"[DocumentDB] Error deleting citation for {doc_id}: {e}")

    def get_citation(self, doc_id):
        if not self._conn: return {}
        try:
            self._ensure_citation_columns()
            cursor = self._conn.cursor()
            cursor.execute("""
                SELECT title, authors, year, journal, doi, vol_issue, publisher,
                       url, item_type, fields_json, source_provider, source_item_key,
                       source_updated_at
                FROM citations WHERE doc_id = ?
            """, (doc_id,))
            row = cursor.fetchone()
            if row:
                fields = {}
                if row[9]:
                    try:
                        fields = json.loads(row[9]) if isinstance(row[9], str) else {}
                    except json.JSONDecodeError:
                        fields = {}
                data = {
                    "doc_id": doc_id,
                    "title": row[0] or "",
                    "authors": row[1] or "",
                    "year": row[2] or "",
                    "journal": row[3] or "",
                    "doi": row[4] or "",
                    "vol_issue": row[5] or "",
                    "publisher": row[6] or "",
                    "url": row[7] or "",
                    "item_type": row[8] or "",
                    "fields": fields,
                    "source_provider": row[10] or "",
                    "source_item_key": row[11] or "",
                    "source_updated_at": row[12] or "",
                }
                for key, value in fields.items():
                    data.setdefault(key, value)
                return data
            return {}
        except sqlite3.Error as e:
            print(f"Error reading citation for {doc_id}: {e}")
            return {}

    def _ensure_citation_columns(self):
        cursor = self._conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS citations (
            doc_id TEXT PRIMARY KEY, title TEXT, authors TEXT,
            year TEXT, journal TEXT, doi TEXT
        )''')
        cursor.execute("PRAGMA table_info(citations)")
        existing = {col[1] for col in cursor.fetchall()}
        for col_name, col_type in {
            "vol_issue": "TEXT",
            "publisher": "TEXT",
            "url": "TEXT",
            "item_type": "TEXT",
            "fields_json": "TEXT",
            "source_provider": "TEXT",
            "source_item_key": "TEXT",
            "source_updated_at": "TEXT",
        }.items():
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE citations ADD COLUMN {col_name} {col_type}")

    def _normalize_citation_data(self, citation_data):
        canonical = {
            "doc_id", "title", "authors", "year", "journal", "doi", "vol_issue",
            "publisher", "url", "item_type", "fields", "fields_json",
            "source", "source_provider", "source_item_key", "source_updated_at",
        }
        fields = {}
        raw_fields = citation_data.get("fields")
        if isinstance(raw_fields, dict):
            fields.update(raw_fields)
        raw_fields_json = citation_data.get("fields_json")
        if isinstance(raw_fields_json, str) and raw_fields_json.strip():
            try:
                decoded = json.loads(raw_fields_json)
                if isinstance(decoded, dict):
                    fields.update(decoded)
            except json.JSONDecodeError:
                pass
        for key, value in citation_data.items():
            if key not in canonical:
                fields[key] = value
        normalized = dict(citation_data)
        normalized["source_provider"] = (
            citation_data.get("source_provider") or citation_data.get("source") or ""
        )
        normalized["fields_json"] = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        return normalized

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
                    "Build a concise argument map for the document. In each chunk, select short quotes that reveal "
                    "the author's substantive claims, concrete data/evidence, causal support, important limitations, "
                    "or counterpoints. Avoid methodology, framework, literature-review, or section-framing quotes "
                    "unless they directly support the argument. During synthesis, infer one central thesis or "
                    "overarching claim, then only a few broad support reasons. Treat smaller assertions, examples, "
                    "and data points as evidence beneath those supports instead of promoting them into duplicate "
                    "top-level claims. The final graph should be one readable evidence-backed hierarchy."
                ),
                "schema": json.dumps({
                    "graph_artifacts": "claims, reasoning nodes, exact quote evidence, and typed relations",
                    "workspace_goal": "quote -> reasoning -> claim chains that can be sent to a board",
                }, indent=2),
                "node_types": ["entity.claim", "entity.reasoning", "entity.quote"],
                "relation_types": self._default_argument_relation_types(["entity.claim", "entity.reasoning", "entity.quote"]),
                "allow_text_nodes": False,
                "chunk_prompt_key": "Graph Analysis Chunk Observations System",
                "synthesis_prompt_key": "Graph Analysis Synthesis System",
                "master_prompt_key": "Graph Analysis Master System",
                "chunk_query_prompt_key": "Graph Analysis Chunk Observations Query",
                "synthesis_query_prompt_key": "Graph Analysis Synthesis Query",
                "master_query_prompt_key": "Graph Analysis Master Query",
                "analysis_template_version": 12,
                "limits": {
                    "chunk_pages": 4,
                    "max_chunk_chars": 14000,
                    "max_master_chars": 36000,
                    "num_ctx": 24576,
                    "chunk_num_predict": 1400,
                    "synthesis_num_predict": 3500,
                    "master_num_predict": 4000,
                    "max_entities_per_chunk": 12,
                    "max_relations_per_chunk": 16,
                    "max_quotes_per_chunk": 5,
                    "quote_words": 10,
                    "max_quote_words": 18,
                    "explanation_words": 10
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
                    for key in ["instructions", "schema", "node_types", "relation_types", "allow_text_nodes", "chunk_prompt_key", "synthesis_prompt_key", "master_prompt_key", "chunk_query_prompt_key", "synthesis_query_prompt_key", "master_query_prompt_key", "limits", "analysis_template_version"]:
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
        for key in ["node_types", "relation_types", "allow_text_nodes", "chunk_prompt_key", "synthesis_prompt_key", "master_prompt_key", "chunk_query_prompt_key", "synthesis_query_prompt_key", "master_query_prompt_key", "limits"]:
            template[key] = default[key]
        template["analysis_template_version"] = default["analysis_template_version"]
        if "graph_artifacts" not in str(template.get("schema", "")):
            template["schema"] = default["schema"]

    def _default_argument_relation_types(self, node_types):
        registry = OntologyRegistry()
        preferred = [
            RelationType.SUPPORTS.value,
            RelationType.REASONS.value,
            RelationType.REFUTES.value,
            RelationType.CONTRADICTS.value,
        ]
        selected = [
            rel_type for rel_type in preferred
            if registry.get_relation_blueprint(rel_type)
            and any(registry.validate_relation(rel_type, src, tgt) for src in node_types for tgt in node_types)
        ]
        if selected:
            return selected
        selected = []
        argument_traits = {RelationTrait.EVIDENTIARY, RelationTrait.HIERARCHICAL, RelationTrait.SEMANTIC}
        for bp in registry.all_relations():
            traits = set(bp.traits or [])
            if not traits.intersection(argument_traits):
                continue
            if not any(node_type in bp.valid_source_types for node_type in node_types):
                continue
            if not any(node_type in bp.valid_target_types for node_type in node_types):
                continue
            if any(registry.validate_relation(bp.type_key, src, tgt) for src in node_types for tgt in node_types):
                selected.append(bp.type_key)
        return selected

    def save_document_analysis(self, doc_path, template_id, chunk_index, json_data):
        if not self._conn: return
        self._execute_analysis_write(
            "INSERT INTO document_analyses (doc_path, template_id, chunk_index, json_data) VALUES (?, ?, ?, ?)",
            (doc_path, template_id, chunk_index, json_data),
        )

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
        self._execute_analysis_write("DELETE FROM document_analyses WHERE doc_path = ? AND template_id = ?", (doc_path, template_id))

    def _execute_analysis_write(self, sql, params):
        try:
            self._conn.execute(sql, params)
            self._conn.commit()
            return
        except sqlite3.ProgrammingError as exc:
            if "created in a thread" not in str(exc):
                raise

        db_path = getattr(self.manager, "project_filepath", None)
        if not db_path:
            raise
        conn = sqlite3.connect(db_path, timeout=10.0)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()
