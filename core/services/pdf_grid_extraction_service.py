from __future__ import annotations

from statistics import median
from typing import Any, Optional

from core.models.data_dock_models import DataProvenance, ExtractedGrid, PdfRegion


class PdfGridExtractionService:
    """Extract text-backed PDF regions without UI or model dependencies."""

    def __init__(self, provider_registry=None):
        self.provider_registry = provider_registry

    def extract(
        self,
        region: PdfRegion,
        *,
        expected_rows: Optional[int] = None,
        expected_columns: Optional[int] = None,
    ) -> ExtractedGrid:
        if len(region.bbox) != 4 or not region.pdf_path:
            raise ValueError("A PDF path, page number, and four-coordinate bounding box are required.")
        provenance = DataProvenance(
            source_path=region.pdf_path,
            pdf_path=region.pdf_path,
            page_number=region.page_number,
            bounding_box_coordinates=[list(region.bbox)],
            selection_ref=region.request_id or None,
        )
        payload = {"region": region, "expected_rows": expected_rows, "expected_columns": expected_columns}
        if self.provider_registry:
            for spec in self.provider_registry.grid_extractors().values():
                if spec.callback:
                    try:
                        result = spec.callback(payload)
                    except Exception:
                        continue
                    if result is not None:
                        return result if isinstance(result, ExtractedGrid) else ExtractedGrid.from_dict(result)

        import fitz

        with fitz.open(region.pdf_path) as doc:
            if region.page_number < 0 or region.page_number >= len(doc):
                raise ValueError("PDF page number is outside the document.")
            page = doc.load_page(region.page_number)
            clip = fitz.Rect(region.bbox) & page.rect
            if clip.is_empty:
                raise ValueError("The selected PDF region is empty or outside the page.")
            table = self._from_tables(page, clip, provenance)
            if table and self._shape_compatible(table, expected_rows, expected_columns):
                return table
            words = self._words(page, clip)
            if not words:
                return ExtractedGrid(
                    strategy="unsupported",
                    warnings=["No text was found. Run OCR before extracting data from a scanned region."],
                    provenance=provenance,
                )
            return self._from_words(words, provenance, expected_rows, expected_columns)

    def _from_tables(self, page, clip, provenance) -> Optional[ExtractedGrid]:
        try:
            finder = page.find_tables(clip=clip)
            tables = list(getattr(finder, "tables", []) or [])
        except Exception:
            return None
        if not tables:
            return None
        table = max(tables, key=lambda item: len(item.extract() or []) * max((len(row or []) for row in item.extract() or []), default=0))
        cells = [[str(value or "").replace("\n", " ").strip() for value in row or []] for row in table.extract() or []]
        if not cells:
            return None
        width = max(len(row) for row in cells)
        cells = [row + [""] * (width - len(row)) for row in cells]
        raw_boxes = list(getattr(table, "cells", []) or [])
        boxes = []
        for row in range(len(cells)):
            boxes.append([list(raw_boxes[row * width + column]) if row * width + column < len(raw_boxes) and raw_boxes[row * width + column] else [] for column in range(width)])
        return ExtractedGrid(cells, boxes, "pymupdf_table", [], provenance)

    def _from_words(self, words, provenance, expected_rows, expected_columns) -> ExtractedGrid:
        rows = self._cluster_rows(words)
        if expected_columns == 1:
            if expected_rows and expected_rows > 1 and len(rows) == 1 and len(rows[0]) >= expected_rows:
                groups = self._partition_horizontal(rows[0], expected_rows)
                return ExtractedGrid(
                    [[" ".join(word[4] for word in group) for group in groups]],
                    [[self._union_boxes(group) for group in groups]],
                    "pymupdf_words_transposed_row", [], provenance,
                )
            cells = [[" ".join(word[4] for word in row)] for row in rows]
            boxes = [[self._union_boxes(row)] for row in rows]
            cells, boxes = self._fit_vector(cells, boxes, expected_rows, axis="rows")
            return ExtractedGrid(cells, boxes, "pymupdf_words_column", [], provenance)
        if expected_rows == 1:
            if expected_columns and expected_columns > 1 and len(rows) >= expected_columns:
                cells = [[" ".join(word[4] for word in row)] for row in rows]
                boxes = [[self._union_boxes(row)] for row in rows]
                cells, boxes = self._fit_vector(cells, boxes, expected_columns, axis="rows")
                return ExtractedGrid(cells, boxes, "pymupdf_words_transposed_column", [], provenance)
            flat = [word for row in rows for word in row]
            groups = self._partition_horizontal(flat, expected_columns)
            return ExtractedGrid([[" ".join(word[4] for word in group) for group in groups]],
                                 [[self._union_boxes(group) for group in groups]],
                                 "pymupdf_words_row", [], provenance)
        if expected_rows and expected_columns:
            row_groups = self._partition_rows(rows, expected_rows)
            matrix, boxes = [], []
            for group in row_groups:
                horizontal = self._partition_horizontal([word for row in group for word in row], expected_columns)
                matrix.append([" ".join(word[4] for word in cell) for cell in horizontal])
                boxes.append([self._union_boxes(cell) for cell in horizontal])
            return ExtractedGrid(matrix, boxes, "pymupdf_words_expected_grid", [], provenance)
        matrix, boxes = self._infer_matrix(rows)
        return ExtractedGrid(matrix, boxes, "pymupdf_words_geometry", [], provenance)

    @staticmethod
    def _words(page, clip):
        try:
            return [tuple(word[:5]) for word in page.get_text("words", clip=clip) if str(word[4]).strip()]
        except TypeError:
            return [tuple(word[:5]) for word in page.get_text("words") if clip.intersects(type(clip)(word[:4])) and str(word[4]).strip()]

    @staticmethod
    def _cluster_rows(words):
        ordered = sorted(words, key=lambda word: ((word[1] + word[3]) / 2, word[0]))
        heights = [max(1.0, word[3] - word[1]) for word in ordered]
        tolerance = max(2.5, median(heights) * 0.65) if heights else 4.0
        rows, centers = [], []
        for word in ordered:
            center = (word[1] + word[3]) / 2
            index = next((idx for idx, value in enumerate(centers) if abs(center - value) <= tolerance), None)
            if index is None:
                rows.append([word]); centers.append(center)
            else:
                rows[index].append(word)
                centers[index] = sum((item[1] + item[3]) / 2 for item in rows[index]) / len(rows[index])
        return [sorted(row, key=lambda word: word[0]) for _, row in sorted(zip(centers, rows))]

    @staticmethod
    def _partition_horizontal(words, count):
        ordered = sorted(words, key=lambda word: (word[0] + word[2]) / 2)
        if not count or count <= 1:
            return [ordered]
        if len(ordered) < count:
            return [[word] for word in ordered] + [[] for _ in range(count - len(ordered))]
        gaps = [(ordered[index + 1][0] - ordered[index][2], index) for index in range(len(ordered) - 1)]
        boundaries = sorted(index for _, index in sorted(gaps, reverse=True)[:count - 1])
        groups, start = [], 0
        for boundary in boundaries:
            groups.append(ordered[start:boundary + 1]); start = boundary + 1
        groups.append(ordered[start:])
        return groups + [[] for _ in range(count - len(groups))]

    @staticmethod
    def _partition_rows(rows, count):
        if len(rows) == count:
            return [[row] for row in rows]
        result = [[] for _ in range(count)]
        for index, row in enumerate(rows):
            target = min(count - 1, int(index * count / max(1, len(rows))))
            result[target].append(row)
        return result

    def _infer_matrix(self, rows):
        # Gap-based inference is intentionally conservative; expected shapes are preferred.
        gap_counts = []
        for row in rows:
            widths = [max(1.0, word[2] - word[0]) for word in row]
            threshold = (median(widths) * 1.8) if widths else 12
            gap_counts.append(1 + sum(1 for left, right in zip(row, row[1:]) if right[0] - left[2] > threshold))
        columns = max(1, int(median(gap_counts))) if gap_counts else 1
        matrix, boxes = [], []
        for row in rows:
            groups = self._partition_horizontal(row, columns)
            matrix.append([" ".join(word[4] for word in group) for group in groups])
            boxes.append([self._union_boxes(group) for group in groups])
        return matrix, boxes

    @staticmethod
    def _union_boxes(words):
        return [] if not words else [min(word[0] for word in words), min(word[1] for word in words),
                                     max(word[2] for word in words), max(word[3] for word in words)]

    @staticmethod
    def _fit_vector(cells, boxes, expected, axis="rows"):
        if not expected or len(cells) == expected:
            return cells, boxes
        # Wrapped lines are merged into evenly ordered destination slots, never dropped.
        merged_cells, merged_boxes = [], []
        groups = [[] for _ in range(expected)]
        box_groups = [[] for _ in range(expected)]
        for index, (cell, box) in enumerate(zip(cells, boxes)):
            target = min(expected - 1, int(index * expected / max(1, len(cells))))
            groups[target].extend(cell)
            if box and box[0]:
                box_groups[target].append(box[0])
        for values, value_boxes in zip(groups, box_groups):
            merged_cells.append([" ".join(value for value in values if value)])
            merged_boxes.append([[
                min(box[0] for box in value_boxes), min(box[1] for box in value_boxes),
                max(box[2] for box in value_boxes), max(box[3] for box in value_boxes),
            ] if value_boxes else []])
        return merged_cells, merged_boxes

    @staticmethod
    def _shape_compatible(grid, rows, columns):
        actual = grid.shape
        return (not rows or not columns or actual == (rows, columns) or actual == (columns, rows))
