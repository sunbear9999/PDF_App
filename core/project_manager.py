import json
import os
import fitz

class ProjectManager:
    def __init__(self):
        self.project_filepath = None
        self.project_name = "Untitled Project"
        self.pdfs = []
        self.open_docs = {} # Memory cache: path -> fitz.Document

    def create_project(self, filepath):
        # Extremely strict extension enforcement to beat OS GUI bugs
        filepath = filepath.strip()
        if filepath.endswith(".index.json"):
            filepath = filepath.replace(".index.json", "")
        if not filepath.lower().endswith(".pdfproj"):
            filepath += ".pdfproj"
            
        self.project_filepath = filepath
        self.project_name = os.path.basename(filepath).replace(".pdfproj", "")
        self.pdfs = []
        self.open_docs = {}
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
        self.open_docs = {}
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                self.pdfs = data.get("pdfs", [])
            return True
        except:
            return False

    def save_project(self):
        if self.project_filepath:
            with open(self.project_filepath, 'w') as f:
                json.dump({"name": self.project_name, "pdfs": self.pdfs}, f, indent=4)

    def add_pdf(self, pdf_path):
        if pdf_path not in self.pdfs:
            self.pdfs.append(pdf_path)
            self.save_project()
            return True
        return False

    def get_doc(self, filepath):
        if filepath not in self.open_docs:
            self.open_docs[filepath] = fitz.open(filepath)
        return self.open_docs[filepath]

    def save_all_docs(self):
        for path, doc in self.open_docs.items():
            if not doc.is_closed:
                # BUG 2 FIX: Enforce robust safe incremental saving 
                try: 
                    doc.save(doc.name, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
                except Exception as e: 
                    print(f"Incremental save failed in project manager for {doc.name}: {e}")
                    try:
                        doc.save(doc.name) # Full save fallback
                    except Exception as fallback_e:
                        print(f"Full save fallback failed for {doc.name}: {fallback_e}")