from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Dict
from .base import BasePayload

class TTSIntent(Enum):
    FETCH_VOICES = auto()
    GENERATE = auto()
    EXTRACT_TEXT = auto()

@dataclass
class TTSPayload(BasePayload):
    text: Optional[str] = None
    voice_file: str = "voice1.onnx"
    speed: float = 1.0
    path: Optional[str] = None
    start_page: int = 1
    end_page: int = 9999
    ignore_headers: bool = True

class TTSEvent(Enum):
    TEXT_EXTRACTED = auto()

@dataclass
class TTSEventPayload(BasePayload):
    text: str = ""
    char_count: int = 0
    error: Optional[str] = None

class TTSStatus(Enum):
    VOICES_LOADED = auto()
    RUNNING = auto()
    COMPLETE = auto()
    ERROR = auto()

@dataclass
class TTSStatusPayload(BasePayload):
    status: Optional[TTSStatus] = None
    msg: str = ""
    voices: Dict[str, str] = field(default_factory=dict)
    file: Optional[str] = None