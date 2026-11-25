from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional
from .base import BasePayload

class OCRIntent(Enum):
    RUN = auto()
    CANCEL = auto()

@dataclass
class OCRPayload(BasePayload):
    file_path: Optional[str] = None
    mode: Optional[str] = None
    # Set when OCR was requested to unblock intelligence for a source.
    source_id: Optional[str] = None

class OCRStatus(Enum):
    RUNNING = auto()
    COMPLETE = auto()
    ERROR = auto()
    CANCELLED = auto()

@dataclass
class OCRStatusPayload(BasePayload):
    status: Optional[OCRStatus] = None
    msg: str = ""
    # Only "text" mode carries recognised text (it is the preview); file modes
    # write a PDF and leave this empty so signals stay small.
    text: str = ""
    progress: Optional[int] = None
    total: Optional[int] = None
    file_path: Optional[str] = None
    output_path: Optional[str] = None
    source_id: Optional[str] = None
    mode: Optional[str] = None
