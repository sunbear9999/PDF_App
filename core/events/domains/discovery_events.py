from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional
from .base import BasePayload


class DiscoveryIntent(Enum):
    RUN_EXTRACTION = auto()
    SAVE_ENTITY = auto()
    SAVE_ALL = auto()
    RUN_LLM_ANALYSIS = auto()
    FIND_REFERENCES = auto()   # find cross-document mentions of a named entity


class DiscoveryEvent(Enum):
    EXTRACTION_COMPLETE = auto()
    EXTRACTION_STARTED = auto()
    SAVE_COMPLETE = auto()
    LLM_ANALYSIS_STARTED = auto()
    REFERENCES_FOUND = auto()  # result of FIND_REFERENCES intent


@dataclass
class DiscoveryPayload(BasePayload):
    source_path: str = ""
    entity_type: str = ""
    entity_name: str = ""      # used by FIND_REFERENCES
    entity_groups: List[Any] = field(default_factory=list)
    instructions: str = ""
    model: str = ""


@dataclass
class DiscoveryEventPayload(BasePayload):
    entity_groups: List[Any] = field(default_factory=list)
    entity_type: str = ""
    entity_name: str = ""
    source_path: str = ""
    saved_ids: List[str] = field(default_factory=list)
    references: List[Dict[str, Any]] = field(default_factory=list)  # [{doc_name, doc_path, mention_count, contexts}]
    error: Optional[str] = None
