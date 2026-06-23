from __future__ import annotations

from typing import Any

from core.models.data_dock_models import ChartConfig, DataGridState
from core.utils.numeric_utils import coerce_number


class ChartDataAdapter:
    """Normalize Data Dock rows into a renderer-neutral chart data contract."""

    def adapt(self, state: DataGridState, config: ChartConfig) -> dict[str, Any]:
        headers = list(state.headers)
        selected = {tuple(cell) for cell in (config.source_selection or {}).get("cells", []) if len(cell) == 2}
        row_indexes = sorted({row for row, _ in selected}) or list(range(len(state.rows)))
        x_field = (config.encodings or {}).get("x") or config.x_field
        x_index = headers.index(x_field) if x_field in headers else 0
        specs = list(config.series or [])
        if not specs and config.y_field:
            specs = [{"name": config.y_field, "y_field": config.y_field}]
        categories, series = [], []
        for row_index in row_indexes:
            row = state.rows[row_index]
            if x_field == "__row_header__":
                label = state.row_headers[row_index] if row_index < len(state.row_headers) else str(row_index + 1)
            else:
                label = str(row[x_index]) if x_index < len(row) else ""
            categories.append(label)
        for spec in specs:
            field = spec.get("y_field") or spec.get("field") or spec.get("name")
            if field not in headers:
                continue
            index = headers.index(field)
            values = [coerce_number(state.rows[row][index] if index < len(state.rows[row]) else "") for row in row_indexes]
            series.append({"name": spec.get("name") or field, "field": field, "values": values})
        if x_field == "__row_header__":
            x_values = [coerce_number(state.row_headers[row] if row < len(state.row_headers) else "") for row in row_indexes]
        else:
            x_values = [coerce_number(state.rows[row][x_index] if x_index < len(state.rows[row]) else "") for row in row_indexes]
        return {"categories": categories, "series": series, "x_values": x_values, "row_indexes": row_indexes}
