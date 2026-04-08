from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class NodeData:
    node_id: str
    quote: str = ""
    note: str = ""
    color: str = ""
    is_custom: bool = False
    width: int = 150
    height: int = 80
    pdf_path: Optional[str] = None
    page_num: Optional[int] = None
    manual_font_size: Optional[int] = None
    x: float = 0.0
    y: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quote": self.quote,
            "note": self.note,
            "color": self.color,
            "is_custom": self.is_custom,
            "pdf_path": self.pdf_path,
            "page_num": self.page_num,
            "manual_font_size": self.manual_font_size,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeData":
        return cls(
            node_id=data.get("node_id") or data.get("id") or "",
            quote=data.get("quote", ""),
            note=data.get("note", ""),
            color=data.get("color", ""),
            is_custom=bool(data.get("is_custom", False)),
            width=int(data.get("width", 150)) if data.get("width") is not None else 150,
            height=int(data.get("height", 80)) if data.get("height") is not None else 80,
            pdf_path=data.get("pdf_path"),
            page_num=(int(data["page_num"]) if data.get("page_num") is not None else None),
            manual_font_size=(int(data["manual_font_size"]) if data.get("manual_font_size") is not None else None),
            x=float(data.get("x", 0.0)) if data.get("x") is not None else 0.0,
            y=float(data.get("y", 0.0)) if data.get("y") is not None else 0.0,
        )


@dataclass
class EdgeData:
    edge_id: str
    source: str
    target: str
    label: str = ""
    color: str = "#888888"
    weight: int = 2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "color": self.color,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EdgeData":
        return cls(
            edge_id=data.get("id") or data.get("edge_id") or "",
            source=data.get("source", ""),
            target=data.get("target", ""),
            label=data.get("label", ""),
            color=data.get("color", "#888888"),
            weight=int(data.get("weight", 2)) if data.get("weight") is not None else 2,
        )
