from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Dict, Any
from .base import BasePayload

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