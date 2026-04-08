import os
import json

from .database import Database
from .pdf_cache import PDFCache

class ProjectManager:
    def __init__(self, max_cache_size=5):
        self.project_filepath = None
        self.project_name = "Untitled Project"
        self.pdfs = []

        self.database = Database()
        self.pdf_cache = PDFCache(max_cache_size)

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
            self.pdf_cache._clear_cache()

            if os.path.exists(filepath):
                os.remove(filepath)

            self.database.create_project(filepath, self.project_name)
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
            self.pdf_cache._clear_cache()

            success, data = self.database.load_project(filepath)
            if success:
                self.project_name = data["project_name"]
                self.pdfs = data["pdfs"]
                return True
            else:
                # Try legacy JSON migration
                print("Legacy JSON project detected. Migrating to SQLite...")
                with open(filepath, 'r') as f:
                    legacy_data = json.load(f)

                if self.database.migrate_from_json(filepath, legacy_data):
                    self.project_name = legacy_data.get("project_name", os.path.basename(filepath).replace(".pdfproj", ""))
                    self.pdfs = legacy_data.get("pdfs", [])
                    return True

            return False
        except Exception as e:
            print(f"Error loading project: {e}")
            return False

    def save_project(self):
        pass

    def add_pdf(self, pdf_path):
        if pdf_path not in self.pdfs:
            self.pdfs.append(pdf_path)
            return self.database.add_pdf(pdf_path)
        return False

    def save_document_map(self, pdf_path, json_map_str):
        return self.database.save_document_map(pdf_path, json_map_str)

    def get_document_map(self, pdf_path):
        return self.database.get_document_map(pdf_path)

    def get_unmapped_pdfs(self):
        return self.database.get_unmapped_pdfs()

    def get_workspace_data(self):
        return self.database.get_workspace_data()

    def save_workspace_data(self, workspace_data):
        self.database.save_workspace_data(workspace_data)

    def set_active_file(self, filepath):
        self.pdf_cache.set_active_file(filepath)

    def mark_dirty(self, filepath):
        self.pdf_cache.mark_dirty(filepath)

    def get_doc(self, filepath):
        return self.pdf_cache.get_doc(filepath)

    def save_all_docs(self):
        self.pdf_cache.save_all_docs()

    def get_pdf_paths(self):
        return self.pdfs.copy()