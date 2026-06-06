from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional
from .base import BasePayload
class DocumentIntent(Enum):
    OPEN = auto()
    ADD_FILES = auto()
    SHOW_OCR_BANNER = auto()
    RELOAD_PAGE = auto()

@dataclass
class DocumentPayload(BasePayload):
    path: Optional[str] = None
    paths: Optional[List[str]] = None
    page_num: Optional[int] = None


class AnnotationIntent(Enum):
    EDIT_POPUP = auto()
    FORCE_REDRAW = auto()
    JUMP_TO_PAGE = auto()


@dataclass
class AnnotationPayload(BasePayload):
    target_annot: object = None
    annot_id: Optional[str] = None
    page_num: Optional[int] = None
    pdf_path: Optional[str] = None


class DocumentEvent(Enum):
    HIGHLIGHT_CREATED = auto()
    HIGHLIGHT_UPDATED = auto()
    HIGHLIGHT_DELETED = auto()
    PDF_SWITCHED = auto()
    PDF_RENAMED = auto()
    PDF_REMOVED = auto()
    DOCUMENT_OPENED = auto()


@dataclass
class DocumentEventPayload(BasePayload):
    path: Optional[str] = None
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    annot_id: Optional[str] = None
    changes: Dict[str, Any] = field(default_factory=dict)
    highlight_data: Dict[str, Any] = field(default_factory=dict)
    doc: Any = None
    needs_ocr: bool = False
