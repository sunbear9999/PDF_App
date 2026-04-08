from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class AnnotationData:
    id: str
    quote: str = ""
    note: str = ""
    color: str = ""
    pdf_path: Optional[str] = None
    page: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "id": self.id,
            "quote": self.quote,
            "note": self.note,
            "color": self.color,
            "pdf_path": self.pdf_path,
            "page": self.page,
        }
        if self.page is not None:
            result["page_num"] = self.page
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnnotationData":
        return cls(
            id=data.get("id", ""),
            quote=data.get("quote", ""),
            note=data.get("note", ""),
            color=data.get("color", ""),
            pdf_path=data.get("pdf_path"),
            page=(int(data.get("page")) if data.get("page") is not None else (
                int(data.get("page_num")) if data.get("page_num") is not None else None
            )),
        )
