from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

from .base import BasePayload


class AISetupIntent(Enum):
    INSTALL_BACKEND = auto()
    UNINSTALL_BACKEND = auto()
    PULL_MODEL = auto()
    REMOVE_MODEL = auto()
    PULL_TTS_VOICE = auto()
    REMOVE_TTS_VOICE = auto()
    SET_ACTIVE_BACKEND = auto()
    SET_DEFAULT_MODEL = auto()
    REFRESH = auto()
    TOGGLE_AI = auto()          # payload.data = {"enabled": bool}


class AISetupEvent(Enum):
    BACKEND_STATUS_CHANGED = auto()
    MODEL_LIST_CHANGED = auto()
    TTS_VOICE_LIST_CHANGED = auto()
    PROGRESS = auto()
    OPERATION_COMPLETE = auto()
    OPERATION_FAILED = auto()
    HARDWARE_INFO_READY = auto()
    DEFAULTS_CHANGED = auto()
    AI_TOGGLE_CHANGED = auto()  # payload.data = {"enabled": bool}


@dataclass
class AISetupPayload(BasePayload):
    backend_id: Optional[str] = None
    model_id: Optional[str] = None
    voice_id: Optional[str] = None
    message: str = ""
    pct: int = 0
    data: Optional[dict] = field(default_factory=dict)
