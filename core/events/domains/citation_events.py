from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Dict, Any
from .base import BasePayload

class CitationIntent(Enum):
    REFRESH_TABLE = auto()
    UPDATE_ENTRY = auto()
    GENERATE_WORKS_CITED = auto()

@dataclass
class CitationPayload(BasePayload):
    style: str = "APA"
    doc_ids: Optional[List[str]] = None
    data: Optional[Dict[str, Any]] = None

class CitationEvent(Enum):
    TABLE_DATA_READY = auto()
    WORKS_CITED_GENERATED = auto()

@dataclass
class CitationEventPayload(BasePayload):
    data: List[Any] = field(default_factory=list)
    works: List[str] = field(default_factory=list)
    formatted_text: str = ""