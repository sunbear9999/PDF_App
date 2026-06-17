from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Dict, Any
from .base import BasePayload

class EssayIntent(Enum):
    LIST = auto()
    LOAD = auto()
    SAVE = auto()

class EssayEvent(Enum):
    LIST_READY = auto()
    LOADED = auto()
    SAVED = auto()

@dataclass
class EssayPayload(BasePayload):
    essay_id: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None

@dataclass
class EssayEventPayload(BasePayload):
    essays: List[Dict[str, Any]] = field(default_factory=list)
    essay_id: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None

class IndexIntent(Enum):
    CHECK_STATUS = auto()
    RUN_INDEXING = auto()

class IndexEvent(Enum):
    STATUS_READY = auto()
    PROGRESS = auto()
    COMPLETE = auto()
    FAILED = auto()

@dataclass
class IndexPayload(BasePayload):
    pdf_paths: Optional[List[str]] = None

@dataclass
class IndexEventPayload(BasePayload):
    is_indexed: bool = False
    doc_count: int = 0
    progress_msg: Optional[str] = None
    error: Optional[str] = None