# core/project_manager.py
import sqlite3
import json
import os
import shutil
import tempfile
import fitz
import sys
import mimetypes
import uuid
from core.models.workspace_models import NodeModel, EdgeModel, WorkspaceModel

# Import the refactored DB modules
from core.db.schema import DatabaseSchema
from core.db.workspace_db import WorkspaceDB
from core.db.annotation_db import AnnotationDB
from core.db.tag_db import TagDB
from core.db.ai_db import AIDB
from core.db.document_db import DocumentDB
from core.db.graph_db import GraphDB
from core.db.data_dock_db import DataDockDB
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload

class ProjectManager:
    def __init__(self, max_cache_size=5):
        self.project_filepath = None
        self.project_name = "Untitled Project"
        self.pdfs = []
        self.sources = []

        self.open_docs = {}
        self.dirty_docs = set()
        self.max_cache_size = max_cache_size
        self.active_file = None
        self._conn = None
        # Set by the GUI layer to stop the viewer before saving the active PDF.
        self._halt_viewer_callback = None

        if getattr(sys, 'frozen', False):
            root_dir = sys._MEIPASS
        else:
            root_dir = os.path.abspath(os.path.dirname(__file__))

        self.templates_path = os.path.join(root_dir, "analysis_templates.json")

        # Initialize the DB subsystems
        self.db_schema = DatabaseSchema(self)
        self.db_workspaces = WorkspaceDB(self)
        self.db_annotations = AnnotationDB(self)
        self.db_tags = TagDB(self)
        self.db_ai = AIDB(self)
        self.db_docs = DocumentDB(self)
        self.db_graph = GraphDB(self)
        self.db_data_dock = DataDockDB(self)

        self.db_docs.ensure_default_templates()

        # --- NEW: Subscribe to Global Events ---
        self.bus = EventBus.get_instance()
        self.bus.highlight_created.connect(self._on_highlight_created)
        self.bus.highlight_updated.connect(self._on_highlight_updated)
        self.bus.highlight_deleted.connect(self._on_highlight_deleted)

    # ---------------------------------------------------------
    # Core Project & File System Logic (Maintains its state here)
    # ---------------------------------------------------------
    def _on_highlight_created(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event != DocumentEvent.HIGHLIGHT_CREATED:
            return
        highlight_data = payload.highlight_data
        """Automatically saves new highlights to the database when announced on the bus."""
        pdf_path = highlight_data.get("pdf_path") or highlight_data.get("doc_id") or self.active_file
        self.upsert_highlight({
            "id": highlight_data.get("id"),
            "doc_id": pdf_path,
            "page_num": highlight_data.get("page_num"),
            "rect_coords": highlight_data.get("rect_coords"),
            "text_content": highlight_data.get("subject") or highlight_data.get("text_content", ""),
            "note_content": highlight_data.get("content") or highlight_data.get("note_content", ""),
            "color": highlight_data.get("color"),
        })
        self.mark_dirty(pdf_path)

    def _on_highlight_updated(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event != DocumentEvent.HIGHLIGHT_UPDATED:
            return
        highlight_id, changes = payload.annot_id, payload.changes
        """Persist highlight metadata updates announced by UI or services."""
        if not highlight_id:
            return
        if "note" in changes or "content" in changes:
            self.update_highlight_note(highlight_id, changes.get("note", changes.get("content", "")))
        if changes.get("color"):
            self.update_highlight_color(highlight_id, changes["color"])
        pdf_path = changes.get("pdf_path")
        if pdf_path:
            self.mark_dirty(pdf_path)

    def _on_highlight_deleted(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event != DocumentEvent.HIGHLIGHT_DELETED:
            return
        highlight_id = payload.annot_id
        if highlight_id:
            self.delete_highlight_record(highlight_id)
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

            self.db_schema.init_database()
            self.set_metadata("project_name", self.project_name)
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
                self.db_schema.init_database()
                cursor = self._conn.cursor()
                cursor.execute("SELECT path FROM pdfs")
                self.pdfs = [row[0] for row in cursor.fetchall()]
                self._migrate_pdfs_to_sources()
                self.sources = self.list_sources()
            except sqlite3.DatabaseError:
                print("Legacy JSON project detected. Migrating to SQLite...")
                with open(filepath, 'r') as f:
                    data = json.load(f)

                if self._conn:
                    self._conn.close()
                os.remove(filepath)

                self.db_schema.init_database()
                self.pdfs = data.get("pdfs", [])

                cursor = self._conn.cursor()
                for p in self.pdfs:
                    cursor.execute("INSERT OR IGNORE INTO pdfs (path) VALUES (?)", (p,))
                    self._upsert_source_row(p, "pdf", commit=False)

                # For migration bridging
                dummy_ws = WorkspaceModel(workspace_id=1)
                for node_data in data.get("workspace_data", {}).get("nodes", {}).values():
                    dummy_ws.nodes.append(NodeModel(**node_data))
                for edge_data in data.get("workspace_data", {}).get("edges", []):
                    dummy_ws.edges.append(EdgeModel(**edge_data))
                self.sync_workspace(dummy_ws)
                print("Migration complete!")

            return True
        except Exception as e:
            print(f"Error loading project: {e}")
            return False

    def save_project(self):
        if not self._conn or not self.project_filepath: return
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

            self._conn.close()
            self._conn = None
            self._safe_swap_file(tmp_db, self.project_filepath)
            tmp_db = None

            self.db_schema.init_database()
        except Exception as e:
            print(f"Error saving project safely: {e}")
            if tmp_db and os.path.exists(tmp_db):
                os.remove(tmp_db)

    def _safe_swap_file(self, tmp_file, original_file):
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
        tf.close()
        return temp_path

    def detect_source_type(self, path):
        ext = os.path.splitext(path or "")[1].lower()
        if ext == ".pdf":
            return "pdf"
        if ext in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
            return "video"
        mime = self._guess_mime_type(path)
        if mime.startswith("video/"):
            return "video"
        return "pdf"

    def _guess_mime_type(self, path):
        return mimetypes.guess_type(path or "")[0] or ""

    def _source_row_id(self, path):
        return f"source:{uuid.uuid5(uuid.NAMESPACE_URL, path or '')}"

    def _upsert_source_row(self, path, source_type, metadata=None, state=None, commit=True):
        if not self._conn or not path:
            return None
        source_id = self._source_row_id(path)
        self._conn.execute(
            """
            INSERT INTO sources (id, path, source_type, mime_type, title, metadata_json, state_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(path) DO UPDATE SET
                source_type=excluded.source_type,
                mime_type=excluded.mime_type,
                title=excluded.title,
                metadata_json=excluded.metadata_json,
                state_json=excluded.state_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                source_id,
                path,
                source_type,
                self._guess_mime_type(path),
                os.path.basename(path),
                json.dumps(metadata or {}, ensure_ascii=False),
                json.dumps(state or {"is_removed": False}, ensure_ascii=False),
            ),
        )
        if commit:
            self._conn.commit()
        return source_id

    def _migrate_pdfs_to_sources(self):
        if not self._conn:
            return
        for path in list(self.pdfs or []):
            self._upsert_source_row(path, "pdf", commit=False)
            self.db_graph.ensure_source_entity(path, properties={"source_type": "pdf"}, commit=False)
        self._conn.commit()

    def list_sources(self, source_type=None, include_removed=False):
        if not self._conn:
            return []
        try:
            cursor = self._conn.cursor()
            if source_type:
                cursor.execute(
                    "SELECT id, path, source_type, mime_type, title, metadata_json, state_json FROM sources WHERE source_type = ? ORDER BY title",
                    (source_type,),
                )
            else:
                cursor.execute("SELECT id, path, source_type, mime_type, title, metadata_json, state_json FROM sources ORDER BY title")
            rows = []
            for row in cursor.fetchall():
                state = self._json_obj(row[6])
                if state.get("is_removed") and not include_removed:
                    continue
                rows.append({
                    "id": row[0],
                    "path": row[1],
                    "source_type": row[2] or "pdf",
                    "mime_type": row[3] or "",
                    "title": row[4] or os.path.basename(row[1] or ""),
                    "metadata": self._json_obj(row[5]),
                    "state": state,
                })
            return rows
        except sqlite3.Error as e:
            print(f"Error listing sources: {e}")
            return []

    def get_source_record_by_path(self, path):
        return next((s for s in self.list_sources(include_removed=True) if s.get("path") == path), None)

    def get_source_type(self, path):
        rec = self.get_source_record_by_path(path)
        return rec.get("source_type") if rec else self.detect_source_type(path)

    def get_source_path(self, source_id):
        path = self.db_graph.get_source_path(source_id)
        if path:
            return path
        if not self._conn or not source_id:
            return None
        cursor = self._conn.cursor()
        cursor.execute("SELECT path FROM sources WHERE id = ?", (source_id,))
        row = cursor.fetchone()
        return row[0] if row else None

    def save_video_transcript_status(self, source_id, path, status, model="", language="", error_text=""):
        if not self._conn or not source_id:
            return
        self._conn.execute(
            """
            INSERT INTO video_transcripts (source_id, path, status, model, language, error_text, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(source_id) DO UPDATE SET
                path=excluded.path,
                status=excluded.status,
                model=COALESCE(NULLIF(excluded.model, ''), video_transcripts.model),
                language=COALESCE(NULLIF(excluded.language, ''), video_transcripts.language),
                error_text=excluded.error_text,
                updated_at=CURRENT_TIMESTAMP
            """,
            (source_id, path, status, model or "", language or "", error_text or ""),
        )
        self._conn.commit()

    def save_video_transcript(self, source_id, path, segments, full_text, model="", language=""):
        if not self._conn or not source_id:
            return
        self._conn.execute(
            """
            INSERT INTO video_transcripts (
                source_id, path, status, language, model, full_text, segments_json, error_text, updated_at
            ) VALUES (?, ?, 'completed', ?, ?, ?, ?, '', CURRENT_TIMESTAMP)
            ON CONFLICT(source_id) DO UPDATE SET
                path=excluded.path,
                status='completed',
                language=excluded.language,
                model=excluded.model,
                full_text=excluded.full_text,
                segments_json=excluded.segments_json,
                error_text='',
                updated_at=CURRENT_TIMESTAMP
            """,
            (source_id, path, language or "", model or "", full_text or "", json.dumps(segments or [], ensure_ascii=False)),
        )
        self._conn.commit()

    def get_video_transcript(self, source_id):
        if not self._conn or not source_id:
            return {}
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT source_id, path, status, language, model, full_text, segments_json, error_text FROM video_transcripts WHERE source_id = ?",
            (source_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {}
        return {
            "source_id": row[0],
            "path": row[1],
            "status": row[2],
            "language": row[3] or "",
            "model": row[4] or "",
            "full_text": row[5] or "",
            "segments": self._json_list(row[6]),
            "error": row[7] or "",
        }

    def _json_obj(self, raw):
        try:
            data = json.loads(raw or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _json_list(self, raw):
        try:
            data = json.loads(raw or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    # File and DB synchrony logic maintained in Manager
    def add_pdf(self, pdf_path):
        return self.add_source(pdf_path, "pdf")

    def add_source(self, path, source_type=None, metadata=None):
        if not path:
            return False
        source_type = source_type or self.detect_source_type(path)
        if source_type == "pdf" and path not in self.pdfs:
            self.pdfs.append(path)
        elif source_type == "video" and path in {s.get("path") for s in self.list_sources()}:
            return False
        elif source_type == "pdf" and path in {s.get("path") for s in self.list_sources()}:
            return False

        if self._conn:
            try:
                if source_type == "pdf":
                    self._conn.execute("INSERT OR IGNORE INTO pdfs (path) VALUES (?)", (path,))
                self._upsert_source_row(path, source_type, metadata=metadata, commit=False)
                self.db_graph.ensure_source_entity(path, properties={
                    "source_type": source_type,
                    "mime_type": self._guess_mime_type(path),
                }, commit=False)
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                print(f"Error adding source to DB: {e}")
                return False
        self.sources = self.list_sources()
        return True

    def remove_pdf(self, pdf_path):
        return self.remove_source(pdf_path)

    def remove_source(self, pdf_path):
        source = self.get_source_entity_by_path(pdf_path) if self._conn else None
        source_type = self.get_source_type(pdf_path)
        self._halt_viewer_if_active(pdf_path)
        if pdf_path in self.open_docs:
            doc = self.open_docs.pop(pdf_path)
            if not doc.is_closed:
                doc.close()
        self.dirty_docs.discard(pdf_path)

        if pdf_path in self.pdfs:
            self.pdfs.remove(pdf_path)

        if self._conn:
            try:
                cursor = self._conn.cursor()
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute("DELETE FROM pdfs WHERE path = ?", (pdf_path,))
                cursor.execute("DELETE FROM sources WHERE path = ?", (pdf_path,))
                if source:
                    cursor.execute("DELETE FROM video_transcripts WHERE source_id = ?", (source.id,))
                cursor.execute("DELETE FROM highlights WHERE doc_id = ?", (pdf_path,))
                cursor.execute("DELETE FROM nodes WHERE pdf_path = ?", (pdf_path,))
                cursor.execute("DELETE FROM citations WHERE doc_id = ?", (pdf_path,))
                self.db_graph.remove_source_entity(pdf_path, purge=False, commit=False)
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                print(f"Error removing PDF from DB: {e}")

        if self.active_file == pdf_path:
            self.active_file = None
        self.sources = self.list_sources()
        return True

    def rename_pdf(self, old_path, new_path):
        return self.rename_source(old_path, new_path)

    def rename_source(self, old_path, new_path):
        source_type = self.get_source_type(old_path)
        if source_type == "pdf" and old_path not in self.pdfs:
            return False

        if old_path in self.open_docs:
            doc = self.open_docs.pop(old_path)
            if old_path in self.dirty_docs:
                self._save_single_doc(doc, old_path)
            if not doc.is_closed:
                doc.close()
        self.dirty_docs.discard(old_path)

        try:
            os.rename(old_path, new_path)
        except Exception as e:
            print(f"OS Rename failed: {e}")
            return False

        if old_path in self.pdfs:
            idx = self.pdfs.index(old_path)
            self.pdfs[idx] = new_path

        if self._conn:
            try:
                cursor = self._conn.cursor()
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute("UPDATE pdfs SET path = ? WHERE path = ?", (new_path, old_path))
                cursor.execute(
                    "UPDATE sources SET path = ?, title = ?, updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                    (new_path, os.path.basename(new_path), old_path),
                )
                cursor.execute("UPDATE highlights SET doc_id = ? WHERE doc_id = ?", (new_path, old_path))
                cursor.execute("UPDATE nodes SET pdf_path = ? WHERE pdf_path = ?", (new_path, old_path))
                cursor.execute("UPDATE doc_tags SET doc_id = ? WHERE doc_id = ?", (new_path, old_path))
                source = self.db_graph.get_source_entity_by_path(old_path)
                if source:
                    cursor.execute("UPDATE video_transcripts SET path = ? WHERE source_id = ?", (new_path, source.id))
                self.db_graph.rename_source_entity(old_path, new_path, commit=False)
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                print(f"Error renaming PDF in DB: {e}")
                return False

        if self.active_file == old_path:
            self.active_file = new_path
        self.sources = self.list_sources()
        return True

    # ---------------------------------------------------------
    # PDF Cache Management
    # ---------------------------------------------------------
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
        if self._halt_viewer_callback:
            return self._halt_viewer_callback(path)
        return None

    def _save_single_doc(self, doc, path):
        if not doc or doc.is_closed:
            return
        viewer = self._halt_viewer_if_active(path)
        temp_path = None
        try:
            temp_path = self._create_closed_temp_path(path, suffix=".tmp_save")
            doc.save(temp_path)

            if viewer and viewer.doc:
                viewer.doc = None

            self._safe_swap_file(temp_path, path)
            temp_path = None
            self.dirty_docs.discard(path)
            doc.close()
        except Exception as e:
            print(f"Primary save failed for {path}: {e}")
            if temp_path and os.path.exists(temp_path):
                try: os.remove(temp_path)
                except Exception: pass
        finally:
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
        """Flushes SQLite DB and writes physical PDF annotations to disk."""
        if self._conn:
            self._conn.commit()

        if hasattr(self, 'open_docs'):
            for file_path, doc in list(self.open_docs.items()):
                if not doc.is_closed and file_path in self.dirty_docs:
                    self._save_single_doc(doc, file_path)

        open_paths = set(getattr(self, "open_docs", {}).keys())
        for dirty_key in list(self.dirty_docs):
            if dirty_key not in open_paths:
                self.dirty_docs.discard(dirty_key)

    # ---------------------------------------------------------
    # Delegated Subsystem Methods (Keeps all references identical)
    # ---------------------------------------------------------

    # --- Workspaces ---
    def get_workspaces(self): return self.db_workspaces.get_workspaces()
    def create_workspace(self, name): return self.db_workspaces.create_workspace(name)
    def rename_workspace(self, workspace_id, name): return self.db_workspaces.rename_workspace(workspace_id, name)
    def delete_workspace(self, workspace_id): return self.db_workspaces.delete_workspace(workspace_id)
    def get_workspace_data(self, workspace_id=1): return self.db_workspaces.get_workspace_data(workspace_id)
    def sync_workspace(self, workspace): return self.db_workspaces.sync_workspace(workspace)
    def sync_workspace_delta(self, delta): return self.db_workspaces.sync_workspace_delta(delta)
    def set_node_verification(self, node_id, is_verified): return self.db_workspaces.set_node_verification(node_id, is_verified)
    def save_node_embedding_threadsafe(self, node_id, vector): return self.db_workspaces.save_node_embedding_threadsafe(node_id, vector)
    def save_node_embedding(self, node_id, vector): return self.db_workspaces.save_node_embedding(node_id, vector)
    def get_node_embeddings_batch(self, node_ids): return self.db_workspaces.get_node_embeddings_batch(node_ids)

    # --- Global Graph ---
    def upsert_entity(self, entity): return self.db_graph.upsert_entity(entity)
    def get_entity(self, entity_id): return self.db_graph.get_entity(entity_id)
    def get_entities_by_type(self, entity_type): return self.db_graph.get_entities_by_type(entity_type)
    def get_unverified_entities(self, limit=None, entity_type=None, offset=0): return self.db_graph.get_unverified_entities(limit=limit, entity_type=entity_type, offset=offset)
    def delete_entity(self, entity_id): return self.db_graph.delete_entity(entity_id)
    def upsert_relation(self, relation): return self.db_graph.upsert_relation(relation)
    def get_relation(self, relation_id): return self.db_graph.get_relation(relation_id)
    def get_relations_for_entity(self, entity_id, as_source=True, as_target=True): return self.db_graph.get_relations_for_entity(entity_id, as_source, as_target)
    def get_relations_by_trait(self, trait, registry=None): return self.db_graph.get_relations_by_trait(trait, registry)
    def delete_relation(self, relation_id): return self.db_graph.delete_relation(relation_id)
    def upsert_view(self, view): return self.db_graph.upsert_view(view)
    def get_view(self, view_id): return self.db_graph.get_view(view_id)
    def get_views(self): return self.db_graph.get_views()
    def upsert_view_entity_meta(self, meta): return self.db_graph.upsert_view_entity_meta(meta)
    def get_entities_for_view(self, view_id): return self.db_graph.get_entities_for_view(view_id)
    def get_relations_for_view(self, view_id): return self.db_graph.get_relations_for_view(view_id)
    def ensure_source_entity(self, pdf_path, properties=None): return self.db_graph.ensure_source_entity(pdf_path, properties)
    def get_source_entity(self, source_id): return self.db_graph.get_source_entity(source_id)
    def get_source_entity_by_path(self, pdf_path): return self.db_graph.get_source_entity_by_path(pdf_path)
    def list_source_entities(self):
        paths = [s.get("path") for s in self.list_sources() if s.get("path")]
        if not paths:
            paths = self.pdfs
        return self.db_graph.list_source_entities(paths)

    # --- Data Dock ---
    def list_data_dock_datasets(self): return self.db_data_dock.list_datasets()
    def get_data_dock_dataset(self, dataset_id): return self.db_data_dock.get_dataset(dataset_id)
    def save_data_dock_dataset(self, state): return self.db_data_dock.upsert_dataset(state)
    def delete_data_dock_dataset(self, dataset_id): return self.db_data_dock.delete_dataset(dataset_id)
    def list_data_dock_charts(self): return self.db_data_dock.list_charts()
    def get_data_dock_chart(self, chart_id): return self.db_data_dock.get_chart(chart_id)
    def save_data_dock_chart(self, config): return self.db_data_dock.upsert_chart(config)

    # --- Annotations ---
    def upsert_highlight(self, highlight_data): return self.db_annotations.upsert_highlight(highlight_data)
    def get_highlights(self): return self.db_annotations.get_highlights()
    def get_highlight(self, highlight_id): return self.db_annotations.get_highlight(highlight_id)
    def get_unused_highlights(self, workspace_id): return self.db_annotations.get_unused_highlights(workspace_id)
    def delete_highlight_record(self, highlight_id): return self.db_annotations.delete_highlight_record(highlight_id)
    def update_highlight_text(self, highlight_id, new_text): return self.db_annotations.update_highlight_text(highlight_id, new_text)
    def update_highlight_note(self, highlight_id, new_text): return self.db_annotations.update_highlight_note(highlight_id, new_text)
    def update_highlight_color(self, highlight_id, hex_color): return self.db_annotations.update_highlight_color(highlight_id, hex_color)

    # --- Tags ---
    def create_tag(self, name, color): return self.db_tags.create_tag(name, color)
    def delete_tag(self, tag_id): return self.db_tags.delete_tag(tag_id)
    def get_all_tags(self): return self.db_tags.get_all_tags()
    def assign_tag_to_doc(self, doc_id, tag_id): return self.db_tags.assign_tag_to_doc(doc_id, tag_id)
    def remove_tag_from_doc(self, doc_id, tag_id): return self.db_tags.remove_tag_from_doc(doc_id, tag_id)
    def assign_tag_to_node(self, node_id, tag_id): return self.db_tags.assign_tag_to_node(node_id, tag_id)
    def remove_tag_from_node(self, node_id, tag_id): return self.db_tags.remove_tag_from_node(node_id, tag_id)
    def get_tags_for_doc(self, doc_id): return self.db_tags.get_tags_for_doc(doc_id)
    def get_tags_for_node(self, node_id): return self.db_tags.get_tags_for_node(node_id)
    def get_docs_for_tag(self, tag_id): return self.db_tags.get_docs_for_tag(tag_id)

    # --- AI & Logging ---
    def save_chat_message(self, tab_name, role, content, ui_format="live_stream", trace_id=None, ui_payload=None): return self.db_ai.save_chat_message(tab_name, role, content, ui_format, trace_id, ui_payload)
    def get_chat_history(self, tab_name): return self.db_ai.get_chat_history(tab_name)
    def clear_chat_history(self, tab_name): return self.db_ai.clear_chat_history(tab_name)
    def log_ai_interaction(self, prompt, response, model): return self.db_ai.log_ai_interaction(prompt, response, model)
    def log_ai_interaction_threadsafe(self, prompt, response, model): return self.db_ai.log_ai_interaction_threadsafe(prompt, response, model)
    def save_prompt_trace(self, trace_record): return self.db_ai.save_prompt_trace(trace_record)
    def get_prompt_trace(self, trace_id): return self.db_ai.get_prompt_trace(trace_id)
    def get_prompt_trace_for_job(self, job_id): return self.db_ai.get_prompt_trace_for_job(job_id)
    def generate_llm_log(self, export_path): return self.db_ai.generate_llm_log(export_path)

    # --- Documents, Essays, Templates & Meta ---
    def get_metadata(self, key, default=None): return self.db_docs.get_metadata(key, default)
    def set_metadata(self, key, value): return self.db_docs.set_metadata(key, value)
    def upsert_essay(self, essay_id, title, content): return self.db_docs.upsert_essay(essay_id, title, content)
    def get_all_essays(self): return self.db_docs.get_all_essays()
    def get_essay(self, essay_id): return self.db_docs.get_essay(essay_id)
    def upsert_citation(self, citation_data): return self.db_docs.upsert_citation(citation_data)
    def get_citation(self, doc_id): return self.db_docs.get_citation(doc_id)
    def delete_citation(self, doc_id): return self.db_docs.delete_citation(doc_id)
    def get_analysis_templates(self): return self.db_docs.get_analysis_templates()
    def save_analysis_templates(self, templates_list): return self.db_docs.save_analysis_templates(templates_list)
    def save_document_analysis(self, doc_path, template_id, chunk_index, json_data): return self.db_docs.save_document_analysis(doc_path, template_id, chunk_index, json_data)
    def get_document_analyses(self, doc_path, template_id): return self.db_docs.get_document_analyses(doc_path, template_id)
    def clear_document_analyses(self, doc_path, template_id): return self.db_docs.clear_document_analyses(doc_path, template_id)
