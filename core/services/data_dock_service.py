from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.models.data_dock_models import ChartConfig, DataGridState, DataProvenance, utc_now_iso
from core.registries.data_provider_registry import DataProviderRegistry


class DataDockService:
    def __init__(self, project_manager=None, provider_registry: Optional[DataProviderRegistry] = None, event_bus=None):
        self.project_manager = project_manager
        self.provider_registry = provider_registry or DataProviderRegistry()
        self.bus = event_bus
        self._open: Dict[str, DataGridState] = {}
        self._active_dataset_id: Optional[str] = None

    def clear_memory(self) -> None:
        self._open.clear()
        self._active_dataset_id = None

    def list_datasets(self) -> List[Dict]:
        saved = []
        if self.project_manager and hasattr(self.project_manager, "list_data_dock_datasets"):
            saved = self.project_manager.list_data_dock_datasets()
        open_ids = set(self._open.keys())
        merged = []
        for state in self._open.values():
            merged.append(self._summary(state))
        for item in saved:
            if item.get("dataset_id") not in open_ids:
                merged.append(item)
        return merged

    def active_dataset(self) -> Optional[DataGridState]:
        return self._open.get(self._active_dataset_id or "")

    def set_active_dataset(self, dataset_id: str) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if state is None:
            state = self.load_dataset(dataset_id)
        if state:
            self._active_dataset_id = state.dataset_id
        return state

    def new_dataset(self, name: str = "Untitled Dataset", rows: int = 8, columns: int = 4) -> DataGridState:
        state = DataGridState(
            dataset_id=f"data_{uuid.uuid4()}",
            name=name,
            headers=[f"Column {i + 1}" for i in range(columns)],
            row_headers=[str(i + 1) for i in range(rows)],
            rows=[["" for _ in range(columns)] for _ in range(rows)],
        )
        self._track(state)
        return state

    def dataset_from_selection(self, selection_payload: Any) -> DataGridState:
        context = self._payload_value(selection_payload, "context", {}) or {}
        provenance = DataProvenance(
            source_pdf_id=self._payload_value(selection_payload, "source_id", None) or context.get("source_id"),
            pdf_path=self._payload_value(selection_payload, "path", None) or context.get("doc_path") or context.get("pdf_path"),
            page_number=context.get("page_num"),
            bounding_box_coordinates=context.get("rects") or [],
            selection_text=self._payload_value(selection_payload, "text", "") or "",
        )
        matrix = context.get("matrix") or self._matrix_from_words(context.get("words") or [], lax=True)
        state = self._state_from_matrix(matrix, "Extracted Dataset", provenance.page_number or 0, provenance.pdf_path or "", provenance.bounding_box_coordinates[0] if provenance.bounding_box_coordinates else None)
        if state is None:
            text = self._payload_value(selection_payload, "text", "") or ""
            state = self.provider_registry.parse_selection(text, provenance)
        if state is None:
            state = self.new_dataset("Extracted Dataset", 1, 1)
        state.provenance = provenance
        self._track(state)
        return state

    def load_dataset(self, dataset_id: str) -> Optional[DataGridState]:
        if dataset_id in self._open:
            return self._open[dataset_id]
        if not self.project_manager or not hasattr(self.project_manager, "get_data_dock_dataset"):
            return None
        state = self.project_manager.get_data_dock_dataset(dataset_id)
        if state:
            state.is_persisted = True
            state.dirty = False
            self._track(state)
        return state

    def save_dataset(self, dataset_id: Optional[str] = None, name: Optional[str] = None) -> Optional[DataGridState]:
        state = self._open.get(dataset_id or self._active_dataset_id or "")
        if not state or not self.project_manager or not hasattr(self.project_manager, "save_data_dock_dataset"):
            return state
        if name:
            state.name = name
        state.updated_at = utc_now_iso()
        saved = self.project_manager.save_data_dock_dataset(state)
        self._track(saved)
        return saved

    def save_dataset_as(self, source_dataset_id: str, name: str) -> Optional[DataGridState]:
        source = self._open.get(source_dataset_id) or self.load_dataset(source_dataset_id)
        if not source:
            return None
        state = DataGridState.from_dict(source.to_dict())
        state.dataset_id = f"data_{uuid.uuid4()}"
        state.name = name or f"{source.name} Copy"
        state.is_persisted = False
        state.dirty = True
        state.created_at = utc_now_iso()
        state.updated_at = state.created_at
        self._track(state)
        return self.save_dataset(state.dataset_id)

    def duplicate_dataset(self, dataset_id: str) -> Optional[DataGridState]:
        state = self._open.get(dataset_id) or self.load_dataset(dataset_id)
        if not state:
            return None
        clone = DataGridState.from_dict(state.to_dict())
        clone.dataset_id = f"data_{uuid.uuid4()}"
        clone.name = f"{state.name} Copy"
        clone.is_persisted = False
        clone.dirty = True
        clone.created_at = utc_now_iso()
        clone.updated_at = clone.created_at
        self._track(clone)
        return clone

    def delete_dataset(self, dataset_id: str) -> None:
        state = self._open.pop(dataset_id, None)
        if state and not state.is_persisted:
            if self._active_dataset_id == dataset_id:
                self._active_dataset_id = next(iter(self._open), None)
            return
        if self.project_manager and hasattr(self.project_manager, "delete_data_dock_dataset"):
            self.project_manager.delete_data_dock_dataset(dataset_id)
        if self._active_dataset_id == dataset_id:
            self._active_dataset_id = next(iter(self._open), None)

    def update_grid(
        self,
        dataset_id: str,
        headers: List[str],
        rows: List[List[str]],
        column_types: Optional[Dict[str, str]] = None,
        row_headers: Optional[List[str]] = None,
    ) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        state.headers = list(headers)
        state.rows = [list(row) for row in rows]
        if row_headers is not None:
            state.row_headers = list(row_headers)
        if len(state.row_headers) < len(state.rows):
            state.row_headers.extend(str(i + 1) for i in range(len(state.row_headers), len(state.rows)))
        elif len(state.row_headers) > len(state.rows):
            state.row_headers = state.row_headers[:len(state.rows)]
        if column_types is not None:
            state.column_types = dict(column_types)
        state.version += 1
        state.dirty = True
        state.updated_at = utc_now_iso()
        return state

    def transpose(self, dataset_id: str) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        matrix = [state.headers] + state.rows
        width = max((len(row) for row in matrix), default=0)
        padded = [row + [""] * (width - len(row)) for row in matrix]
        transposed = [list(row) for row in zip(*padded)] if padded else []
        headers = [str(row[0]) or f"Column {idx + 1}" for idx, row in enumerate(transposed)]
        rows = [row[1:] for row in transposed]
        row_headers = list(state.headers)
        return self.update_grid(dataset_id, headers, rows, {}, row_headers=row_headers)

    def drop_empty(self, dataset_id: str) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        source_row_headers = list(state.row_headers or [])
        if len(source_row_headers) < len(state.rows):
            source_row_headers.extend(str(i + 1) for i in range(len(source_row_headers), len(state.rows)))
        rows = [row for row in state.rows if any(str(cell).strip() for cell in row)]
        row_headers = [
            header for header, row in zip(source_row_headers, state.rows)
            if any(str(cell).strip() for cell in row)
        ]
        keep_cols = [
            idx for idx, header in enumerate(state.headers)
            if str(header).strip() or any(idx < len(row) and str(row[idx]).strip() for row in rows)
        ]
        headers = [state.headers[idx] for idx in keep_cols]
        cleaned_rows = [[row[idx] if idx < len(row) else "" for idx in keep_cols] for row in rows]
        column_types = {h: t for h, t in state.column_types.items() if h in headers}
        return self.update_grid(dataset_id, headers, cleaned_rows, column_types, row_headers=row_headers)

    def selection_summary(self, values: Iterable[Any]) -> Dict[str, Any]:
        nums = []
        for value in values:
            try:
                nums.append(float(str(value).replace(",", "").replace("$", "").replace("%", "")))
            except Exception:
                pass
        total = sum(nums)
        return {
            "sum": total,
            "average": total / len(nums) if nums else 0,
            "count": len(nums),
        }

    def chart_config_for_active(self, chart_type: str = "bar") -> Optional[ChartConfig]:
        state = self.active_dataset()
        if not state:
            return None
        headers = list(state.headers)
        return ChartConfig(
            chart_id=f"chart_{uuid.uuid4()}",
            name=f"{state.name} Chart",
            chart_type=chart_type,
            dataset_id=state.dataset_id if state.is_persisted else None,
            x_field=headers[0] if headers else "",
            y_field=headers[1] if len(headers) > 1 else (headers[0] if headers else ""),
            x_title=headers[0] if headers else "",
            y_title=headers[1] if len(headers) > 1 else "",
        )

    def extract_document(self, doc: Any, pdf_path: str = "") -> List[DataGridState]:
        extracted: List[DataGridState] = []
        if doc is None:
            return extracted
        try:
            page_count = len(doc)
        except Exception:
            return extracted
        for page_index in range(page_count):
            try:
                page = doc.load_page(page_index)
            except Exception:
                continue
            extracted.extend(self._extract_page_tables(page, page_index, pdf_path))
        for state in extracted:
            self._track(state)
        return extracted

    def _extract_page_tables(self, page: Any, page_index: int, pdf_path: str) -> List[DataGridState]:
        states = []

        # Step 1: Use AI layout model to pinpoint table regions precisely.
        layout_bboxes = self._find_table_bboxes_with_layout(page)
        if layout_bboxes:
            for clip_rect in layout_bboxes:
                state = self._extract_table_from_region(
                    page, page_index, pdf_path, clip_rect, len(states), lax=True
                )
                if state:
                    states.append(state)
            if states:
                return states

        # Step 2: Full-page PyMuPDF structural table detection.
        tables = []
        try:
            finder = page.find_tables()
            tables = list(getattr(finder, "tables", []) or [])
        except Exception:
            tables = []
        for table in tables:
            try:
                matrix = table.extract()
            except Exception:
                matrix = []
            if not self._matrix_quality_ok(matrix):
                continue
            state = self._state_from_matrix(
                matrix,
                self._table_name(matrix, page_index, len(states)),
                page_index, pdf_path, getattr(table, "bbox", None),
            )
            if state:
                states.append(state)
        if states:
            return states

        # Step 3: Whole-page word-based fallback (strict — no arbitrary paragraphs).
        try:
            words = [list(w[:5]) for w in page.get_text("words")]
        except Exception:
            words = []
        matrix = self._matrix_from_words(words, require_table_like=True)
        if matrix:
            state = self._state_from_matrix(
                matrix,
                self._table_name(matrix, page_index, 0),
                page_index, pdf_path, self._page_bbox(page),
            )
            if state:
                states.append(state)
        return states

    def _extract_table_from_region(self, page: Any, page_index: int, pdf_path: str,
                                    clip_rect: Any, table_idx: int, lax: bool = False) -> Optional[DataGridState]:
        """Extract a table from a pre-identified region, trying PyMuPDF then word clustering."""
        try:
            import fitz
            clip = fitz.Rect(clip_rect) if not hasattr(clip_rect, "x0") else clip_rect
        except Exception:
            return None

        # Try structural detection clipped to the region.
        try:
            finder = page.find_tables(clip=clip)
            tables = list(getattr(finder, "tables", []) or [])
        except Exception:
            tables = []
        for table in tables:
            try:
                matrix = table.extract()
            except Exception:
                continue
            if not self._matrix_quality_ok(matrix):
                continue
            return self._state_from_matrix(
                matrix,
                self._table_name(matrix, page_index, table_idx),
                page_index, pdf_path,
                list(getattr(table, "bbox", clip)),
            )

        # Fall back to word-based for this region.
        try:
            words = [list(w[:5]) for w in page.get_text("words", clip=clip)]
        except TypeError:
            all_words = page.get_text("words")
            try:
                import fitz
                words = [list(w[:5]) for w in all_words if fitz.Rect(w[:4]).intersects(clip)]
            except Exception:
                words = []
        if not words:
            return None
        matrix = self._matrix_from_words(words, lax=lax)
        if not matrix:
            return None
        return self._state_from_matrix(
            matrix,
            self._table_name(matrix, page_index, table_idx),
            page_index, pdf_path, list(clip),
        )

    def _table_name(self, matrix: List[List[Any]], page_index: int, table_index: int) -> str:
        """Generate a meaningful dataset name from the first row of a matrix."""
        if matrix and len(matrix) > 1:
            first_row = [str(c).strip() for c in matrix[0] if str(c).strip()]
            if first_row:
                preview = " | ".join(first_row[:4])
                if len(first_row) > 4:
                    preview += " ..."
                return f"P{page_index + 1}: {preview[:60]}"
        return f"Page {page_index + 1} Table {table_index + 1}"

    def _get_layout_model(self) -> Any:
        """Lazily load and cache the Artifex layout AI model."""
        if not hasattr(self, "_layout_model"):
            try:
                from pymupdf.layout.DocumentLayoutAnalyzer import get_model
                self._layout_model = get_model()
            except Exception:
                self._layout_model = None
        return self._layout_model

    def _find_table_bboxes_with_layout(self, page: Any) -> List[Any]:
        """Run the layout model to identify table regions; returns fitz.Rect list."""
        model = self._get_layout_model()
        if model is None:
            return []
        try:
            import fitz
            results = model.predict(page)
            return [fitz.Rect(r[0], r[1], r[2], r[3]) for r in results if r[4] == "table"]
        except Exception:
            return []

    def _extract_page_chart_inventory(self, page: Any, page_index: int, pdf_path: str) -> Optional[DataGridState]:
        rows = []
        try:
            page_dict = page.get_text("dict")
            for idx, block in enumerate(page_dict.get("blocks") or []):
                bbox = block.get("bbox")
                if block.get("type") == 1 and bbox:
                    rows.append([page_index + 1, f"Image/Chart Region {idx + 1}", *[round(float(v), 2) for v in bbox]])
        except Exception:
            pass
        try:
            text = page.get_text("text") or ""
            for line in text.splitlines():
                stripped = line.strip()
                low = stripped.lower()
                if low.startswith(("figure ", "fig. ", "chart ")) or " chart " in f" {low} ":
                    rows.append([page_index + 1, stripped[:80], "", "", "", ""])
        except Exception:
            pass
        if not rows:
            return None
        return DataGridState(
            dataset_id=f"data_{uuid.uuid4()}",
            name=f"Page {page_index + 1} Chart/Image Regions",
            headers=["Page", "Detected Item", "x0", "y0", "x1", "y1"],
            row_headers=[str(i + 1) for i in range(len(rows))],
            rows=[[str(cell) for cell in row] for row in rows],
            provenance=DataProvenance(pdf_path=pdf_path, page_number=page_index, bounding_box_coordinates=[self._page_bbox(page)]),
        )

    def _matrix_quality_ok(self, matrix: List[List[Any]]) -> bool:
        rows = [row for row in (matrix or []) if any(str(cell or "").strip() for cell in (row or []))]
        if len(rows) < 2:
            return False
        width = max((len(row) for row in rows), default=0)
        if width < 2:
            return False
        total = len(rows) * width
        filled = sum(1 for row in rows for cell in (row + [None] * (width - len(row))) if str(cell or "").strip())
        return filled >= total * 0.3

    def _state_from_matrix(self, matrix: List[List[Any]], name: str, page_index: int, pdf_path: str, bbox: Any = None) -> Optional[DataGridState]:
        rows = [
            [str(cell or "").replace("\n", " ").strip() for cell in (row or [])]
            for row in matrix or []
            if any(str(cell or "").strip() for cell in (row or []))
        ]
        if not rows:
            return None
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        text = "\n".join("\t".join(row) for row in rows)
        state = self.provider_registry.parse_selection(
            text,
            DataProvenance(
                pdf_path=pdf_path,
                page_number=page_index,
                bounding_box_coordinates=[list(bbox) if bbox else []],
                selection_text=text,
            ),
        )
        if state:
            state.name = name
        return state

    def _matrix_from_words(self, words: List[Any], require_table_like: bool = False, lax: bool = False) -> List[List[str]]:
        normalized = self._normalize_words(words)
        if len(normalized) < 4:
            return []
        rows = self._cluster_word_rows(normalized)
        if len(rows) < 2:
            return []
        row_cells = [self._row_to_cells(row) for row in rows]
        row_cells = [cells for cells in row_cells if cells]
        if len(row_cells) < 2:
            return []
        matrix = self._align_cells_to_columns(row_cells)
        matrix = self._trim_empty_matrix(matrix)
        if not lax and not self._looks_like_data_table(matrix):
            return []
        if not lax and require_table_like and not self._has_repeated_column_structure(row_cells, matrix):
            return []
        return matrix

    def _normalize_words(self, words: List[Any]) -> List[Dict[str, Any]]:
        normalized = []
        for word in words:
            if isinstance(word, dict):
                text = word.get("text") or word.get("word") or ""
                x0, y0 = word.get("x0", 0), word.get("y0", 0)
                x1, y1 = word.get("x1", x0), word.get("y1", y0)
            else:
                if len(word) < 5:
                    continue
                x0, y0, x1, y1, text = word[:5]
            text = str(text).strip()
            if not text:
                continue
            try:
                normalized.append({
                    "x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1),
                    "text": text,
                    "cx": (float(x0) + float(x1)) / 2,
                    "cy": (float(y0) + float(y1)) / 2,
                    "w": max(1.0, float(x1) - float(x0)),
                    "h": max(1.0, float(y1) - float(y0)),
                })
            except Exception:
                continue
        return sorted(normalized, key=lambda item: (item["cy"], item["x0"]))

    def _cluster_word_rows(self, words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        heights = sorted(w["h"] for w in words)
        median_h = heights[len(heights) // 2] if heights else 8
        tolerance = max(3.0, min(10.0, median_h * 0.65))
        rows: List[List[Dict[str, Any]]] = []
        row_centers: List[float] = []
        for word in words:
            target = None
            for idx, center in enumerate(row_centers):
                if abs(word["cy"] - center) <= tolerance:
                    target = idx
                    break
            if target is None:
                rows.append([word])
                row_centers.append(word["cy"])
            else:
                rows[target].append(word)
                row_centers[target] = sum(w["cy"] for w in rows[target]) / len(rows[target])
        rows.sort(key=lambda row: sum(w["cy"] for w in row) / len(row))
        for row in rows:
            row.sort(key=lambda item: item["x0"])
        return rows

    def _row_to_cells(self, row: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not row:
            return []
        widths = sorted(w["w"] for w in row)
        median_w = widths[len(widths) // 2] if widths else 8
        gaps = [row[idx + 1]["x0"] - row[idx]["x1"] for idx in range(len(row) - 1)]
        positive_gaps = sorted(g for g in gaps if g > 0)
        median_gap = positive_gaps[len(positive_gaps) // 2] if positive_gaps else 0
        split_gap = max(8.0, median_w * 0.85, median_gap * 1.8 if median_gap <= median_w * 0.6 else 0)
        cells = []
        current = [row[0]]
        for prev, word in zip(row, row[1:]):
            gap = word["x0"] - prev["x1"]
            if gap >= split_gap:
                cells.append(self._cell_from_words(current))
                current = [word]
            else:
                current.append(word)
        cells.append(self._cell_from_words(current))
        return cells

    def _cell_from_words(self, words: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "x0": min(w["x0"] for w in words),
            "x1": max(w["x1"] for w in words),
            "text": " ".join(w["text"] for w in words),
        }

    def _align_cells_to_columns(self, row_cells: List[List[Dict[str, Any]]]) -> List[List[str]]:
        starts = sorted(cell["x0"] for cells in row_cells for cell in cells)
        if not starts:
            return []
        deltas = sorted(starts[idx + 1] - starts[idx] for idx in range(len(starts) - 1) if starts[idx + 1] - starts[idx] > 1)
        tolerance = max(12.0, (deltas[len(deltas) // 2] * 0.35) if deltas else 16.0)
        clusters: List[List[float]] = []
        for start in starts:
            if not clusters or abs(start - (sum(clusters[-1]) / len(clusters[-1]))) > tolerance:
                clusters.append([start])
            else:
                clusters[-1].append(start)
        anchors = [sum(cluster) / len(cluster) for cluster in clusters]
        matrix = []
        for cells in row_cells:
            row = ["" for _ in anchors]
            for cell in cells:
                idx = min(range(len(anchors)), key=lambda i: abs(cell["x0"] - anchors[i]))
                row[idx] = f"{row[idx]} {cell['text']}".strip() if row[idx] else cell["text"]
            matrix.append(row)
        return matrix

    def _trim_empty_matrix(self, matrix: List[List[str]]) -> List[List[str]]:
        rows = [[str(cell).strip() for cell in row] for row in matrix if any(str(cell).strip() for cell in row)]
        if not rows:
            return []
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        keep = [idx for idx in range(width) if any(row[idx] for row in rows)]
        return [[row[idx] for idx in keep] for row in rows]

    def _looks_like_data_table(self, matrix: List[List[str]]) -> bool:
        if len(matrix) < 2:
            return False
        width = max((len(row) for row in matrix), default=0)
        if width < 2:
            return False
        non_empty_rows = [row for row in matrix if sum(1 for cell in row if str(cell).strip()) >= 2]
        if len(non_empty_rows) < 2:
            return False
        numeric_cells = sum(1 for row in matrix[1:] for cell in row if self._is_numericish(cell) or self._is_dateish(cell))
        return numeric_cells >= max(1, len(matrix) - 1)

    def _has_repeated_column_structure(self, row_cells: List[List[Dict[str, Any]]], matrix: List[List[str]]) -> bool:
        expected_width = max((len(row) for row in matrix), default=0)
        rows_with_multiple_cells = sum(1 for cells in row_cells if len(cells) >= 2)
        rows_with_expected_width = sum(1 for row in matrix if sum(1 for cell in row if cell) >= min(2, expected_width))
        return expected_width >= 2 and rows_with_multiple_cells >= 2 and rows_with_expected_width >= 2

    def _is_numericish(self, value: Any) -> bool:
        text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
        if not text:
            return False
        try:
            float(text)
            return True
        except Exception:
            return False

    def _is_dateish(self, value: Any) -> bool:
        text = str(value).strip()
        if not text:
            return False
        return any(sep in text for sep in ("-", "/", ":")) and any(ch.isdigit() for ch in text)

    def _page_bbox(self, page: Any) -> List[float]:
        try:
            rect = page.rect
            return [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
        except Exception:
            return []

    def _track(self, state: DataGridState) -> DataGridState:
        self._open[state.dataset_id] = state
        self._active_dataset_id = state.dataset_id
        return state

    def _summary(self, state: DataGridState) -> Dict[str, Any]:
        return {
            "dataset_id": state.dataset_id,
            "name": state.name,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "is_persisted": state.is_persisted,
            "dirty": state.dirty,
            "open": True,
        }

    def _payload_value(self, payload: Any, key: str, default: Any = None) -> Any:
        if isinstance(payload, dict):
            return payload.get(key, default)
        return getattr(payload, key, default)

    def _table_text_from_words(self, words: List[Any]) -> str:
        normalized = []
        for word in words:
            if isinstance(word, dict):
                text = word.get("text") or word.get("word") or ""
                x0, y0 = word.get("x0", 0), word.get("y0", 0)
            else:
                if len(word) < 5:
                    continue
                x0, y0, text = word[0], word[1], word[4]
            if str(text).strip():
                normalized.append((float(x0), float(y0), str(text)))
        if not normalized:
            return ""
        normalized.sort(key=lambda item: (item[1], item[0]))
        rows = []
        current = []
        current_y = None
        for x0, y0, text in normalized:
            if current_y is None or abs(y0 - current_y) <= 4:
                current.append((x0, text))
                current_y = y0 if current_y is None else (current_y + y0) / 2
            else:
                rows.append(current)
                current = [(x0, text)]
                current_y = y0
        if current:
            rows.append(current)
        lines = []
        for row in rows:
            row.sort(key=lambda item: item[0])
            lines.append("\t".join(text for _, text in row))
        return "\n".join(lines)
