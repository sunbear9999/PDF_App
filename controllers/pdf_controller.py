from typing import Any, Dict, List, Optional, Sequence
from services.pdf_service import PDFService


class PDFController:
    def __init__(self, pdf_service: PDFService, main_window=None, view=None):
        self.pdf_service = pdf_service
        self.main_window = main_window
        self.view = view

    def set_view(self, view: object) -> None:
        self.view = view

    def set_main_window(self, main_window: object) -> None:
        self.main_window = main_window

    def get_pdf_paths(self) -> List[str]:
        return self.pdf_service.get_pdf_paths()

    def add_pdfs(self, file_paths: Sequence[str]) -> List[str]:
        return self.pdf_service.add_pdfs(file_paths)

    def get_doc(self, pdf_path: str):
        return self.pdf_service.get_doc(pdf_path)

    def set_active_file(self, pdf_path: str) -> None:
        self.pdf_service.set_active_file(pdf_path)

    def save_all_docs(self) -> None:
        self.pdf_service.save_all_docs()

    def mark_dirty(self, filepath: str) -> None:
        self.pdf_service.mark_dirty(filepath)

    def get_document_map(self, pdf_path: str) -> Optional[str]:
        return self.pdf_service.get_document_map(pdf_path)

    def save_document_map(self, pdf_path: str, json_map_str: str) -> bool:
        return self.pdf_service.save_document_map(pdf_path, json_map_str)

    def get_unmapped_pdfs(self) -> List[str]:
        return self.pdf_service.get_unmapped_pdfs()

    @property
    def project_filepath(self) -> Optional[str]:
        return self.pdf_service.project_filepath

    @property
    def project_name(self) -> Optional[str]:
        return self.pdf_service.project_name

    def switch_to_pdf(self, pdf_path: str) -> None:
        if self.main_window and hasattr(self.main_window, 'switch_to_pdf'):
            self.main_window.switch_to_pdf(pdf_path)
