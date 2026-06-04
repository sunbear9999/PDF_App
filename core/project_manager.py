# core/project_manager.py
import sqlite3
import json
import os
import shutil
import tempfile
import fitz
import sys
from core.models.workspace_models import NodeModel, EdgeModel, WorkspaceModel

# Import the refactored DB modules
from core.db.schema import DatabaseSchema
from core.db.workspace_db import WorkspaceDB
from core.db.annotation_db import AnnotationDB
from core.db.tag_db import TagDB
from core.db.ai_db import AIDB
from core.db.document_db import DocumentDB

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

        # Initialize the DB subsystems
        self.db_schema = DatabaseSchema(self)
        self.db_workspaces = WorkspaceDB(self)
        self.db_annotations = AnnotationDB(self)
        self.db_tags = TagDB(self)
        self.db_ai = AIDB(self)
        self.db_docs = DocumentDB(self)
        
        self.db_docs.ensure_default_templates()

    # ---------------------------------------------------------
    # Core Project & File System Logic (Maintains its state here)
    # ---------------------------------------------------------
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

    # File and DB synchrony logic maintained in Manager
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
        if old_path not in self.pdfs: 
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
            
        idx = self.pdfs.index(old_path)
        self.pdfs[idx] = new_path
        
        if self._conn:
            try:
                cursor = self._conn.cursor()
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute("UPDATE pdfs SET path = ? WHERE path = ?", (new_path, old_path))
                cursor.execute("UPDATE highlights SET doc_id = ? WHERE doc_id = ?", (new_path, old_path))
                cursor.execute("UPDATE nodes SET pdf_path = ? WHERE pdf_path = ?", (new_path, old_path))
                cursor.execute("UPDATE doc_tags SET doc_id = ? WHERE doc_id = ?", (new_path, old_path))
                self._conn.commit()
            except sqlite3.Error as e:
                self._conn.rollback()
                print(f"Error renaming PDF in DB: {e}")
                return False
                
        if self.active_file == old_path:
            self.active_file = new_path
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
        if hasattr(self, 'main_window') and self.main_window and self.main_window.current_file_path == path:
            viewer = getattr(self.main_window, 'viewer', None)
            if viewer:
                if viewer.worker and viewer.worker.isRunning():
                    viewer.worker.stop()
                    viewer.worker.wait()
                return viewer
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
        try:
            for path, doc in list(self.open_docs.items()):
                if path in self.dirty_docs and path != "workspace":
                    self._save_single_doc(doc, path)
            self.dirty_docs.discard("workspace")
        except Exception as e:
            print(f"Error during save_all_docs: {e}")

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

    # --- Annotations ---
    def upsert_highlight(self, highlight_data): return self.db_annotations.upsert_highlight(highlight_data)
    def get_highlights(self): return self.db_annotations.get_highlights()
    def get_highlight(self, highlight_id): return self.db_annotations.get_highlight(highlight_id)
    def get_unused_highlights(self, workspace_id): return self.db_annotations.get_unused_highlights(workspace_id)
    def delete_highlight_record(self, highlight_id): return self.db_annotations.delete_highlight_record(highlight_id)

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
    def save_chat_message(self, tab_name, role, content, ui_format="live_stream"): return self.db_ai.save_chat_message(tab_name, role, content, ui_format)
    def get_chat_history(self, tab_name): return self.db_ai.get_chat_history(tab_name)
    def clear_chat_history(self, tab_name): return self.db_ai.clear_chat_history(tab_name)
    def log_ai_interaction(self, prompt, response, model): return self.db_ai.log_ai_interaction(prompt, response, model)
    def log_ai_interaction_threadsafe(self, prompt, response, model): return self.db_ai.log_ai_interaction_threadsafe(prompt, response, model)
    def generate_llm_log(self, export_path): return self.db_ai.generate_llm_log(export_path)

    # --- Documents, Essays, Templates & Meta ---
    def get_metadata(self, key, default=None): return self.db_docs.get_metadata(key, default)
    def set_metadata(self, key, value): return self.db_docs.set_metadata(key, value)
    def upsert_essay(self, essay_id, title, content): return self.db_docs.upsert_essay(essay_id, title, content)
    def get_all_essays(self): return self.db_docs.get_all_essays()
    def get_essay(self, essay_id): return self.db_docs.get_essay(essay_id)
    def upsert_citation(self, citation_data): return self.db_docs.upsert_citation(citation_data)
    def get_citation(self, doc_id): return self.db_docs.get_citation(doc_id)
    def get_analysis_templates(self): return self.db_docs.get_analysis_templates()
    def save_analysis_templates(self, templates_list): return self.db_docs.save_analysis_templates(templates_list)
    def save_document_analysis(self, doc_path, template_id, chunk_index, json_data): return self.db_docs.save_document_analysis(doc_path, template_id, chunk_index, json_data)
    def get_document_analyses(self, doc_path, template_id): return self.db_docs.get_document_analyses(doc_path, template_id)
    def clear_document_analyses(self, doc_path, template_id): return self.db_docs.clear_document_analyses(doc_path, template_id)