from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Dict, Any
from .base import BasePayload
class TTSIntent(Enum):
    FETCH_VOICES = auto()
    GENERATE = auto()

@dataclass
class TTSPayload(BasePayload):
    text: Optional[str] = None
    voice_file: str = "voice1.onnx"
    speed: float = 1.0

class OCRIntent(Enum):
    RUN = auto()

@dataclass
class OCRPayload(BasePayload):
    file_path: Optional[str] = None
    mode: Optional[str] = None

class DictionaryIntent(Enum):
    FETCH_DICTS = auto()
    PUBLIC_SEARCH = auto()
    SEARCH = auto()
    ADD_WORD = auto()
    IMPORT = auto()
    

@dataclass
class DictionaryPayload(BasePayload):
    query: Optional[str] = None
    dict_id: Optional[str] = None
    fuzzy: bool = False
    word: Optional[str] = None
    definition: Optional[str] = None
    ext: Optional[str] = None
    path: Optional[str] = None

class CitationIntent(Enum):
    REFRESH_TABLE = auto()
    UPDATE_ENTRY = auto()
    GENERATE_WORKS_CITED = auto()

@dataclass
class CitationPayload(BasePayload):
    style: str = "APA"
    doc_ids: Optional[List[str]] = None
    data: Optional[Dict[str, Any]] = None # Used for passing raw citation dictionaries


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


class DictionaryEvent(Enum):
    DICTS_LOADED = auto()
    PUBLIC_SEARCH = auto()
    WORD_ADDED = auto()
    IMPORT_SUCCESS = auto()
    ERROR = auto()
    RESULTS_READY = auto()


@dataclass
class DictionaryEventPayload(BasePayload):
    data: List[Dict[str, Any]] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)
    query: Optional[str] = None
    word: Optional[str] = None
    msg: str = ""


class CitationEvent(Enum):
    TABLE_DATA_READY = auto()
    WORKS_CITED_GENERATED = auto()


@dataclass
class CitationEventPayload(BasePayload):
    data: List[Any] = field(default_factory=list)
    works: List[str] = field(default_factory=list)
