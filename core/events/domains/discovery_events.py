from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, List, Optional
from .base import BasePayload


class DiscoveryIntent(Enum):
    RUN_EXTRACTION = auto()
    SAVE_ENTITY = auto()
    SAVE_ALL = auto()
    RUN_LLM_ANALYSIS = auto()


class DiscoveryEvent(Enum):
    EXTRACTION_COMPLETE = auto()
    EXTRACTION_STARTED = auto()
    SAVE_COMPLETE = auto()
    LLM_ANALYSIS_STARTED = auto()


@dataclass
class DiscoveryPayload(BasePayload):
    source_path: str = ""
    entity_type: str = ""
    entity_groups: List[Any] = field(default_factory=list)
    instructions: str = ""
    model: str = ""


@dataclass
class DiscoveryEventPayload(BasePayload):
    entity_groups: List[Any] = field(default_factory=list)
    entity_type: str = ""
    source_path: str = ""
    saved_ids: List[str] = field(default_factory=list)
    error: Optional[str] = None
