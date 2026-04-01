# core/project_manager.py
import json
import os
import fitz

class ProjectManager:
    def __init__(self, max_cache_size=5):
        self.project_filepath = None
        self.project_name = "Untitled Project"
        self.pdfs = []
        self.workspace_data = {"nodes": {}, "edges": []}
        
        self.open_docs = {} 
        self.dirty_docs = set()
        self.max_cache_size = max_cache_size
        self.active_file = None

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
            self.workspace_data = {"nodes": {}, "edges": []}
            self._clear_cache()
            self.save_project()
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
            
            with open(filepath, 'r') as f:
                data = json.load(f)
                self.pdfs = data.get("pdfs", [])
                self.workspace_data = data.get("workspace_data", {"nodes": {}, "edges": []})
            return True
        except Exception as e:
            print(f"Error loading project: {e}")
            return False

    def _clear_cache(self):
        try:
            for doc in self.open_docs.values():
                if not doc.is_closed:
                    doc.close()
            self.open_docs = {}
            self.dirty_docs = set()
            self.active_file = None
        except Exception as e:
            print(f"Error clearing cache: {e}")

    def save_project(self):
        if self.project_filepath:
            try:
                with open(self.project_filepath, 'w') as f:
                    json.dump({
                        "name": self.project_name, 
                        "pdfs": self.pdfs,
                        "workspace_data": self.workspace_data
                    }, f, indent=4)
            except Exception as e:
                print(f"Error saving project JSON: {e}")

    def add_pdf(self, pdf_path):
        if pdf_path not in self.pdfs:
            self.pdfs.append(pdf_path)
            self.save_project()
            return True
        return False

    def set_active_file(self, filepath):
        self.active_file = filepath

    def mark_dirty(self, filepath):
        if filepath:
            self.dirty_docs.add(filepath)

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
                
                if not evict_candidate:
                    break 
                    
                doc = self.open_docs.pop(evict_candidate)
                if evict_candidate in self.dirty_docs:
                    self._save_single_doc(doc, evict_candidate)
                
                if not doc.is_closed:
                    doc.close()
        except Exception as e:
            print(f"Error evicting docs: {e}")

    def _save_single_doc(self, doc, path):
        if not doc or doc.is_closed: return
        try: 
            # Atomic OS-Level File Replacement for Bulletproof Saving
            temp_path = path + ".tmp_save"
            doc.save(temp_path, garbage=3, deflate=True) # Full clean save to prevent bloat/corruption
            
            if os.path.exists(temp_path):
                os.replace(temp_path, path)
                
            self.dirty_docs.discard(path)
        except Exception as e: 
            print(f"Primary save failed for {path}: {e}")
            # In-memory bytes fallback if temp file creation fails due to permissions
            try:
                pdf_bytes = doc.write()
                with open(path, 'wb') as f:
                    f.write(pdf_bytes)
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