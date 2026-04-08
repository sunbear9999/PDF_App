import os
from typing import Any, List, Optional, Sequence
from core.project_manager import ProjectManager


class PDFService:
    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager

    def get_pdf_paths(self) -> List[str]:
        return list(self.project_manager.pdfs)

    def add_pdfs(self, file_paths: Sequence[str]) -> List[str]:
        added = []
        for path in file_paths:
            if self.project_manager.add_pdf(path):
                added.append(path)
        return added

    def get_doc(self, pdf_path: str):
        return self.project_manager.get_doc(pdf_path)

    def set_active_file(self, pdf_path: str) -> None:
        self.project_manager.set_active_file(pdf_path)

    def save_all_docs(self) -> None:
        self.project_manager.save_all_docs()

    def mark_dirty(self, filepath: str) -> None:
        self.project_manager.mark_dirty(filepath)

    def get_document_map(self, pdf_path: str) -> Optional[str]:
        return self.project_manager.get_document_map(pdf_path)

    def save_document_map(self, pdf_path: str, json_map_str: str) -> bool:
        return self.project_manager.save_document_map(pdf_path, json_map_str)

    def get_unmapped_pdfs(self) -> List[str]:
        return self.project_manager.get_unmapped_pdfs()

    @property
    def project_filepath(self) -> Optional[str]:
        return self.project_manager.project_filepath

    @property
    def project_name(self) -> Optional[str]:
        return self.project_manager.project_name
