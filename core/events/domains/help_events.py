from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict
from .base import BasePayload


class HelpIntent(Enum):
    SHOW_CENTER = auto()      # Open the searchable Help Center
    SHOW_TOPIC = auto()       # Open Help Center scrolled to a specific topic
    START_TUTORIAL = auto()   # Begin an interactive tutorial by ID
    STOP_TUTORIAL = auto()    # Cancel the running tutorial
    SHOW_WHATS_THIS = auto()  # Toggle What's This? cursor mode
    RESET_PROGRESS = auto()   # Clear all tutorial completion records
    SHOW_F1_HELP = auto()     # F1 pressed — resolve focused widget to a topic
    OPEN_DOCK = auto()        # Approved tutorial action: open a dock by ID
    SELECT_TAB = auto()       # Approved tutorial action: select a tab in a dock


@dataclass
class HelpPayload(BasePayload):
    topic_id: str = ""
    tutorial_id: str = ""
    dock_id: str = ""
    tab_index: int = 0
    data: Dict[str, Any] = field(default_factory=dict)


class HelpEvent(Enum):
    TUTORIAL_COMPLETED = auto()
    TUTORIAL_CANCELLED = auto()
    TUTORIAL_FAILED = auto()
    TOPIC_VIEWED = auto()


@dataclass
class HelpEventPayload(BasePayload):
    topic_id: str = ""
    tutorial_id: str = ""
    reason: str = ""
