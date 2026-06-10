from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from .base import BasePayload


class AnalysisIntent(Enum):
    RUN = auto()
    SEND_TO_WORKSPACE = auto()
    REFRESH_REQUESTED = auto()


class AnalysisEvent(Enum):
    RUN_STARTED = auto()
    PROGRESS = auto()
    CHUNK_RESULT = auto()
    RUN_COMPLETED = auto()
    RUN_FAILED = auto()
    RESULT_READY = auto()
    SENT_TO_WORKSPACE = auto()
    TEMPLATES_CHANGED = auto()


@dataclass
class AnalysisPayload(BasePayload):
    doc_path: Optional[str] = None
    template_id: Optional[str] = None
    template: Dict[str, Any] = field(default_factory=dict)
    selected_model: Optional[str] = None
    run_id: Optional[str] = None
    workspace_id: Optional[int] = None
    result: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
