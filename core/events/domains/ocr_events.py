from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional
from .base import BasePayload

class OCRIntent(Enum):
    RUN = auto()

@dataclass
class OCRPayload(BasePayload):
    file_path: Optional[str] = None
    mode: Optional[str] = None

class OCRStatus(Enum):
    RUNNING = auto()
    COMPLETE = auto()
    ERROR = auto()

@dataclass
class OCRStatusPayload(BasePayload):
    status: Optional[OCRStatus] = None
    msg: str = ""
    text: str = ""
    progress: Optional[int] = None
    total: Optional[int] = None