"""
TableExtractionService — canonical shared table store.

Wraps PdfGridExtractionService and converts ExtractedGrid objects to the
shared ExtractedTable model.  Data dock, analysis, and RAG all consume
ExtractedTable so tables are never extracted more than once per document.

The canonical dataset_id on each ExtractedTable links back to the
data_dock_datasets record so the data dock UI shows the same object.
"""
from __future__ import annotations

import re
import uuid
from typing import List, Optional

from core.models.extraction_models import (
    DocumentBlock,
    ExtractedTable,
    ExtractedTableCell,
    ExtractionProvenance,
    SourceLocation,
)

_EXTRACTOR_VERSION = "1.0"

_CAPTION_RE = re.compile(
    r"^(?:table|tbl\.?|figure|fig\.?)\s*\d",
    re.I,
)


def _provenance(strategy: str, confidence: float) -> ExtractionProvenance:
    return ExtractionProvenance(
        extractor_name="table_extraction_service",
        extractor_version=_EXTRACTOR_VERSION,
        parser_provider="pymupdf_layout",
        method="layout",
        confidence=confidence,
    )


class TableExtractionService:
    """
    Canonical table extraction layer.

    Pulls tables from two sources:
      - DocumentBlock objects already classified as 'table' by the layout parser
      - PdfGridExtractionService for fine-grained cell extraction

    Results are cached via ExtractionCache and optionally upserted into the
    data dock (when data_dock_service is provided).
    """

    def __init__(
        self,
        project_manager,
        extraction_cache,
        pdf_grid_extraction_service=None,
        data_dock_service=None,
    ) -> None:
        self._pm = project_manager
        self._cache = extraction_cache
        self._grid_svc = pdf_grid_extraction_service
        self._data_dock = data_dock_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_tables(
        self, doc_id: str, blocks: List[DocumentBlock], pdf_path: Optional[str] = None
    ) -> List[ExtractedTable]:
        """
        Extract tables from a document and return canonical ExtractedTable objects.

        Prefers PyMuPDF find_tables() via PdfGridExtractionService when available;
        falls back to DocumentBlock-level table classification.
        """
        cached = self._cache.get(doc_id, "table_extraction_service", "tables")
        if cached:
            return cached

        tables: List[ExtractedTable] = []

        # Source 1: grid extraction (fine-grained, preferred)
        if self._grid_svc and pdf_path:
            tables.extend(self._extract_via_grid_service(doc_id, pdf_path, blocks))

        # Source 2: table blocks not yet covered by grid extraction
        covered_pages = {t.page_num for t in tables}
        table_blocks = [
            b for b in blocks
            if b.block_type == "table" and b.page_num not in covered_pages
        ]
        for tb in table_blocks:
            tables.append(self._block_to_table(tb, doc_id, blocks))

        # Attach captions
        caption_blocks = [b for b in blocks if b.block_type == "caption"]
        self._attach_captions(tables, caption_blocks, blocks)

        # Upsert to data dock for UI consumption
        if self._data_dock:
            self._upsert_to_data_dock(tables, doc_id, pdf_path)

        self._cache.set(doc_id, "table_extraction_service", "1.0", "tables", tables)
        return tables

    def get_tables(self, doc_id: str) -> List[ExtractedTable]:
        """Return cached tables (empty list if not yet extracted)."""
        result = self._cache.get(doc_id, "table_extraction_service", "tables")
        return result or []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_via_grid_service(
        self,
        doc_id: str,
        pdf_path: str,
        blocks: List[DocumentBlock],
    ) -> List[ExtractedTable]:
        """Convert PdfGridExtractionService grids to ExtractedTable objects."""
        tables: List[ExtractedTable] = []
        try:
            grids = self._grid_svc.extract_all(pdf_path, doc_id)
        except Exception:
            return tables

        for grid in grids:
            page_num = getattr(grid, "page_num", 0) or 0
            table_id = str(uuid.uuid4())

            headers: List[str] = []
            cells: List[List[ExtractedTableCell]] = []

            raw_rows = getattr(grid, "rows", None) or []
            for r_idx, row in enumerate(raw_rows):
                row_cells: List[ExtractedTableCell] = []
                for c_idx, cell in enumerate(row):
                    cell_text = str(cell) if cell is not None else ""
                    is_header = r_idx == 0
                    if is_header:
                        headers.append(cell_text)
                    row_cells.append(ExtractedTableCell(
                        row=r_idx,
                        col=c_idx,
                        text=cell_text,
                        bbox=None,
                        is_header=is_header,
                    ))
                cells.append(row_cells)

            strategy = getattr(grid, "strategy", "grid_service")
            confidence = getattr(grid, "confidence", 0.75)
            dataset_id = getattr(grid, "dataset_id", None)
            bbox = getattr(grid, "bbox", None)

            tables.append(ExtractedTable(
                table_id=table_id,
                doc_id=doc_id,
                page_num=page_num,
                caption=None,
                headers=headers,
                cells=cells,
                bbox=bbox,
                extraction_strategy=strategy,
                confidence=confidence,
                provenance=_provenance(strategy, confidence),
                dataset_id=dataset_id,
            ))

        return tables

    def _block_to_table(
        self,
        block: DocumentBlock,
        doc_id: str,
        all_blocks: List[DocumentBlock],
    ) -> ExtractedTable:
        """Convert a table-typed DocumentBlock into an ExtractedTable skeleton."""
        # Best-effort: split text by lines, then by whitespace → rows
        lines = [ln for ln in block.exact_text.splitlines() if ln.strip()]
        headers: List[str] = []
        cells: List[List[ExtractedTableCell]] = []

        for r_idx, line in enumerate(lines):
            cols = re.split(r"\s{2,}|\t", line.strip())
            if r_idx == 0:
                headers = cols
            row_cells = [
                ExtractedTableCell(
                    row=r_idx,
                    col=c_idx,
                    text=col.strip(),
                    is_header=(r_idx == 0),
                )
                for c_idx, col in enumerate(cols)
            ]
            cells.append(row_cells)

        return ExtractedTable(
            table_id=str(uuid.uuid4()),
            doc_id=doc_id,
            page_num=block.page_num,
            caption=None,
            headers=headers,
            cells=cells,
            bbox=block.bbox,
            extraction_strategy="block_text_split",
            confidence=0.50,
            provenance=_provenance("block_text_split", 0.50),
            dataset_id=None,
        )

    def _attach_captions(
        self,
        tables: List[ExtractedTable],
        caption_blocks: List[DocumentBlock],
        all_blocks: List[DocumentBlock],
    ) -> None:
        """Match caption blocks to tables by page and vertical proximity."""
        for table in tables:
            best_caption: Optional[str] = None
            best_dist = float("inf")
            table_y = (table.bbox[1] if table.bbox and len(table.bbox) >= 2 else 0)

            for cb in caption_blocks:
                if cb.page_num != table.page_num:
                    continue
                if not _CAPTION_RE.match(cb.exact_text.strip()):
                    continue
                cap_y = (cb.bbox[1] if cb.bbox and len(cb.bbox) >= 2 else 0)
                dist = abs(cap_y - table_y)
                if dist < best_dist:
                    best_dist = dist
                    best_caption = cb.exact_text.strip()

            if best_caption:
                table.caption = best_caption

    def _upsert_to_data_dock(
        self,
        tables: List[ExtractedTable],
        doc_id: str,
        pdf_path: Optional[str],
    ) -> None:
        """Write extracted tables to the data dock if it doesn't already have them."""
        try:
            for table in tables:
                if table.dataset_id:
                    # Already written by PdfGridExtractionService
                    continue
                if not table.cells:
                    continue
                # Convert to row-dict format data dock expects
                rows = []
                for row_cells in table.cells[1:]:  # skip header row
                    row_dict = {
                        (table.headers[c.col] if c.col < len(table.headers) else f"col_{c.col}"): c.text
                        for c in row_cells
                    }
                    rows.append(row_dict)
                if not rows:
                    continue

                name = table.caption or f"Table (p.{table.page_num})"
                dataset_id = self._data_dock.add_manual_dataset(
                    doc_id=doc_id,
                    name=name,
                    rows=rows,
                    source_info={
                        "extraction_strategy": table.extraction_strategy,
                        "table_id": table.table_id,
                        "page_num": table.page_num,
                    },
                )
                if dataset_id:
                    table.dataset_id = dataset_id
        except Exception:
            pass
