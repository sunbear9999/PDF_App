from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional
from .base import BasePayload
class NotesIntent(Enum):
    FETCH = auto()
    DELETE = auto()
    CHANGE_COLOR = auto()
    SYNC_TAGS = auto()

@dataclass
class NotesPayload(BasePayload):
    scope: str = "Current PDF"
    tag: Optional[str] = None
    active_pdf: Optional[str] = None
    annot_id: Optional[str] = None
    pdf_path: Optional[str] = None
    page_num: Optional[int] = None
    color: Optional[tuple] = None # Or string, depending on your color implementation
    cursor: str = ''
    limit: int = 25

class TagIntent(Enum):
    FETCH_ALL = auto()
    FETCH_DETAILS = auto()
    FETCH_TARGET_ASSIGNMENTS = auto()
    CREATE = auto()
    DELETE = auto()
    MASS_ASSIGN = auto()
    UPDATE_ASSIGNMENTS = auto()
    PREVIEW_RULE = auto()
    APPLY_RULE = auto()
    SET_PARENT = auto()

@dataclass
class TagPayload(BasePayload):
    tag_id: Optional[str] = None
    target_id: Optional[str] = None
    target_type: Optional[str] = None
    name: Optional[str] = None
    color: Optional[str] = None
    assign_docs: Optional[List[str]] = None
    remove_docs: Optional[List[str]] = None
    assign_tags: Optional[List[str]] = None
    remove_tags: Optional[List[str]] = None
    request_id: Optional[str] = None
    rule: Dict[str, Any] = field(default_factory=dict)
    parent_tag_id: Optional[int] = None

class PromptIntent(Enum):
    SAVE = auto()
    DELETE = auto()
    RESTORE = auto()

@dataclass
class PromptPayload(BasePayload):
    key: Optional[str] = None
    content: Optional[str] = None


class NotesEvent(Enum):
    DATA_READY = auto()


@dataclass
class NotesEventPayload(BasePayload):
    notes: List[Dict[str, Any]] = field(default_factory=list)
    next_cursor: str = ''
    diagnostics: List[str] = field(default_factory=list)


class TagEvent(Enum):
    ALL_TAGS = auto()
    TAG_DETAILS = auto()
    TARGET_ASSIGNMENTS = auto()
    RULE_PREVIEW = auto()
    RULE_APPLIED = auto()
    ASSIGNMENTS_CHANGED = auto()
    PARENT_UPDATED = auto()


@dataclass
class TagEventPayload(BasePayload):
    tags: List[Dict[str, Any]] = field(default_factory=list)
    docs: List[Dict[str, Any]] = field(default_factory=list)
    assigned: List[Dict[str, Any]] = field(default_factory=list)
    all_tags: List[Dict[str, Any]] = field(default_factory=list)
    request_id: Optional[str] = None
    matched_sources: List[Dict[str, Any]] = field(default_factory=list)
    matched_count: int = 0
    changed_count: int = 0
    error: Optional[str] = None


class PromptEvent(Enum):
    UPDATED = auto()


@dataclass
class PromptEventPayload(BasePayload):
    data: Dict[str, Any] = field(default_factory=dict)
