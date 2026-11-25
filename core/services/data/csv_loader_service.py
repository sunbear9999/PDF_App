"""
core/services/data/csv_loader_service.py

Loads a standalone CSV/TSV file directly into a DataGridState — the
file-driven counterpart to DefaultTableParserProvider's clipboard-paste
parsing (core/registries/data_provider_registry.py). Registered as a
DataSourceLoaderSpec so it's usable from both the CSV/TSV read-only source
preview (gui/components/csv_preview_viewer.py) and the Data Dock's "open
file" action — both load through this single implementation.
"""
from __future__ import annotations

import csv
import os
import uuid
from typing import Optional

from core.models.data_dock_models import DataGridState, DataProvenance

LOADER_ID = "csv_stdlib"


class CsvLoaderService:
    loader_id = LOADER_ID

    @staticmethod
    def is_available() -> bool:
        return True

    def load(self, path: str) -> Optional[DataGridState]:
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                sample = fh.read(4096)
                fh.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
                except csv.Error:
                    dialect = csv.excel
                try:
                    has_header = csv.Sniffer().has_header(sample)
                except csv.Error:
                    has_header = False
                raw_rows = list(csv.reader(fh, dialect))
        except OSError:
            return None

        if not raw_rows:
            return None
        width = max(len(row) for row in raw_rows)
        raw_rows = [row + [""] * (width - len(row)) for row in raw_rows]

        if has_header:
            headers = [cell or f"Column {i + 1}" for i, cell in enumerate(raw_rows[0])]
            data_rows = raw_rows[1:]
        else:
            headers = [f"Column {i + 1}" for i in range(width)]
            data_rows = raw_rows

        ext = os.path.splitext(path)[1].lower().lstrip(".")
        source_type = ext if ext in ("csv", "tsv") else "csv"

        return DataGridState(
            dataset_id=f"data_{uuid.uuid4()}",
            name=os.path.splitext(os.path.basename(path))[0],
            headers=headers,
            row_headers=[str(i + 1) for i in range(len(data_rows))],
            rows=data_rows,
            provenance=DataProvenance(source_path=path, source_type=source_type, pdf_path=path),
        )
