from typing import Optional
from services.ocr_service import OCRService


class OCRController:
    def __init__(self, ocr_service: OCRService, main_window=None):
        self.ocr_service = ocr_service
        self.main_window = main_window

    def set_main_window(self, main_window: object) -> None:
        self.main_window = main_window

    def refresh_document_cache(self, file_path: str) -> None:
        """Refresh a document in cache after OCR modification."""
        self.ocr_service.refresh_document_cache(file_path)

    def get_doc(self, file_path: str):
        """Get a document from the project cache."""
        return self.ocr_service.get_doc(file_path)

    def add_pdf_to_project(self, file_path: str) -> bool:
        """Add an OCR'd PDF to the current project."""
        return self.ocr_service.add_pdf(file_path)

    def get_current_file_path(self) -> Optional[str]:
        """Get the currently active file path."""
        if self.main_window and hasattr(self.main_window, 'current_file_path'):
            return self.main_window.current_file_path
        return None

    def switch_to_file(self, file_path: str) -> None:
        """Switch the viewer to a specific PDF file."""
        if self.main_window and hasattr(self.main_window, 'switch_to_pdf'):
            self.main_window.switch_to_pdf(file_path)

    def refresh_pdf_dropdown(self) -> None:
        """Refresh the PDF selector dropdown in the main window."""
        if self.main_window and hasattr(self.main_window, '_refresh_pdf_dropdown'):
            self.main_window._refresh_pdf_dropdown()

    def load_document_in_viewer(self, doc) -> None:
        """Load a document into the PDF viewer."""
        if self.main_window and hasattr(self.main_window, 'viewer') and self.main_window.viewer:
            self.main_window.viewer.load_document(doc)
