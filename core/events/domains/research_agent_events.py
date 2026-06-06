from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict

from .base import BasePayload


class ResearchAgentEvent(Enum):
    SESSION_UPDATED = auto()
    STATUS_CHANGED = auto()
    CHECKPOINT_REQUESTED = auto()
    ERROR = auto()


@dataclass
class ResearchAgentPayload(BasePayload):
    session: Dict[str, Any] = field(default_factory=dict)
    checkpoint: Dict[str, Any] = field(default_factory=dict)
    message: str = ""
