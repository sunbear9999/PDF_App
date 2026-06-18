"""
core/events/domains/pack_events.py

Events and intents for the .ppack import/export system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional

from .base import BasePayload


class PackIntent(Enum):
    EXPORT = auto()
    IMPORT = auto()
    PREVIEW = auto()


@dataclass
class PackPayload(BasePayload):
    file_path: str = ""
    selection: Dict[str, List[str]] = field(default_factory=dict)
    pack_name: str = ""
    pack_description: str = ""


class PackEvent(Enum):
    EXPORT_COMPLETE = auto()
    IMPORT_COMPLETE = auto()
    EXPORT_FAILED = auto()
    IMPORT_FAILED = auto()
    PREVIEW_READY = auto()
