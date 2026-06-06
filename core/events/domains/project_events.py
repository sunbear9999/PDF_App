# core/events/domains/project_events.py
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, Optional
from .base import BasePayload
class ProjectIntent(Enum):
    CREATE = auto()
    LOAD = auto()
    SAVE = auto()
    SAVE_AS = auto()
    EXPORT_LOG = auto()
    FLUSH_UI_STATES = auto()
    SAVE_COMPLETED = auto()
    EXPORT_LOG_RESULT = auto()

@dataclass
class ProjectPayload(BasePayload):
    path: Optional[str] = None
    new_path: Optional[str] = None
    success: Optional[bool] = None
    msg: Optional[str] = None


class ProjectEvent(Enum):
    LOADED = auto()
    CLEARING_STARTED = auto()
    SAVED = auto()
    THEME_CHANGED = auto()


@dataclass
class ProjectEventPayload(BasePayload):
    theme: Optional[Dict[str, Any]] = None
