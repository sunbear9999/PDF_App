from __future__ import annotations

import copy
import csv
import io
import json
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.models.data_dock_models import (
    ChartConfig, DataGridState, DataProvenance, ExtractedGrid, GridCoordinate,
    GridPatchPlan, GridPatchResult, GridSelection, utc_now_iso,
)
from core.registries.data_provider_registry import DataProviderRegistry
from core.services.grid_mapping_service import GridMappingService
from core.events.domains.project_events import ProjectIntent
from core.utils.numeric_utils import coerce_number


class DataDockService:
    def __init__(self, project_manager=None, provider_registry: Optional[DataProviderRegistry] = None, event_bus=None):
        self.project_manager = project_manager
        self.provider_registry = provider_registry or DataProviderRegistry()
        self.bus = event_bus
        self._open: Dict[str, DataGridState] = {}
        self._active_dataset_id: Optional[str] = None
        self.grid_mapping_service = GridMappingService()
        if self.bus is not None and hasattr(self.bus, "project_action_requested"):
            self.bus.project_action_requested.connect(self._handle_project_intent)

    def _handle_project_intent(self, intent, payload) -> None:
        if intent == ProjectIntent.FLUSH_UI_STATES:
            self.save_all_open_datasets()

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
            source_id=self._payload_value(selection_payload, "source_id", None) or context.get("source_id"),
            source_path=self._payload_value(selection_payload, "path", None) or context.get("doc_path") or context.get("pdf_path"),
            source_type=context.get("source_type") or "pdf",
            pdf_path=self._payload_value(selection_payload, "path", None) or context.get("doc_path") or context.get("pdf_path"),
            page_number=context.get("page_num"),
            bounding_box_coordinates=context.get("rects") or [],
            selection_text=self._payload_value(selection_payload, "text", "") or "",
            selection_ref=context.get("selection_ref"),
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

    def save_all_open_datasets(self) -> List[DataGridState]:
        if not self.project_manager or not hasattr(self.project_manager, "save_data_dock_dataset"):
            return list(self._open.values())
        saved_states: List[DataGridState] = []
        for dataset_id in list(self._open.keys()):
            state = self._open.get(dataset_id)
            if not state:
                continue
            if state.dirty or not state.is_persisted:
                saved = self.save_dataset(dataset_id)
                if saved:
                    saved_states.append(saved)
            else:
                saved_states.append(state)
        return saved_states

    def rename_dataset(self, dataset_id: str, name: str, save: bool = False) -> Optional[DataGridState]:
        state = self._open.get(dataset_id) or self.load_dataset(dataset_id)
        if not state:
            return None
        state.name = (name or state.name).strip() or state.name
        state.version += 1
        state.dirty = True
        state.updated_at = utc_now_iso()
        self._record_history(state, "rename_dataset", {"name": state.name})
        return self.save_dataset(dataset_id) if save and state.is_persisted else state

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

    def update_cell(self, dataset_id: str, row: int, column: int, value: Any) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state or row < 0 or column < 0:
            return None
        self._ensure_shape(state, row + 1, column + 1)
        state.rows[row][column] = "" if value is None else str(value)
        state.cell_provenance.pop(f"{row}:{column}", None)
        state.cell_provenance.pop(f"data:{row}:{column}", None)
        state.version += 1
        state.dirty = True
        state.updated_at = utc_now_iso()
        return state

    def update_header(self, dataset_id: str, orientation: str, index: int, value: Any) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state or index < 0:
            return None
        text = "" if value is None else str(value)
        if orientation == "column":
            self._ensure_shape(state, len(state.rows), index + 1)
            state.headers[index] = text or f"Column {index + 1}"
            state.cell_provenance.pop(f"column_header:{index}", None)
        else:
            self._ensure_shape(state, index + 1, len(state.headers))
            state.row_headers[index] = text or str(index + 1)
            state.cell_provenance.pop(f"row_header:{index}", None)
        state.version += 1
        state.dirty = True
        state.updated_at = utc_now_iso()
        return state

    def plan_pdf_fill(
        self,
        dataset_id: str,
        expected_version: int,
        selection: GridSelection | Dict[str, Any],
        extracted_grid: ExtractedGrid | Dict[str, Any],
        preferred_orientation: Optional[str] = None,
    ) -> GridPatchPlan:
        state = self._open.get(dataset_id) or self.load_dataset(dataset_id)
        if not state:
            raise ValueError("The destination dataset is no longer available.")
        selection = selection if isinstance(selection, GridSelection) else GridSelection.from_dict(selection)
        extracted_grid = extracted_grid if isinstance(extracted_grid, ExtractedGrid) else ExtractedGrid.from_dict(extracted_grid)
        if expected_version != state.version:
            raise ValueError("The dataset changed after the PDF fill began; select the cells again.")
        for spec in self.provider_registry.grid_mapping_strategies().values():
            if spec.callback:
                try:
                    custom = spec.callback(state, selection, extracted_grid)
                except Exception:
                    continue
                if custom is not None:
                    return custom
        return self.grid_mapping_service.plan(state, selection, extracted_grid, preferred_orientation)

    def apply_grid_patch(self, plan: GridPatchPlan, conflict_policy: str = "cancel") -> GridPatchResult:
        state = self._open.get(plan.dataset_id) or self.load_dataset(plan.dataset_id)
        if not state:
            raise ValueError("The destination dataset is no longer available.")
        if state.version != plan.expected_version:
            raise ValueError("The dataset changed after the fill was previewed; no values were applied.")
        policy = str(conflict_policy or "cancel").lower()
        if plan.conflicts and policy in {"cancel", "ask"}:
            raise ValueError("The patch contains existing values and requires an overwrite or skip policy.")
        applied, skipped = [], []
        for assignment in plan.assignments:
            conflict = assignment.target.key in plan.conflicts
            if conflict and policy in {"skip", "blanks_only"}:
                skipped.append(assignment.target.key)
                continue
            self._set_coordinate_value(state, assignment.target, assignment.value)
            provenance = plan.extracted_grid.provenance.to_dict()
            provenance.update({
                "source_text": assignment.value,
                "source_cell_bbox": list(assignment.source_box),
                "extraction_strategy": plan.extracted_grid.strategy,
                "mapping_orientation": plan.orientation,
                "captured_at": utc_now_iso(),
            })
            state.cell_provenance[assignment.target.key] = provenance
            # Retain legacy data-cell lookup compatibility for older consumers.
            if assignment.target.kind == "data":
                state.cell_provenance[f"{assignment.target.row}:{assignment.target.column}"] = provenance
            applied.append(assignment.target.key)
        if applied:
            state.version += 1
            state.dirty = True
            state.updated_at = utc_now_iso()
            self._record_history(state, "apply_grid_patch", {
                "orientation": plan.orientation, "applied": applied, "skipped": skipped,
            })
        return GridPatchResult(state.dataset_id, state.version, applied, skipped)

    def _set_coordinate_value(self, state: DataGridState, coordinate: GridCoordinate, value: Any) -> None:
        text = "" if value is None else str(value)
        if coordinate.kind == "row_header":
            row = int(coordinate.row or 0)
            self._ensure_shape(state, row + 1, len(state.headers))
            state.row_headers[row] = text
        elif coordinate.kind == "column_header":
            column = int(coordinate.column or 0)
            self._ensure_shape(state, len(state.rows), column + 1)
            state.headers[column] = text
        else:
            row, column = int(coordinate.row or 0), int(coordinate.column or 0)
            self._ensure_shape(state, row + 1, column + 1)
            state.rows[row][column] = text

    def sort_rows(self, dataset_id: str, column: int, reverse: bool = False) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state or not 0 <= column < len(state.headers):
            return state
        titles = list(state.row_headers) + [str(index + 1) for index in range(len(state.row_headers), len(state.rows))]
        pairs = list(enumerate(zip(titles, state.rows)))
        def key(pair):
            row = pair[1][1]
            value = row[column] if column < len(row) else ""
            number = self.coerce_number(value)
            return (value == "", 0 if number is not None else 1, number if number is not None else str(value).casefold())
        pairs.sort(key=key, reverse=reverse)
        state.row_headers = [pair[1][0] for pair in pairs]
        state.rows = [pair[1][1] for pair in pairs]
        self._remap_row_provenance(state, [pair[0] for pair in pairs])
        self._mark_transform(state, "sort_rows", {"column": column, "reverse": reverse})
        return state

    def remove_duplicate_rows(self, dataset_id: str, columns: Optional[Iterable[int]] = None) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        selected = sorted(set(columns or range(len(state.headers))))
        seen, rows, titles, old_indexes = set(), [], [], []
        source_titles = list(state.row_headers) + [str(index + 1) for index in range(len(state.row_headers), len(state.rows))]
        for old_index, (title, row) in enumerate(zip(source_titles, state.rows)):
            key = tuple(row[column] if column < len(row) else "" for column in selected)
            if key in seen:
                continue
            seen.add(key); rows.append(row); titles.append(title); old_indexes.append(old_index)
        state.rows, state.row_headers = rows, titles
        self._remap_row_provenance(state, old_indexes)
        self._mark_transform(state, "remove_duplicate_rows", {"columns": selected})
        return state

    def find_data_issues(self, dataset_id: str, columns: Optional[Iterable[int]] = None) -> Dict[str, Any]:
        state = self._open.get(dataset_id)
        if not state:
            return {"missing": [], "duplicates": [], "type_errors": []}
        selected = sorted(set(columns or range(len(state.headers))))
        missing = [[row, column] for row, values in enumerate(state.rows) for column in selected
                   if column >= len(values) or not str(values[column]).strip()]
        keys, duplicates = {}, []
        for row, values in enumerate(state.rows):
            key = tuple(values[column] if column < len(values) else "" for column in selected)
            if key in keys: duplicates.append([keys[key], row])
            else: keys[key] = row
        type_errors = []
        for column in selected:
            declared = state.column_types.get(state.headers[column], "") if column < len(state.headers) else ""
            if str(declared).lower() in {"number", "numeric", "float", "integer"}:
                type_errors.extend([[row, column] for row, values in enumerate(state.rows)
                                    if column < len(values) and str(values[column]).strip() and self.coerce_number(values[column]) is None])
        return {"missing": missing, "duplicates": duplicates, "type_errors": type_errors}

    def fill_missing_values(
        self, dataset_id: str, columns: Iterable[int], method: str = "previous", constant: str = ""
    ) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        selected = sorted(set(columns))
        for column in selected:
            numeric = [self.coerce_number(row[column]) for row in state.rows if column < len(row)]
            numeric = [value for value in numeric if value is not None]
            statistic = ""
            if numeric and method in {"mean", "average"}: statistic = self.format_number(sum(numeric) / len(numeric))
            if numeric and method == "median":
                ordered = sorted(numeric); statistic = self.format_number(ordered[len(ordered) // 2])
            for row_index, row in enumerate(state.rows):
                self._ensure_shape(state, len(state.rows), column + 1)
                if str(row[column]).strip(): continue
                if method == "constant": row[column] = str(constant)
                elif method in {"mean", "average", "median"}: row[column] = statistic
                elif method == "previous" and row_index: row[column] = state.rows[row_index - 1][column]
                elif method == "next":
                    row[column] = next((str(state.rows[index][column]) for index in range(row_index + 1, len(state.rows))
                                        if column < len(state.rows[index]) and str(state.rows[index][column]).strip()), "")
        self._mark_transform(state, "fill_missing_values", {"columns": selected, "method": method})
        return state

    def append_dataset(self, dataset_id: str, other_dataset_id: str) -> Optional[DataGridState]:
        state = self._open.get(dataset_id) or self.load_dataset(dataset_id)
        other = self._open.get(other_dataset_id) or self.load_dataset(other_dataset_id)
        if not state or not other:
            return None
        headers = list(dict.fromkeys(state.headers + other.headers))
        def expanded(source, row): return [row[source.headers.index(header)] if header in source.headers and source.headers.index(header) < len(row) else "" for header in headers]
        state.rows = [expanded(state, row) for row in state.rows] + [expanded(other, row) for row in other.rows]
        other_titles = list(other.row_headers) + [str(index + 1) for index in range(len(other.row_headers), len(other.rows))]
        state.row_headers.extend(other_titles[:len(other.rows)])
        state.headers = headers
        self._mark_transform(state, "append_dataset", {"other_dataset_id": other_dataset_id})
        return state

    def join_dataset(
        self, dataset_id: str, other_dataset_id: str, left_key: str, right_key: Optional[str] = None
    ) -> Optional[DataGridState]:
        state = self._open.get(dataset_id) or self.load_dataset(dataset_id)
        other = self._open.get(other_dataset_id) or self.load_dataset(other_dataset_id)
        right_key = right_key or left_key
        if not state or not other or left_key not in state.headers or right_key not in other.headers:
            return None
        left_index, right_index = state.headers.index(left_key), other.headers.index(right_key)
        added = [header for index, header in enumerate(other.headers) if index != right_index]
        unique_added = []
        for header in added:
            candidate, suffix = header, 2
            while candidate in state.headers or candidate in unique_added:
                candidate = f"{header} ({suffix})"; suffix += 1
            unique_added.append(candidate)
        lookup = {}
        for row in other.rows:
            key = row[right_index] if right_index < len(row) else ""
            lookup.setdefault(str(key), row)
        for row in state.rows:
            match = lookup.get(str(row[left_index] if left_index < len(row) else ""))
            row.extend([match[index] if match and index < len(match) else "" for index in range(len(other.headers)) if index != right_index])
        state.headers.extend(unique_added)
        self._mark_transform(state, "join_dataset", {"other_dataset_id": other_dataset_id, "left_key": left_key, "right_key": right_key})
        return state

    def pivot(self, dataset_id: str, row_field: str, column_field: str, value_field: str) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state or any(field not in state.headers for field in (row_field, column_field, value_field)):
            return None
        row_index, column_index, value_index = (state.headers.index(field) for field in (row_field, column_field, value_field))
        row_keys, column_keys, values = [], [], {}
        for row in state.rows:
            row_key = row[row_index] if row_index < len(row) else ""
            column_key = row[column_index] if column_index < len(row) else ""
            value = row[value_index] if value_index < len(row) else ""
            if row_key not in row_keys: row_keys.append(row_key)
            if column_key not in column_keys: column_keys.append(column_key)
            values.setdefault((row_key, column_key), value)
        state.headers = [row_field] + [str(key) for key in column_keys]
        state.rows = [[str(row_key)] + [values.get((row_key, column_key), "") for column_key in column_keys] for row_key in row_keys]
        state.row_headers = [str(index + 1) for index in range(len(state.rows))]
        state.cell_provenance = {}
        self._mark_transform(state, "pivot", {"row_field": row_field, "column_field": column_field, "value_field": value_field})
        return state

    def unpivot(self, dataset_id: str, value_columns: Iterable[int], variable_name="Variable", value_name="Value") -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state: return None
        values = sorted(set(value_columns)); ids = [index for index in range(len(state.headers)) if index not in values]
        rows, row_headers = [], []
        for row_index, row in enumerate(state.rows):
            for column in values:
                rows.append([row[index] if index < len(row) else "" for index in ids] + [state.headers[column], row[column] if column < len(row) else ""])
                row_headers.append(state.row_headers[row_index] if row_index < len(state.row_headers) else str(row_index + 1))
        state.headers = [state.headers[index] for index in ids] + [variable_name, value_name]
        state.rows, state.row_headers = rows, row_headers
        state.cell_provenance = {}
        self._mark_transform(state, "unpivot", {"value_columns": values})
        return state

    def export_selection(self, dataset_id: str, selection: GridSelection, file_format: str = "tsv") -> str:
        state = self._open.get(dataset_id) or self.load_dataset(dataset_id)
        if not state: return ""
        items = [{"coordinate": coordinate.to_dict(), "value": self.grid_mapping_service.value_at(state, coordinate),
                  "provenance": state.cell_provenance.get(coordinate.key, {})} for coordinate in selection.ordered()]
        if file_format.lower() == "json": return json.dumps(items, ensure_ascii=False, indent=2)
        output = io.StringIO(); delimiter = "," if file_format.lower() == "csv" else "\t"
        writer = csv.writer(output, delimiter=delimiter); writer.writerow(["kind", "row", "column", "value"])
        for item in items:
            coordinate = item["coordinate"]; writer.writerow([coordinate["kind"], coordinate.get("row"), coordinate.get("column"), item["value"]])
        return output.getvalue()

    def _mark_transform(self, state: DataGridState, action: str, details: Dict[str, Any]) -> None:
        state.version += 1; state.dirty = True; state.updated_at = utc_now_iso()
        self._record_history(state, action, details)

    @staticmethod
    def _remap_row_provenance(state: DataGridState, old_indexes: List[int]) -> None:
        source, remapped = dict(state.cell_provenance), {}
        for key, value in source.items():
            if key.startswith("column_header:"):
                remapped[key] = value
        for new_row, old_row in enumerate(old_indexes):
            row_key = f"row_header:{old_row}"
            if row_key in source: remapped[f"row_header:{new_row}"] = source[row_key]
            for column in range(len(state.headers)):
                modern, legacy = f"data:{old_row}:{column}", f"{old_row}:{column}"
                provenance = source.get(modern) or source.get(legacy)
                if provenance:
                    remapped[f"data:{new_row}:{column}"] = provenance
                    remapped[f"{new_row}:{column}"] = provenance
        state.cell_provenance = remapped

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
        updated = self.update_grid(dataset_id, headers, cleaned_rows, column_types, row_headers=row_headers)
        if updated:
            self._record_history(updated, "drop_empty", {})
        return updated

    def selection_summary(self, values: Iterable[Any]) -> Dict[str, Any]:
        nums: List[float] = []
        total_values = 0
        for value in values:
            total_values += 1
            parsed = self.coerce_number(value)
            if parsed is not None:
                nums.append(parsed)
        total = sum(nums)
        return {
            "count": total_values,
            "numeric_count": len(nums),
            "sum": total,
            "average": total / len(nums) if nums else 0,
            "min": min(nums) if nums else None,
            "max": max(nums) if nums else None,
        }

    def coerce_number(self, value: Any) -> Optional[float]:
        return coerce_number(value)

    def trim_whitespace(self, dataset_id: str) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        rows = [[str(cell).strip() for cell in row] for row in state.rows]
        headers = [str(header).strip() or f"Column {idx + 1}" for idx, header in enumerate(state.headers)]
        row_headers = [str(header).strip() or str(idx + 1) for idx, header in enumerate(state.row_headers)]
        updated = self.update_grid(dataset_id, headers, rows, state.column_types, row_headers=row_headers)
        if updated:
            self._record_history(updated, "trim_whitespace", {})
        return updated

    def normalize_numbers(self, dataset_id: str, columns: Optional[List[int]] = None) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        targets = set(columns if columns is not None else range(len(state.headers)))
        rows = []
        for row in state.rows:
            next_row = list(row)
            for col in targets:
                if col < len(next_row):
                    number = self.coerce_number(next_row[col])
                    if number is not None:
                        next_row[col] = ("%g" % number)
            rows.append(next_row)
        updated = self.update_grid(dataset_id, state.headers, rows, state.column_types, row_headers=state.row_headers)
        if updated:
            self._record_history(updated, "normalize_numbers", {"columns": sorted(targets)})
        return updated

    def promote_row_to_headers(self, dataset_id: str, row_index: int, remove_row: bool = True) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state or row_index < 0 or row_index >= len(state.rows):
            return None
        width = max(len(state.headers), len(state.rows[row_index]))
        header_row = state.rows[row_index] + [""] * (width - len(state.rows[row_index]))
        headers = [str(value).strip() or f"Column {idx + 1}" for idx, value in enumerate(header_row)]
        rows = [list(row) for idx, row in enumerate(state.rows) if not remove_row or idx != row_index]
        row_headers = [h for idx, h in enumerate(state.row_headers) if not remove_row or idx != row_index]
        updated = self.update_grid(dataset_id, headers, rows, {}, row_headers=row_headers)
        if updated:
            self._record_history(updated, "promote_row_to_headers", {"row": row_index})
        return updated

    def promote_column_to_row_headers(self, dataset_id: str, column_index: int, remove_column: bool = True) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state or column_index < 0:
            return None
        row_headers = []
        rows = []
        for idx, row in enumerate(state.rows):
            padded = row + [""] * (len(state.headers) - len(row))
            row_headers.append(str(padded[column_index]).strip() or str(idx + 1) if column_index < len(padded) else str(idx + 1))
            rows.append([cell for col, cell in enumerate(padded) if not remove_column or col != column_index])
        headers = [h for col, h in enumerate(state.headers) if not remove_column or col != column_index]
        updated = self.update_grid(dataset_id, headers, rows, {}, row_headers=row_headers)
        if updated:
            self._record_history(updated, "promote_column_to_row_headers", {"column": column_index})
        return updated

    def fill_down(self, dataset_id: str, columns: Optional[List[int]] = None) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        targets = set(columns if columns is not None else range(len(state.headers)))
        rows = [list(row) + [""] * (len(state.headers) - len(row)) for row in state.rows]
        for col in targets:
            last = ""
            for row in rows:
                if col >= len(row):
                    continue
                if str(row[col]).strip():
                    last = row[col]
                elif last:
                    row[col] = last
        updated = self.update_grid(dataset_id, state.headers, rows, state.column_types, row_headers=state.row_headers)
        if updated:
            self._record_history(updated, "fill_down", {"columns": sorted(targets)})
        return updated

    def replace_text(self, dataset_id: str, find_text: str, replace_with: str, columns: Optional[List[int]] = None) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state or find_text == "":
            return state
        targets = set(columns if columns is not None else range(len(state.headers)))
        rows = []
        for row in state.rows:
            next_row = list(row)
            for col in targets:
                if col < len(next_row):
                    next_row[col] = str(next_row[col]).replace(find_text, replace_with)
            rows.append(next_row)
        updated = self.update_grid(dataset_id, state.headers, rows, state.column_types, row_headers=state.row_headers)
        if updated:
            self._record_history(updated, "replace_text", {"find": find_text, "columns": sorted(targets)})
        return updated

    def split_column(self, dataset_id: str, column_index: int, delimiter: str) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state or column_index < 0 or column_index >= len(state.headers) or delimiter == "":
            return state
        split_rows = []
        max_parts = 1
        for row in state.rows:
            parts = str(row[column_index] if column_index < len(row) else "").split(delimiter)
            max_parts = max(max_parts, len(parts))
            split_rows.append(parts)
        headers = list(state.headers)
        original = headers[column_index]
        headers[column_index:column_index + 1] = [original if idx == 0 else f"{original} {idx + 1}" for idx in range(max_parts)]
        rows = []
        for row, parts in zip(state.rows, split_rows):
            padded = row + [""] * (len(state.headers) - len(row))
            rows.append(padded[:column_index] + parts + [""] * (max_parts - len(parts)) + padded[column_index + 1:])
        updated = self.update_grid(dataset_id, headers, rows, {}, row_headers=state.row_headers)
        if updated:
            self._record_history(updated, "split_column", {"column": column_index, "delimiter": delimiter})
        return updated

    def merge_columns(self, dataset_id: str, columns: List[int], delimiter: str = " ") -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        columns = sorted(set(columns or []))
        if not state or len(columns) < 2:
            return state
        first = columns[0]
        headers = [h for idx, h in enumerate(state.headers) if idx not in columns[1:]]
        headers[first] = " ".join(state.headers[idx] for idx in columns if idx < len(state.headers)).strip() or headers[first]
        rows = []
        for row in state.rows:
            padded = row + [""] * (len(state.headers) - len(row))
            merged = delimiter.join(str(padded[idx]) for idx in columns if idx < len(padded) and str(padded[idx]).strip())
            next_row = [cell for idx, cell in enumerate(padded) if idx not in columns[1:]]
            next_row[first] = merged
            rows.append(next_row)
        updated = self.update_grid(dataset_id, headers, rows, {}, row_headers=state.row_headers)
        if updated:
            self._record_history(updated, "merge_columns", {"columns": columns})
        return updated

    def infer_column_types(self, dataset_id: str) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        types = {}
        for idx, header in enumerate(state.headers):
            values = [row[idx] for row in state.rows if idx < len(row) and str(row[idx]).strip()]
            if values and sum(1 for value in values if self.coerce_number(value) is not None) >= max(1, len(values) * 0.8):
                types[header] = "Number"
            elif values and sum(1 for value in values if self._is_dateish(value)) >= max(1, len(values) * 0.8):
                types[header] = "Date"
            else:
                types[header] = "Text"
        state.column_types = types
        state.version += 1
        state.dirty = True
        state.updated_at = utc_now_iso()
        self._record_history(state, "infer_column_types", {"types": types})
        return state

    def add_metric(
        self,
        dataset_id: str,
        axis: str,
        metric: str,
        indexes: Optional[List[int]] = None,
        label: Optional[str] = None,
    ) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        metric_key = (metric or "average").lower().strip()
        if axis == "column":
            target_cols = indexes or list(range(len(state.headers)))
            values = []
            for row in state.rows:
                nums = [
                    self.coerce_number(row[col] if col < len(row) else "")
                    for col in target_cols
                ]
                values.append(self._metric_value([num for num in nums if num is not None], metric_key))
            state.headers.append(label or metric_key.title())
            for row_idx, row in enumerate(state.rows):
                row.append(values[row_idx] if row_idx < len(values) else "")
        else:
            target_rows = indexes or list(range(len(state.rows)))
            row_values = []
            for col in range(len(state.headers)):
                nums = [
                    self.coerce_number(state.rows[row][col] if row < len(state.rows) and col < len(state.rows[row]) else "")
                    for row in target_rows
                ]
                row_values.append(self._metric_value([num for num in nums if num is not None], metric_key))
            state.rows.append(row_values)
            state.row_headers.append(label or metric_key.title())
        state.version += 1
        state.dirty = True
        state.updated_at = utc_now_iso()
        self._record_history(state, "add_metric", {"axis": axis, "metric": metric_key, "indexes": indexes or []})
        return state

    def add_metric_for_selection(
        self,
        dataset_id: str,
        axis: str,
        metric: str,
        rows: Optional[List[int]] = None,
        columns: Optional[List[int]] = None,
        label: Optional[str] = None,
    ) -> Optional[DataGridState]:
        state = self._open.get(dataset_id)
        if not state:
            return None
        metric_key = (metric or "average").lower().strip()
        selected_rows = sorted({row for row in (rows or []) if 0 <= row < len(state.rows)})
        selected_cols = sorted({col for col in (columns or []) if 0 <= col < len(state.headers)})
        if not selected_rows:
            selected_rows = list(range(len(state.rows)))
        if not selected_cols:
            selected_cols = list(range(len(state.headers)))

        if axis == "column":
            insert_at = min(len(state.headers), max(selected_cols) + 1 if selected_cols else len(state.headers))
            values = []
            for row_idx, row in enumerate(state.rows):
                if row_idx not in selected_rows:
                    values.append("")
                    continue
                nums = [
                    self.coerce_number(row[col] if col < len(row) else "")
                    for col in selected_cols
                ]
                values.append(self._metric_value([num for num in nums if num is not None], metric_key))
            state.headers.insert(insert_at, label or metric_key.title())
            for row_idx, row in enumerate(state.rows):
                row.insert(insert_at, values[row_idx] if row_idx < len(values) else "")
        else:
            insert_at = min(len(state.rows), max(selected_rows) + 1 if selected_rows else len(state.rows))
            row_values = []
            for col_idx in range(len(state.headers)):
                if col_idx not in selected_cols:
                    row_values.append("")
                    continue
                nums = [
                    self.coerce_number(state.rows[row][col_idx] if row < len(state.rows) and col_idx < len(state.rows[row]) else "")
                    for row in selected_rows
                ]
                row_values.append(self._metric_value([num for num in nums if num is not None], metric_key))
            state.rows.insert(insert_at, row_values)
            state.row_headers.insert(insert_at, label or metric_key.title())

        state.version += 1
        state.dirty = True
        state.updated_at = utc_now_iso()
        self._record_history(
            state,
            "add_metric_for_selection",
            {"axis": axis, "metric": metric_key, "rows": selected_rows, "columns": selected_cols},
        )
        return state

    def _metric_value(self, nums: List[float], metric: str) -> str:
        if metric in {"count", "numeric_count"}:
            return str(len(nums))
        if not nums:
            return ""
        if metric == "sum":
            value = sum(nums)
        elif metric in {"avg", "average", "mean"}:
            value = sum(nums) / len(nums)
        elif metric == "min":
            value = min(nums)
        elif metric == "max":
            value = max(nums)
        else:
            value = sum(nums) / len(nums)
        return self.format_number(value)

    def format_number(self, value: Any, precision: int = 6) -> str:
        try:
            number = float(value)
        except Exception:
            return str(value)
        text = f"{number:,.{precision}f}".rstrip("0").rstrip(".")
        return "0" if text == "-0" else text

    def selected_values(self, dataset_id: str, cells: Iterable[Tuple[int, int]]) -> List[Any]:
        state = self._open.get(dataset_id)
        if not state:
            return []
        values = []
        for row, col in cells:
            if 0 <= row < len(state.rows) and 0 <= col < len(state.headers):
                values.append(state.rows[row][col] if col < len(state.rows[row]) else "")
        return values

    def chart_config_for_selection(
        self,
        dataset_id: str,
        cells: Iterable[Tuple[int, int]],
        chart_type: str = "bar",
    ) -> Optional[ChartConfig]:
        state = self._open.get(dataset_id) or self.load_dataset(dataset_id)
        if not state:
            return None
        selected = sorted(set(cells or []))
        columns = sorted({col for _, col in selected})
        if not columns:
            columns = list(range(min(2, len(state.headers))))
        x_col = columns[0] if columns else 0
        y_col = columns[1] if len(columns) > 1 else columns[0] if columns else 0
        return ChartConfig(
            chart_id=f"chart_{uuid.uuid4()}",
            name=f"{state.name} Chart",
            title=f"{state.name} Chart",
            chart_type=chart_type,
            dataset_id=state.dataset_id if state.is_persisted else None,
            x_field=state.headers[x_col] if x_col < len(state.headers) else "",
            y_field=state.headers[y_col] if y_col < len(state.headers) else "",
            x_title=state.headers[x_col] if x_col < len(state.headers) else "",
            y_title=state.headers[y_col] if y_col < len(state.headers) else "",
            source_selection={"cells": [[row, col] for row, col in selected]},
        )

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
            title=f"{state.name} Chart",
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
        states: List[DataGridState] = []

        def add_candidate(state: Optional[DataGridState]) -> None:
            if state is None:
                return
            bbox = (state.provenance.bounding_box_coordinates or [[]])[0]
            for existing in states:
                existing_bbox = (existing.provenance.bounding_box_coordinates or [[]])[0]
                if self._bbox_iou(bbox, existing_bbox) >= 0.75:
                    existing_cells = len(existing.headers) * len(existing.rows)
                    new_cells = len(state.headers) * len(state.rows)
                    if new_cells > existing_cells:
                        states[states.index(existing)] = state
                    return
            states.append(state)

        # Step 1: Use AI layout model with semantic naming
        layout_data = self._find_table_bboxes_with_layout(page)
        if layout_data:
            for table_idx, (clip_rect, semantic_name) in enumerate(layout_data):
                state = self._extract_table_from_region(
                    page, page_index, pdf_path, clip_rect, table_idx, semantic_name, lax=False
                )
                add_candidate(state)
        # Step 2: Full-page PyMuPDF structural table detection.
        # Try the default (line-based) strategy first, then fall back to text-alignment strategy
        # for borderless tables.
        for kwargs in ({}, {"vertical_strategy": "text", "horizontal_strategy": "text"}):
            try:
                finder = page.find_tables(**kwargs)
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
                if kwargs.get("vertical_strategy") == "text" and not self._looks_like_data_table(matrix):
                    continue
                add_candidate(self._state_from_matrix(
                    matrix,
                    self._table_name(matrix, page_index, len(states)),
                    page_index, pdf_path, getattr(table, "bbox", None),
                ))
        if states:
            return states

        # Step 3: Whole-page word-based fallback.
        # Uses structural consistency check only — works for text tables, not just numeric ones.
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
                add_candidate(state)
        return states

    @staticmethod
    def _bbox_iou(first: Any, second: Any) -> float:
        try:
            ax0, ay0, ax1, ay1 = [float(value) for value in first]
            bx0, by0, bx1, by1 = [float(value) for value in second]
        except Exception:
            return 0.0
        intersection = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
        area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
        area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
        union = area_a + area_b - intersection
        return intersection / union if union else 0.0

    def _extract_table_from_region(
        self, page: Any, page_index: int, pdf_path: str,
        clip_rect: Any, table_idx: int, semantic_name: Optional[str] = None, lax: bool = False
    ) -> Optional[DataGridState]:
        try:
            import fitz
            clip = fitz.Rect(clip_rect) if not hasattr(clip_rect, "x0") else clip_rect
        except Exception:
            return None

        # Try multiple structural strategies. "lines" works best for grid tables,
        # "text" works best for borderless financial/scientific tables.
        extraction_strategies = [
            {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
            {"vertical_strategy": "text", "horizontal_strategy": "text", "intersection_tol": 15}
        ]

        best_matrix = []
        for kwargs in extraction_strategies:
            try:
                finder = page.find_tables(clip=clip, **kwargs)
                tables = list(getattr(finder, "tables", []) or [])
                for table in tables:
                    matrix = table.extract()
                    # Only keep the matrix if it has better data density than previous attempts
                    if self._matrix_quality_ok(matrix) and len(matrix) > len(best_matrix):
                        best_matrix = matrix
            except Exception:
                continue

        if best_matrix:
            # Use semantic name if found, otherwise fallback to row generation
            final_name = semantic_name or self._table_name(best_matrix, page_index, table_idx)
            return self._state_from_matrix(
                best_matrix, final_name, page_index, pdf_path, list(clip)
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
        if not matrix or not self._matrix_quality_ok(matrix):
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

    def _find_table_bboxes_with_layout(self, page: Any) -> List[Tuple[Any, str]]:
        """
        Run the layout model to identify table regions and their contextual titles.
        Returns a list of tuples: (fitz.Rect, table_name).
        """
        model = self._get_layout_model()
        if model is None:
            return []

        try:
            import fitz
            results = model.predict(page)

            tables_with_context = []
            last_text_block = None

            for r in results:
                bbox = fitz.Rect(r[0], r[1], r[2], r[3])
                block_type = r[4]

                if block_type in ("title", "text"):
                    # Save the text from this block to potentially name the next table
                    text_content = page.get_text("text", clip=bbox).strip()
                    # Only keep short text blocks (likely titles/captions)
                    if text_content and len(text_content) < 100:
                        last_text_block = text_content.replace("\n", " ")

                elif block_type == "table":
                    # Assign the preceding title, or fallback to a default
                    table_name = last_text_block if last_text_block else None
                    tables_with_context.append((bbox, table_name))
                    last_text_block = None # Reset for the next table

            return tables_with_context
        except Exception:
            return []

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
        rows = self._merge_wrapped_cells(rows)
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
        if not lax:
            # Primary gate: structural consistency (rejects paragraphs, works for text AND numeric tables)
            if not self._has_repeated_column_structure(row_cells, matrix):
                return []
            # Optional strict gate: also require numeric/date data (only when caller asks for it)
            if require_table_like and not self._looks_like_data_table(matrix):
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

    def _merge_wrapped_cells(self, matrix: List[List[str]]) -> List[List[str]]:
        """Absorb rows that are single-column continuations of the previous row's cell.

        When a PDF cell contains wrapped text, word clustering treats each visual
        line as a separate row.  Those rows have content in exactly one column
        while the surrounding rows have multiple filled columns — a reliable signal
        that the text belongs in the cell above.
        """
        if len(matrix) < 2:
            return matrix
        width = max((len(row) for row in matrix), default=0)
        padded = [row + [""] * (width - len(row)) for row in matrix]
        merged: List[List[str]] = [list(padded[0])]
        for row in padded[1:]:
            filled = [i for i, c in enumerate(row) if c.strip()]
            prev = merged[-1]
            prev_filled = [i for i, c in enumerate(prev) if c.strip()]
            if len(filled) == 1 and len(prev_filled) > 1 and filled[0] in prev_filled:
                col = filled[0]
                new_row = list(prev)
                new_row[col] = (prev[col] + " " + row[col]).strip()
                merged[-1] = new_row
            else:
                merged.append(list(row))
        return merged

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
        return coerce_number(value) is not None

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

    def _ensure_shape(self, state: DataGridState, rows: int, columns: int) -> None:
        if len(state.headers) < columns:
            state.headers.extend(f"Column {idx + 1}" for idx in range(len(state.headers), columns))
        if len(state.row_headers) < rows:
            state.row_headers.extend(str(idx + 1) for idx in range(len(state.row_headers), rows))
        while len(state.rows) < rows:
            state.rows.append(["" for _ in range(len(state.headers))])
        for row in state.rows:
            if len(row) < len(state.headers):
                row.extend("" for _ in range(len(state.headers) - len(row)))

    def _record_history(self, state: DataGridState, action: str, details: Dict[str, Any]) -> None:
        history = list(state.metadata.get("cleaning_history") or [])
        history.append({"action": action, "details": dict(details or {}), "at": utc_now_iso()})
        state.metadata["cleaning_history"] = history[-100:]

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
