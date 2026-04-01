import json
import os
import fitz
import gc

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

    def load_project(self, filepath):
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
            with open(filepath, 'r') as f:
                data = json.load(f)
                self.pdfs = data.get("pdfs", [])
                self.workspace_data = data.get("workspace_data", {"nodes": {}, "edges": []})
            return True
        except:
            return False

    def _clear_cache(self):
        for doc in self.open_docs.values():
            if not doc.is_closed:
                doc.close()
        self.open_docs = {}
        self.dirty_docs = set()
        self.active_file = None

    def save_project(self):
        if self.project_filepath:
            with open(self.project_filepath, 'w') as f:
                json.dump({
                    "name": self.project_name, 
                    "pdfs": self.pdfs,
                    "workspace_data": self.workspace_data
                }, f, indent=4)

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
        # LRU eviction pattern
        if filepath in self.open_docs:
            doc = self.open_docs.pop(filepath)
            self.open_docs[filepath] = doc
            return doc
        
        self._evict_old_docs()
        self.open_docs[filepath] = fitz.open(filepath)
        return self.open_docs[filepath]

    def _evict_old_docs(self):
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

    def _save_single_doc(self, doc, path):
        if not doc.is_closed:
            try: 
                # Attempt standard PyMuPDF incremental save
                doc.save(doc.name, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
                self.dirty_docs.discard(path)
            except Exception as e: 
                print(f"Incremental save failed for {path}: {e}")
                
                # Robust OS-Level File Lock Fallback
                try:
                    pdf_bytes = doc.write()
                    doc.close()
                    with open(path, 'wb') as f:
                        f.write(pdf_bytes)
                    
                    self.open_docs[path] = fitz.open(path)
                    self.dirty_docs.discard(path)
                except Exception as fallback_e:
                    print(f"Full save fallback failed for {path}: {fallback_e}")

    def save_all_docs(self):
        # Gracefully scrape the active workspace UI memory into the JSON dict before writing to disk
        if "workspace" in self.dirty_docs:
            for obj in gc.get_objects():
                # Checking class name dynamically avoids circular imports with main_window.py
                if type(obj).__name__ == "MainWindow" and hasattr(obj, "tabs"):
                    if "Notes" in obj.tabs:
                        obj.tabs["Notes"].save_workspace_state()
                    break
            self.dirty_docs.discard("workspace")

        for path, doc in list(self.open_docs.items()):
            if path in self.dirty_docs:
                self._save_single_doc(doc, path)