from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
from .base import BasePayload


class ExtractionIntent(Enum):
    PARSE_DOCUMENT = auto()
    EXTRACT_ALL = auto()
    EXTRACT_CITATIONS = auto()
    EXTRACT_ENTITIES = auto()
    EXTRACT_DATES = auto()
    EXTRACT_TABLES = auto()
    INVALIDATE_CACHE = auto()


class ExtractionEvent(Enum):
    BLOCKS_READY = auto()
    CITATIONS_READY = auto()
    ENTITIES_READY = auto()
    DATES_READY = auto()
    TABLES_READY = auto()
    EXTRACTION_FAILED = auto()


@dataclass
class ExtractionPayload(BasePayload):
    doc_id: str = ""
    extractor_name: str = ""
    error: Optional[str] = None
