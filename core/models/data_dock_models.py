from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class DataProvenance:
    source_pdf_id: Optional[str] = None
    source_id: Optional[str] = None
    source_path: Optional[str] = None
    source_type: str = "pdf"
    pdf_path: Optional[str] = None
    page_number: Optional[int] = None
    bounding_box_coordinates: List[Any] = field(default_factory=list)
    selection_text: str = ""
    parent_dataset_id: Optional[str] = None
    selection_ref: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DataProvenance":
        data = data or {}
        pdf_path = data.get("pdf_path") or data.get("source_path")
        return cls(
            source_pdf_id=data.get("source_pdf_id"),
            source_id=data.get("source_id") or data.get("source_pdf_id"),
            source_path=data.get("source_path") or pdf_path,
            source_type=data.get("source_type") or "pdf",
            pdf_path=pdf_path,
            page_number=data.get("page_number"),
            bounding_box_coordinates=list(data.get("bounding_box_coordinates") or []),
            selection_text=data.get("selection_text") or "",
            parent_dataset_id=data.get("parent_dataset_id"),
            selection_ref=data.get("selection_ref"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DataGridState:
    dataset_id: str
    name: str = "Untitled Dataset"
    headers: List[str] = field(default_factory=list)
    row_headers: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)
    column_types: Dict[str, str] = field(default_factory=dict)
    provenance: DataProvenance = field(default_factory=DataProvenance)
    cell_provenance: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_persisted: bool = False
    dirty: bool = False
    version: int = 1
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DataGridState":
        return cls(
            dataset_id=data["dataset_id"],
            name=data.get("name") or "Untitled Dataset",
            headers=list(data.get("headers") or []),
            row_headers=list(data.get("row_headers") or []),
            rows=[list(row) for row in data.get("rows") or []],
            column_types=dict(data.get("column_types") or {}),
            provenance=DataProvenance.from_dict(data.get("provenance")),
            cell_provenance=dict(data.get("cell_provenance") or {}),
            metadata=dict(data.get("metadata") or {}),
            is_persisted=bool(data.get("is_persisted", False)),
            dirty=bool(data.get("dirty", False)),
            version=int(data.get("version") or 1),
            created_at=data.get("created_at") or utc_now_iso(),
            updated_at=data.get("updated_at") or utc_now_iso(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "name": self.name,
            "headers": list(self.headers),
            "row_headers": list(self.row_headers),
            "rows": [list(row) for row in self.rows],
            "column_types": dict(self.column_types),
            "provenance": self.provenance.to_dict(),
            "cell_provenance": dict(self.cell_provenance),
            "metadata": dict(self.metadata),
            "is_persisted": self.is_persisted,
            "dirty": self.dirty,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class GridCoordinate:
    """A logical Data Dock address, including its editable title bands."""

    kind: str = "data"  # data | row_header | column_header
    row: Optional[int] = None
    column: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GridCoordinate":
        return cls(str(data.get("kind") or "data"), data.get("row"), data.get("column"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def key(self) -> str:
        if self.kind == "row_header":
            return f"row_header:{self.row}"
        if self.kind == "column_header":
            return f"column_header:{self.column}"
        return f"data:{self.row}:{self.column}"

    @property
    def sort_key(self) -> tuple[int, int]:
        # Header cells occupy row/column -1 in the logical spreadsheet plane.
        return (
            -1 if self.kind == "column_header" else int(self.row or 0),
            -1 if self.kind == "row_header" else int(self.column or 0),
        )


@dataclass
class GridSelection:
    dataset_id: str
    dataset_version: int
    coordinates: List[GridCoordinate] = field(default_factory=list)
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GridSelection":
        return cls(
            dataset_id=str(data.get("dataset_id") or ""),
            dataset_version=int(data.get("dataset_version") or 0),
            coordinates=[GridCoordinate.from_dict(item) for item in data.get("coordinates") or []],
            schema_version=int(data.get("schema_version") or 1),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "coordinates": [item.to_dict() for item in self.coordinates],
            "schema_version": self.schema_version,
        }

    def ordered(self) -> List[GridCoordinate]:
        return sorted(set(self.coordinates), key=lambda item: item.sort_key)

    def shape(self) -> tuple[int, int]:
        ordered = self.ordered()
        if not ordered:
            return (0, 0)
        keys = [item.sort_key for item in ordered]
        return (max(r for r, _ in keys) - min(r for r, _ in keys) + 1,
                max(c for _, c in keys) - min(c for _, c in keys) + 1)

    def is_rectangular(self) -> bool:
        rows, columns = self.shape()
        return bool(rows and columns and rows * columns == len(self.ordered()))


@dataclass
class PdfRegion:
    pdf_path: str
    page_number: int
    bbox: List[float]
    request_id: str = ""
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PdfRegion":
        return cls(str(data.get("pdf_path") or ""), int(data.get("page_number") or 0),
                   [float(value) for value in data.get("bbox") or []],
                   str(data.get("request_id") or ""), int(data.get("schema_version") or 1))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractedGrid:
    cells: List[List[str]] = field(default_factory=list)
    cell_boxes: List[List[List[float]]] = field(default_factory=list)
    strategy: str = ""
    warnings: List[str] = field(default_factory=list)
    provenance: DataProvenance = field(default_factory=DataProvenance)
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtractedGrid":
        return cls(
            cells=[[str(value or "") for value in row] for row in data.get("cells") or []],
            cell_boxes=[[list(box or []) for box in row] for row in data.get("cell_boxes") or []],
            strategy=str(data.get("strategy") or ""),
            warnings=[str(value) for value in data.get("warnings") or []],
            provenance=DataProvenance.from_dict(data.get("provenance")),
            schema_version=int(data.get("schema_version") or 1),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cells": [list(row) for row in self.cells],
            "cell_boxes": [[list(box) for box in row] for row in self.cell_boxes],
            "strategy": self.strategy,
            "warnings": list(self.warnings),
            "provenance": self.provenance.to_dict(),
            "schema_version": self.schema_version,
        }

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.cells), max((len(row) for row in self.cells), default=0))


@dataclass
class GridPatchAssignment:
    target: GridCoordinate
    value: str
    source_row: int
    source_column: int
    source_box: List[float] = field(default_factory=list)
    existing_value: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GridPatchAssignment":
        return cls(GridCoordinate.from_dict(data.get("target") or {}), str(data.get("value") or ""),
                   int(data.get("source_row") or 0), int(data.get("source_column") or 0),
                   list(data.get("source_box") or []), str(data.get("existing_value") or ""))

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["target"] = self.target.to_dict()
        return data


@dataclass
class GridPatchPlan:
    dataset_id: str
    expected_version: int
    selection: GridSelection
    extracted_grid: ExtractedGrid
    assignments: List[GridPatchAssignment] = field(default_factory=list)
    orientation: str = "direct"
    conflicts: List[str] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
    requires_preview: bool = False
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GridPatchPlan":
        return cls(
            dataset_id=str(data.get("dataset_id") or ""),
            expected_version=int(data.get("expected_version") or 0),
            selection=GridSelection.from_dict(data.get("selection") or {}),
            extracted_grid=ExtractedGrid.from_dict(data.get("extracted_grid") or {}),
            assignments=[GridPatchAssignment.from_dict(item) for item in data.get("assignments") or []],
            orientation=str(data.get("orientation") or "direct"),
            conflicts=[str(item) for item in data.get("conflicts") or []],
            diagnostics=[str(item) for item in data.get("diagnostics") or []],
            requires_preview=bool(data.get("requires_preview", False)),
            schema_version=int(data.get("schema_version") or 1),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id, "expected_version": self.expected_version,
            "selection": self.selection.to_dict(), "extracted_grid": self.extracted_grid.to_dict(),
            "assignments": [item.to_dict() for item in self.assignments],
            "orientation": self.orientation, "conflicts": list(self.conflicts),
            "diagnostics": list(self.diagnostics), "requires_preview": self.requires_preview,
            "schema_version": self.schema_version,
        }


@dataclass
class GridPatchResult:
    dataset_id: str
    dataset_version: int
    applied: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GridPatchResult":
        return cls(str(data.get("dataset_id") or ""), int(data.get("dataset_version") or 0),
                   list(data.get("applied") or []), list(data.get("skipped") or []))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ChartConfig:
    chart_id: str
    name: str = "Untitled Chart"
    chart_type: str = "bar"
    dataset_id: Optional[str] = None
    node_id: Optional[str] = None
    title: str = ""
    subtitle: str = ""
    x_field: str = ""
    y_field: str = ""
    x_title: str = ""
    y_title: str = ""
    show_x_labels: bool = True
    show_y_labels: bool = True
    show_data_labels: bool = False
    show_grid_lines: bool = True
    show_tick_marks: bool = True
    show_legend: bool = True
    palette_id: str = "default"
    color_overrides: Dict[str, str] = field(default_factory=dict)
    series: List[Dict[str, Any]] = field(default_factory=list)
    source_selection: Dict[str, Any] = field(default_factory=dict)
    export_options: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    schema_version: int = 2
    encodings: Dict[str, Any] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChartConfig":
        encodings = dict(data.get("encodings") or {})
        return cls(
            chart_id=data["chart_id"],
            name=data.get("name") or "Untitled Chart",
            chart_type=data.get("chart_type") or "bar",
            dataset_id=data.get("dataset_id"),
            node_id=data.get("node_id"),
            title=data.get("title") or data.get("name") or "Untitled Chart",
            subtitle=data.get("subtitle") or "",
            x_field=data.get("x_field") or encodings.get("x") or "",
            y_field=data.get("y_field") or encodings.get("y") or "",
            x_title=data.get("x_title") or "",
            y_title=data.get("y_title") or "",
            show_x_labels=bool(data.get("show_x_labels", True)),
            show_y_labels=bool(data.get("show_y_labels", True)),
            show_data_labels=bool(data.get("show_data_labels", False)),
            show_grid_lines=bool(data.get("show_grid_lines", True)),
            show_tick_marks=bool(data.get("show_tick_marks", True)),
            show_legend=bool(data.get("show_legend", True)),
            palette_id=data.get("palette_id") or "default",
            color_overrides=dict(data.get("color_overrides") or {}),
            series=[dict(item) for item in data.get("series") or []],
            source_selection=dict(data.get("source_selection") or {}),
            export_options=dict(data.get("export_options") or {}),
            schema_version=int(data.get("schema_version") or 1),
            encodings=encodings,
            options=dict(data.get("options") or {}),
            created_at=data.get("created_at") or utc_now_iso(),
            updated_at=data.get("updated_at") or utc_now_iso(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
