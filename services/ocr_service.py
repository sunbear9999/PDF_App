import os
from typing import Optional
from core.project_manager import ProjectManager


class OCRService:
    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager

    def refresh_document_cache(self, file_path: str):
        """Refresh a document in the project manager cache after OCR modification."""
        if file_path in self.project_manager.open_docs:
            doc = self.project_manager.open_docs[file_path]
            if not doc.is_closed:
                doc.close()
            del self.project_manager.open_docs[file_path]

    def get_doc(self, file_path: str):
        """Retrieve a document from the project manager cache."""
        return self.project_manager.get_doc(file_path)

    def add_pdf(self, file_path: str) -> bool:
        """Add a PDF to the project."""
        return self.project_manager.add_pdf(file_path)

    def current_file_path(self) -> Optional[str]:
        """Get the current active file path from main window context."""
        # This is set by the OCRController from main_window
        return None
