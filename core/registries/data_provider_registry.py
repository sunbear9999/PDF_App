from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol

from core.models.data_dock_models import DataGridState, DataProvenance


class TableParserProvider(Protocol):
    provider_id: str

    def parse_selection(self, text: str, provenance: DataProvenance) -> Optional[DataGridState]:
        ...


class VisionEstimatorProvider(Protocol):
    provider_id: str

    def estimate_chart_data(self, payload: Any, provenance: DataProvenance) -> Optional[DataGridState]:
        ...


@dataclass
class GridHookProvider:
    provider_id: str
    on_cell_before_edit: Optional[Callable[..., Any]] = None
    on_cell_after_edit: Optional[Callable[..., Any]] = None
    register_grid_context_menu: Optional[Callable[..., Any]] = None
    override_formula_engine: Optional[Callable[..., Any]] = None


class DefaultTableParserProvider:
    provider_id = "core.default_table_parser"

    def parse_selection(self, text: str, provenance: DataProvenance) -> Optional[DataGridState]:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not lines and text:
            lines = [text.strip()]
        if not lines:
            return None

        rows = [self._split_line(line) for line in lines]
        width = max((len(row) for row in rows), default=0)
        if width <= 1:
            words = (text or "").split()
            rows = [words] if words else []
            width = len(words)
        if width == 0:
            return None
        rows = [row + [""] * (width - len(row)) for row in rows]
        headers = self._headers_for(rows)
        data_rows = rows[1:] if self._looks_like_header(rows[0]) and len(rows) > 1 else rows
        return DataGridState(
            dataset_id=f"data_{uuid.uuid4()}",
            name="Extracted Dataset",
            headers=headers,
            row_headers=[str(i + 1) for i in range(len(data_rows))],
            rows=data_rows,
            provenance=provenance,
        )

    def _split_line(self, line: str) -> List[str]:
        if "\t" in line:
            return [part.strip() for part in line.split("\t")]
        if "," in line:
            return [part.strip() for part in line.split(",")]
        parts = [part.strip() for part in re.split(r"\s{2,}", line) if part.strip()]
        return parts if len(parts) > 1 else line.split()

    def _looks_like_header(self, row: List[str]) -> bool:
        if not row:
            return False
        numeric = sum(1 for value in row if _is_number(value))
        return numeric < max(1, len(row) // 2)

    def _headers_for(self, rows: List[List[str]]) -> List[str]:
        if rows and self._looks_like_header(rows[0]) and len(rows) > 1:
            return [str(cell) or f"Column {idx + 1}" for idx, cell in enumerate(rows[0])]
        width = max((len(row) for row in rows), default=0)
        return [f"Column {idx + 1}" for idx in range(width)]


class StubVisionEstimatorProvider:
    provider_id = "core.stub_vision_estimator"

    def estimate_chart_data(self, payload: Any, provenance: DataProvenance) -> Optional[DataGridState]:
        return None


class DataProviderRegistry:
    def __init__(self) -> None:
        self._table_parsers: List[TableParserProvider] = [DefaultTableParserProvider()]
        self._vision_estimators: List[VisionEstimatorProvider] = [StubVisionEstimatorProvider()]
        self._grid_hooks: List[GridHookProvider] = []
        self._palettes: Dict[str, List[str]] = {
            "default": ["#2f80ed", "#27ae60", "#f2994a", "#eb5757", "#9b51e0"],
            "print": ["#111111", "#555555", "#888888", "#bbbbbb", "#dddddd"],
            "warm": ["#c2410c", "#f59e0b", "#be123c", "#7c2d12", "#f97316"],
            "cool": ["#0f766e", "#2563eb", "#0891b2", "#4f46e5", "#16a34a"],
        }

    def register_table_parser(self, provider: TableParserProvider) -> None:
        self._table_parsers.insert(0, provider)

    def register_vision_estimator(self, provider: VisionEstimatorProvider) -> None:
        self._vision_estimators.insert(0, provider)

    def register_grid_hook(self, provider: GridHookProvider) -> None:
        self._grid_hooks.append(provider)

    def register_palette(self, palette_id: str, colors: Iterable[str]) -> None:
        self._palettes[palette_id] = list(colors)

    def parse_selection(self, text: str, provenance: DataProvenance) -> Optional[DataGridState]:
        for provider in list(self._table_parsers):
            result = provider.parse_selection(text, provenance)
            if result is not None:
                return result
        return None

    def estimate_chart_data(self, payload: Any, provenance: DataProvenance) -> Optional[DataGridState]:
        for provider in list(self._vision_estimators):
            result = provider.estimate_chart_data(payload, provenance)
            if result is not None:
                return result
        return None

    def grid_hooks(self) -> List[GridHookProvider]:
        return list(self._grid_hooks)

    def palettes(self) -> Dict[str, List[str]]:
        return {key: list(value) for key, value in self._palettes.items()}

    def palette(self, palette_id: str) -> List[str]:
        return list(self._palettes.get(palette_id) or self._palettes["default"])


def _is_number(value: Any) -> bool:
    try:
        float(str(value).replace(",", "").replace("%", "").replace("$", ""))
        return True
    except Exception:
        return False
