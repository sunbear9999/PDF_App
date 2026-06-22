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

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChartConfig":
        return cls(
            chart_id=data["chart_id"],
            name=data.get("name") or "Untitled Chart",
            chart_type=data.get("chart_type") or "bar",
            dataset_id=data.get("dataset_id"),
            node_id=data.get("node_id"),
            title=data.get("title") or data.get("name") or "Untitled Chart",
            subtitle=data.get("subtitle") or "",
            x_field=data.get("x_field") or "",
            y_field=data.get("y_field") or "",
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
            created_at=data.get("created_at") or utc_now_iso(),
            updated_at=data.get("updated_at") or utc_now_iso(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
