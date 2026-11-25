"""
core/services/data/xlsx_loader_service.py

Loads the active sheet of an .xlsx workbook into a DataGridState using
openpyxl (read-only mode). Multi-sheet support is a documented future
extension, not in this pass — only the active/first sheet is loaded.
"""
from __future__ import annotations

import importlib.util
import os
import uuid
from typing import Optional

from core.models.data_dock_models import DataGridState, DataProvenance

LOADER_ID = "xlsx_openpyxl"


class XlsxLoaderService:
    loader_id = LOADER_ID

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("openpyxl") is not None

    def load(self, path: str) -> Optional[DataGridState]:
        try:
            import openpyxl
        except ImportError:
            return None
        try:
            workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
            sheet = workbook.active
            raw_rows = [
                ["" if cell is None else str(cell) for cell in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            workbook.close()
        except Exception:
            return None

        if not raw_rows:
            return None
        width = max(len(row) for row in raw_rows)
        raw_rows = [row + [""] * (width - len(row)) for row in raw_rows]

        headers = [cell or f"Column {i + 1}" for i, cell in enumerate(raw_rows[0])]
        data_rows = raw_rows[1:]

        return DataGridState(
            dataset_id=f"data_{uuid.uuid4()}",
            name=os.path.splitext(os.path.basename(path))[0],
            headers=headers,
            row_headers=[str(i + 1) for i in range(len(data_rows))],
            rows=data_rows,
            provenance=DataProvenance(source_path=path, source_type="xlsx", pdf_path=path),
        )
