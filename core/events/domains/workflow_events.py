# core/events/domains/workflow_events.py
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional
from .base import BasePayload

class WorkflowIntent(Enum):
    ANALYSIS_REFRESH_REQUESTED = auto()
    RUN_BLUEPRINT = auto()
    ABORT_WORKFLOW = auto()

class WorkflowEvent(Enum):
    STARTED = auto()
    PROGRESS = auto()
    STEP_COMPLETE = auto()
    COMPLETED = auto()
    FAILED = auto()
    USER_INPUT_REQUESTED = auto()
    STATE_SNAPSHOT = auto()

@dataclass
class WorkflowPayload(BasePayload):
    data: Dict[str, Any] = field(default_factory=dict)
    blueprint: Optional[Any] = None
    initial_state: Dict[str, Any] = field(default_factory=dict)
    job_id: Optional[str] = None
    errors: Optional[str] = None
    target_id: Optional[str] = None
    job_name: Optional[str] = None
    job_type: Optional[str] = None
    is_express: bool = False
    runner: Optional[Any] = None
