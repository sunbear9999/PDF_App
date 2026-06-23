from __future__ import annotations

from typing import Iterable

from core.models.data_dock_models import (
    DataGridState,
    ExtractedGrid,
    GridCoordinate,
    GridPatchAssignment,
    GridPatchPlan,
    GridSelection,
)


class GridMappingError(ValueError):
    pass


class GridMappingService:
    """Pure deterministic mapping from an extracted matrix to logical grid cells."""

    def plan(
        self, state: DataGridState, selection: GridSelection, extracted: ExtractedGrid,
        preferred_orientation: str | None = None,
    ) -> GridPatchPlan:
        if selection.dataset_id != state.dataset_id:
            raise GridMappingError("The selection belongs to a different dataset.")
        if selection.dataset_version != state.version:
            raise GridMappingError("The dataset changed after the PDF fill began; select the cells again.")
        targets = selection.ordered()
        if not targets:
            raise GridMappingError("Select at least one destination cell or title.")
        source = self._rectangular(extracted.cells)
        if not source:
            raise GridMappingError("The selected PDF region contains no recoverable text grid.")

        target_rows, target_cols = selection.shape()
        source_rows, source_cols = len(source), len(source[0])
        rectangular = selection.is_rectangular()
        orientations: list[tuple[str, list[tuple[int, int, str]]]] = []
        if rectangular and (source_rows, source_cols) == (target_rows, target_cols):
            orientations.append(("direct", self._flatten(source)))
        transposed = self._transpose(source)
        if rectangular and (source_cols, source_rows) == (target_rows, target_cols) and transposed != source:
            orientations.append(("transposed", self._flatten(transposed)))
        if not rectangular and (source_rows, source_cols) == (target_rows, target_cols):
            logical = [item.sort_key for item in targets]
            minimum_row = min(row for row, _ in logical)
            minimum_column = min(column for _, column in logical)
            wanted = {(row - minimum_row, column - minimum_column) for row, column in logical}
            omitted = [source[row][column] for row in range(source_rows) for column in range(source_cols)
                       if (row, column) not in wanted]
            if all(not str(value).strip() for value in omitted):
                values = [(row, column, source[row][column]) for row, column in sorted(wanted)]
                orientations.append(("masked_direct", values))
        if len(targets) == source_rows * source_cols and not orientations:
            orientations.append(("row_major", self._flatten(source)))
        if not orientations and len(targets) != source_rows * source_cols:
            raise GridMappingError(
                f"PDF selection produced {source_rows}×{source_cols} values, but the destination requires "
                f"{target_rows}×{target_cols} ({len(targets)} selected cells)."
            )

        orientation, values = next(
            (candidate for candidate in orientations if candidate[0] == preferred_orientation), orientations[0]
        )
        assignments = []
        conflicts = []
        for target, (source_row, source_col, value) in zip(targets, values):
            existing = self.value_at(state, target)
            box = self._source_box(extracted, source_row, source_col)
            assignment = GridPatchAssignment(target, value, source_row, source_col, box, existing)
            assignments.append(assignment)
            if existing.strip() and existing != value:
                conflicts.append(target.key)
        diagnostics = []
        if len(orientations) > 1:
            diagnostics.append("More than one orientation fits the selected shape.")
        if not rectangular:
            diagnostics.append("Irregular selections are filled in visible row-major order.")
        diagnostics.extend(extracted.warnings)
        return GridPatchPlan(
            dataset_id=state.dataset_id,
            expected_version=state.version,
            selection=selection,
            extracted_grid=extracted,
            assignments=assignments,
            orientation=orientation,
            conflicts=conflicts,
            diagnostics=diagnostics,
            requires_preview=bool(conflicts or diagnostics or len(orientations) > 1),
        )

    @staticmethod
    def value_at(state: DataGridState, coordinate: GridCoordinate) -> str:
        if coordinate.kind == "row_header":
            row = int(coordinate.row or 0)
            return str(state.row_headers[row]) if 0 <= row < len(state.row_headers) else ""
        if coordinate.kind == "column_header":
            column = int(coordinate.column or 0)
            return str(state.headers[column]) if 0 <= column < len(state.headers) else ""
        row, column = int(coordinate.row or 0), int(coordinate.column or 0)
        return str(state.rows[row][column]) if 0 <= row < len(state.rows) and 0 <= column < len(state.rows[row]) else ""

    @staticmethod
    def _rectangular(rows: Iterable[Iterable[object]]) -> list[list[str]]:
        matrix = [[str(value or "").strip() for value in row] for row in rows]
        if not matrix:
            return []
        width = max((len(row) for row in matrix), default=0)
        return [row + [""] * (width - len(row)) for row in matrix] if width else []

    @staticmethod
    def _flatten(matrix: list[list[str]]) -> list[tuple[int, int, str]]:
        return [(row, column, value) for row, values in enumerate(matrix) for column, value in enumerate(values)]

    @staticmethod
    def _transpose(matrix: list[list[str]]) -> list[list[str]]:
        return [list(row) for row in zip(*matrix)]

    @staticmethod
    def _source_box(extracted: ExtractedGrid, row: int, column: int) -> list[float]:
        try:
            return list(extracted.cell_boxes[row][column])
        except (IndexError, TypeError):
            return []
