from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol

from core.models.data_dock_models import DataGridState, DataProvenance
from core.utils.numeric_utils import is_number


class TableParserProvider(Protocol):
    provider_id: str

    def parse_selection(self, text: str, provenance: DataProvenance) -> Optional[DataGridState]:
        ...


class VisionEstimatorProvider(Protocol):
    provider_id: str

    def estimate_chart_data(self, payload: Any, provenance: DataProvenance) -> Optional[DataGridState]:
        ...


class SelectionClassifierProvider(Protocol):
    provider_id: str
    plugin_id: str

    def classify_selection(self, payload: Any, provenance: DataProvenance) -> Optional[str]:
        ...


class ChartAnalysisProvider(Protocol):
    provider_id: str
    plugin_id: str

    def build_blueprint(self, payload: Any) -> Any:
        ...


@dataclass
class GridHookProvider:
    provider_id: str
    on_cell_before_edit: Optional[Callable[..., Any]] = None
    on_cell_after_edit: Optional[Callable[..., Any]] = None
    register_grid_context_menu: Optional[Callable[..., Any]] = None
    override_formula_engine: Optional[Callable[..., Any]] = None


@dataclass
class DataCleanerSpec:
    cleaner_id: str
    label: str
    description: str = ""
    callback: Optional[Callable[[DataGridState, Dict[str, Any]], DataGridState]] = None
    plugin_id: str = ""


@dataclass
class GridActionSpec:
    action_id: str
    label: str
    description: str = ""
    callback: Optional[Callable[..., Any]] = None
    position: int = 50
    plugin_id: str = ""


@dataclass
class GridExtractorSpec:
    extractor_id: str
    label: str
    callback: Optional[Callable[[Dict[str, Any]], Any]] = None
    position: int = 50
    plugin_id: str = ""


@dataclass
class GridMappingStrategySpec:
    strategy_id: str
    label: str
    callback: Optional[Callable[..., Any]] = None
    position: int = 50
    plugin_id: str = ""


@dataclass
class ChartTypeSpec:
    chart_type: str
    label: str
    description: str = ""
    renderer_factory: Optional[Callable[..., Any]] = None
    supports_series: bool = True
    default_options: Dict[str, Any] = field(default_factory=dict)
    plugin_id: str = ""
    data_adapter: Optional[Callable[..., Any]] = None
    supported_encodings: List[str] = field(default_factory=list)
    option_schema: Dict[str, Any] = field(default_factory=dict)
    capabilities: Dict[str, Any] = field(default_factory=dict)


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


class DefaultChartAnalysisProvider:
    provider_id = "core.prompt_managed_chart_analysis"
    plugin_id = ""

    def build_blueprint(self, payload: Any) -> Any:
        from core.engine.blueprints.chart_vision import build_chart_analysis_blueprint
        return build_chart_analysis_blueprint()


class DataProviderRegistry:
    def __init__(self) -> None:
        self._table_parsers: List[TableParserProvider] = [DefaultTableParserProvider()]
        self._vision_estimators: List[VisionEstimatorProvider] = [StubVisionEstimatorProvider()]
        self._selection_classifiers: List[SelectionClassifierProvider] = []
        self._chart_analysis_providers: List[ChartAnalysisProvider] = [DefaultChartAnalysisProvider()]
        self._grid_hooks: List[GridHookProvider] = []
        self._cleaners: Dict[str, DataCleanerSpec] = {}
        self._grid_actions: Dict[str, GridActionSpec] = {}
        self._grid_extractors: Dict[str, GridExtractorSpec] = {}
        self._grid_mapping_strategies: Dict[str, GridMappingStrategySpec] = {}
        self._chart_types: Dict[str, ChartTypeSpec] = {}
        self._palettes: Dict[str, List[str]] = {
            "default": ["#2f80ed", "#27ae60", "#f2994a", "#eb5757", "#9b51e0"],
            "print": ["#111111", "#555555", "#888888", "#bbbbbb", "#dddddd"],
            "warm": ["#c2410c", "#f59e0b", "#be123c", "#7c2d12", "#f97316"],
            "cool": ["#0f766e", "#2563eb", "#0891b2", "#4f46e5", "#16a34a"],
        }
        for chart_type, label in (
            ("bar", "Bar"), ("horizontal_bar", "Horizontal Bar"),
            ("stacked_bar", "Stacked Bar"), ("stacked_100_bar", "100% Stacked Bar"),
            ("line", "Line"), ("area", "Area"), ("stacked_area", "Stacked Area"),
            ("pie", "Pie"), ("donut", "Donut"), ("scatter", "XY Scatter"),
            ("histogram", "Histogram"), ("box", "Box Plot"), ("heatmap", "Heatmap"),
        ):
            encodings = ["x", "y", "series"]
            if chart_type in {"pie", "donut"}: encodings = ["category", "value"]
            elif chart_type == "histogram": encodings = ["value"]
            elif chart_type == "box": encodings = ["category", "value", "series"]
            elif chart_type == "heatmap": encodings = ["row", "column", "value", "series"]
            self.register_chart_type(ChartTypeSpec(
                chart_type=chart_type, label=label, supported_encodings=encodings,
                supports_series=chart_type not in {"pie", "donut", "histogram"},
                default_options={"bins": None} if chart_type == "histogram" else {},
                capabilities={"negative_values": chart_type not in {"pie", "donut", "histogram"}},
            ))

    def register_table_parser(self, provider: TableParserProvider) -> None:
        self._table_parsers.insert(0, provider)

    def register_vision_estimator(self, provider: VisionEstimatorProvider) -> None:
        self._vision_estimators.insert(0, provider)

    def register_selection_classifier(self, provider: SelectionClassifierProvider) -> None:
        self._selection_classifiers.insert(0, provider)

    def register_chart_analysis_provider(self, provider: ChartAnalysisProvider) -> None:
        self._chart_analysis_providers.insert(0, provider)

    def selection_classifiers(self) -> List[SelectionClassifierProvider]:
        return list(self._selection_classifiers)

    def chart_analysis_providers(self) -> List[ChartAnalysisProvider]:
        return list(self._chart_analysis_providers)

    def register_grid_hook(self, provider: GridHookProvider) -> None:
        self._grid_hooks.append(provider)

    def register_cleaner(self, spec: DataCleanerSpec) -> None:
        self._cleaners[spec.cleaner_id] = spec

    def register_grid_action(self, spec: GridActionSpec) -> None:
        self._grid_actions[spec.action_id] = spec

    def register_grid_extractor(self, spec: GridExtractorSpec) -> None:
        self._grid_extractors[spec.extractor_id] = spec

    def register_grid_mapping_strategy(self, spec: GridMappingStrategySpec) -> None:
        self._grid_mapping_strategies[spec.strategy_id] = spec

    def register_chart_type(self, spec: ChartTypeSpec) -> None:
        self._chart_types[spec.chart_type] = spec

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

    def cleaners(self) -> Dict[str, DataCleanerSpec]:
        return dict(self._cleaners)

    def grid_actions(self) -> Dict[str, GridActionSpec]:
        return dict(self._grid_actions)

    def grid_extractors(self) -> Dict[str, GridExtractorSpec]:
        return dict(sorted(self._grid_extractors.items(), key=lambda item: item[1].position))

    def grid_mapping_strategies(self) -> Dict[str, GridMappingStrategySpec]:
        return dict(sorted(self._grid_mapping_strategies.items(), key=lambda item: item[1].position))

    def chart_types(self) -> Dict[str, ChartTypeSpec]:
        return dict(self._chart_types)

    def chart_type(self, chart_type: str) -> Optional[ChartTypeSpec]:
        return self._chart_types.get(chart_type)

    def palettes(self) -> Dict[str, List[str]]:
        return {key: list(value) for key, value in self._palettes.items()}

    def palette(self, palette_id: str) -> List[str]:
        return list(self._palettes.get(palette_id) or self._palettes["default"])

    def remove_plugin(self, plugin_id: str) -> None:
        self._table_parsers = [item for item in self._table_parsers if getattr(item, "plugin_id", "") != plugin_id]
        self._vision_estimators = [item for item in self._vision_estimators if getattr(item, "plugin_id", "") != plugin_id]
        self._selection_classifiers = [item for item in self._selection_classifiers if getattr(item, "plugin_id", "") != plugin_id]
        self._chart_analysis_providers = [item for item in self._chart_analysis_providers if getattr(item, "plugin_id", "") != plugin_id]
        self._cleaners = {
            key: spec for key, spec in self._cleaners.items()
            if getattr(spec, "plugin_id", "") != plugin_id
        }
        self._grid_actions = {
            key: spec for key, spec in self._grid_actions.items()
            if getattr(spec, "plugin_id", "") != plugin_id
        }
        self._grid_extractors = {
            key: spec for key, spec in self._grid_extractors.items()
            if getattr(spec, "plugin_id", "") != plugin_id
        }
        self._grid_mapping_strategies = {
            key: spec for key, spec in self._grid_mapping_strategies.items()
            if getattr(spec, "plugin_id", "") != plugin_id
        }
        self._chart_types = {
            key: spec for key, spec in self._chart_types.items()
            if getattr(spec, "plugin_id", "") != plugin_id
        }


def _is_number(value: Any) -> bool:
    return is_number(value)
