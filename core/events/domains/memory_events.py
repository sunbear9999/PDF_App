"""Memory-manager requests and notifications (see MemoryManagerService)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from core.models.memory_models import MemoryAction, MemorySnapshot
from .base import BasePayload


class MemoryIntent(Enum):
    REFRESH = auto()
    UNLOAD = auto()             # consumer_id
    LOAD = auto()               # consumer_id
    UNLOAD_ALL_MODELS = auto()
    TRIM_CACHES = auto()
    SET_SAMPLING = auto()       # fast=True while a memory view is visible


class MemoryEvent(Enum):
    SNAPSHOT = auto()           # snapshot
    ACTION_TAKEN = auto()       # action
    PRESSURE_CHANGED = auto()   # snapshot
    SETTINGS_CHANGED = auto()


@dataclass
class MemoryPayload(BasePayload):
    # Fields hold immutable values only (frozen snapshot/action dataclasses).
    snapshot: Optional[MemorySnapshot] = None
    action: Optional[MemoryAction] = None
    consumer_id: str = ""
    level: str = ""
    fast: bool = False
