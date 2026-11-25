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

from core.models.data_dock_models import DataGridState, DataProvenance
from core.models.extraction_models import (
    DocumentBlock,
    ExtractedTable,
    ExtractedTableCell,
    ExtractionProvenance,
    SourceLocation,
)

_EXTRACTOR_VERSION = "1.1"
# Table detectors (layout model + two find_tables passes) cost ~0.6 s a page
# and hold the GIL; a 424-page prose book spent minutes finding no tables and
# stalled the UI. They now run only on pages that show a sign of a table.
_MIN_RULING_SHAPES = 4          # ruled tables draw lines/rects
_MIN_NUMERIC_TOKENS = 8
_NUMERIC_SHARE = 0.35
_NUMERIC_RE = re.compile(r"^[\(\-–$€£]?\d[\d.,:%/)]*$")
_TABLE_CAPTION_RE = re.compile(r"^(?:table|tbl\.?)\s*[\dIVX]", re.I)

_CAPTION_RE = re.compile(
    r"^(?:table|tbl\.?|figure|fig\.?)\s*\d",
    re.I,
)


def table_hint_pages(blocks: List[DocumentBlock]) -> set:
    """Pages whose text suggests a table: a table block, a "Table N" caption,
    or a block dense with numbers."""
    pages = set()
    for block in blocks:
        text = block.exact_text or ""
        if block.block_type == "table" or _TABLE_CAPTION_RE.match(text.strip()) or _numeric_dense(text):
            pages.add(block.page_num)
    return pages


def page_has_ruling(page) -> bool:
    """Ruled tables draw lines; get_cdrawings is the fast C-level listing."""
    try:
        return len(page.get_cdrawings()) >= _MIN_RULING_SHAPES
    except Exception:
        return True


def _numeric_dense(text: str) -> bool:
    tokens = text.split()
    numeric = sum(1 for token in tokens if _NUMERIC_RE.match(token))
    return numeric >= _MIN_NUMERIC_TOKENS and numeric / max(1, len(tokens)) >= _NUMERIC_SHARE


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
      - DataDockService's PDF table extractor (preferred, when a path is known)
      - DocumentBlock objects already classified as 'table' by the layout parser

    Every table ends up as a Data Dock dataset, so the dock, analysis, and RAG
    all read the same extraction instead of running their own.
    """

    def __init__(
        self,
        project_manager,
        extraction_cache,
        data_dock_service=None,
    ) -> None:
        self._pm = project_manager
        self._cache = extraction_cache
        self._data_dock = data_dock_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_tables(
        self, doc_id: str, blocks: List[DocumentBlock], pdf_path: Optional[str] = None
    ) -> List[ExtractedTable]:
        """
        Extract tables from a document and return canonical ExtractedTable objects.

        Prefers the Data Dock PDF table extractor when a path is known; falls
        back to DocumentBlock-level table classification.
        """
        cached = self._cache.get(doc_id, "table_extraction_service", "tables")
        if cached:
            return cached

        tables: List[ExtractedTable] = []

        # Source 1: the Data Dock extractor (fine-grained, preferred)
        if self._data_dock and pdf_path:
            tables.extend(self._extract_via_data_dock(doc_id, pdf_path, table_hint_pages(blocks)))

        # Source 2: table blocks not yet covered by the extractor above
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

        # Publish anything the fallback produced so the dock holds every table
        if self._data_dock:
            self._publish_to_data_dock(tables, doc_id, pdf_path)

        self._cache.set(doc_id, "table_extraction_service", _EXTRACTOR_VERSION, "tables", tables)
        return tables

    def get_tables(self, doc_id: str) -> List[ExtractedTable]:
        """Return cached tables (empty list if not yet extracted)."""
        result = self._cache.get(doc_id, "table_extraction_service", "tables")
        return result or []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_via_data_dock(self, doc_id: str, pdf_path: str,
                               hint_pages: Optional[set] = None) -> List[ExtractedTable]:
        """Extract this document's tables once, through the Data Dock.

        Datasets already recorded for the document are reused as-is, so
        re-running ingestion never duplicates them.
        """
        states = self._data_dock.datasets_for_document(doc_id, pdf_path)
        if not states:
            states = self._run_data_dock_extraction(doc_id, pdf_path, hint_pages)
        return [self._state_to_table(state, doc_id) for state in states if state.rows]

    def _run_data_dock_extraction(self, doc_id: str, pdf_path: str,
                                  hint_pages: Optional[set] = None) -> List[DataGridState]:
        try:
            import fitz
            doc = fitz.open(pdf_path)
        except Exception:
            return []
        try:
            page_filter = None if hint_pages is None else (
                lambda index, page: index in hint_pages or page_has_ruling(page))
            states = self._data_dock.extract_document(doc, pdf_path, doc_id=doc_id, page_filter=page_filter)
        except Exception:
            states = []
        finally:
            doc.close()
        for state in states:
            self._persist(state)
        return states

    def _persist(self, state: DataGridState) -> None:
        """Save a dataset so it outlives the session that extracted it."""
        try:
            self._data_dock.save_dataset(state.dataset_id)
        except Exception:
            pass

    @staticmethod
    def _state_to_table(state: DataGridState, doc_id: str) -> ExtractedTable:
        """Convert a Data Dock dataset into the canonical ExtractedTable."""
        headers = list(state.headers)
        cells: List[List[ExtractedTableCell]] = []
        if headers:
            cells.append([
                ExtractedTableCell(row=0, col=col, text=text, is_header=True)
                for col, text in enumerate(headers)
            ])
        for row_index, row in enumerate(state.rows, start=1 if headers else 0):
            cells.append([
                ExtractedTableCell(row=row_index, col=col, text=str(value), is_header=False)
                for col, value in enumerate(row)
            ])
        boxes = state.provenance.bounding_box_coordinates or []
        strategy = str((state.metadata or {}).get("extraction_strategy") or "data_dock")
        confidence = float((state.metadata or {}).get("confidence") or 0.75)
        return ExtractedTable(
            table_id=str(uuid.uuid4()),
            doc_id=doc_id,
            page_num=state.provenance.page_number or 0,
            caption=state.name,
            headers=headers,
            cells=cells,
            bbox=boxes[0] if boxes else None,
            extraction_strategy=strategy,
            confidence=confidence,
            provenance=_provenance(strategy, confidence),
            dataset_id=state.dataset_id,
        )

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

    def _publish_to_data_dock(
        self,
        tables: List[ExtractedTable],
        doc_id: str,
        pdf_path: Optional[str],
    ) -> None:
        """Give every table without a dataset one, so the dock shows them all."""
        for table in tables:
            if table.dataset_id or not table.cells:
                continue
            rows = [[cell.text for cell in row_cells] for row_cells in table.cells[1:]]
            if not rows:
                continue
            headers = table.headers or [f"col_{cell.col}" for cell in table.cells[0]]
            page_label = f" (p.{table.page_num + 1})" if table.page_num is not None else ""
            try:
                state = self._data_dock.dataset_from_rows(
                    name=table.caption or f"Table{page_label}",
                    headers=headers,
                    rows=rows,
                    provenance=DataProvenance(
                        source_id=doc_id,
                        source_pdf_id=doc_id,
                        source_path=pdf_path,
                        pdf_path=pdf_path,
                        page_number=table.page_num,
                    ),
                    metadata={
                        "extraction_strategy": table.extraction_strategy,
                        "table_id": table.table_id,
                    },
                )
            except Exception:
                continue
            table.dataset_id = state.dataset_id
            self._persist(state)
