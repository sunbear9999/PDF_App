from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from .base import BasePayload


class AnalysisEvent(Enum):
    RUN_STARTED = auto()
    PROGRESS = auto()
    CHUNK_RESULT = auto()
    RUN_COMPLETED = auto()
    RUN_FAILED = auto()
    RESULT_READY = auto()
    SENT_TO_WORKSPACE = auto()
    TEMPLATES_CHANGED = auto()
    # Hierarchical pipeline streaming events
    CHUNK_EVIDENCE_READY = auto()      # per-chunk evidence extracted and stored
    SECTION_SYNTHESIS_READY = auto()   # every-5-chunk section synthesis complete
    GRAPH_PLAN_READY = auto()          # graph planning pass complete
    GRAPH_HYDRATED = auto()            # quote IDs resolved to exact text


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
