from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional
from .base import BasePayload
class DocumentIntent(Enum):
    OPEN = auto()
    ADD_FILES = auto()
    SHOW_OCR_BANNER = auto()
    RELOAD_PAGE = auto()
    EXTRACT_PAGES = auto()
    CREATE_HIGHLIGHT = auto()
    CREATE_HIGHLIGHT_FROM_TEXT = auto()
    UPDATE_HIGHLIGHT_NOTE = auto()
    UPDATE_HIGHLIGHT_COLOR = auto()
    DELETE_HIGHLIGHT = auto()

@dataclass
class DocumentPayload(BasePayload):
    path: Optional[str] = None
    source_id: Optional[str] = None
    paths: Optional[List[str]] = None
    page_num: Optional[int] = None
    save_path: Optional[str] = None
    page_range: Optional[str] = None
    annot_id: Optional[str] = None
    text: Optional[str] = None
    note: Optional[str] = None
    color: Any = None
    rects: Optional[List[Any]] = None
    doc_name: Optional[str] = None


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
    DOCUMENT_ADDED = auto()


@dataclass
class DocumentEventPayload(BasePayload):
    path: Optional[str] = None
    source_id: Optional[str] = None
    old_path: Optional[str] = None
    old_source_id: Optional[str] = None
    new_path: Optional[str] = None
    new_source_id: Optional[str] = None
    annot_id: Optional[str] = None
    changes: Dict[str, Any] = field(default_factory=dict)
    highlight_data: Dict[str, Any] = field(default_factory=dict)
    doc: Any = None
    needs_ocr: bool = False
