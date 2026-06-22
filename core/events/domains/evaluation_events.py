from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from .base import BasePayload


class SourceEvalIntent(Enum):
    RUN_EVALUATION = auto()


class SourceEvalEvent(Enum):
    EVALUATION_STARTED = auto()
    EVALUATION_COMPLETE = auto()
    METADATA_REQUESTED = auto()
    METADATA_PROVIDED = auto()


@dataclass
class SourceEvalPayload(BasePayload):
    pdf_path: str = ""
    correlation_id: str = ""
    missing_fields: List[str] = field(default_factory=list)
    doi: Optional[str] = None
    journal: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceEvalEventPayload(BasePayload):
    pdf_path: str = ""
    correlation_id: str = ""
    score: Optional[int] = None
    ledger: List[Dict] = field(default_factory=list)
    is_retracted: bool = False
    needs_manual_review: bool = False
    error: Optional[str] = None
    doi: Optional[str] = None
    journal: Optional[str] = None
    missing_fields: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
