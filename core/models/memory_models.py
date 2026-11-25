"""Data types for the central memory manager (see MemoryManagerService)."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Callable, Optional, Tuple


class Pressure(str, Enum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    CRITICAL = "critical"


class ReleaseLevel(str, Enum):
    TRIM = "trim"        # drop caches that rebuild cheaply
    UNLOAD = "unload"    # unload a model / close a resource; reloads on demand


class ConsumerKind(str, Enum):
    MODEL = "model"
    CACHE = "cache"
    WORKER = "worker"
    APP = "app"


class PreloadPolicy(str, Enum):
    ALWAYS = "always"
    WHEN_MEMORY_ALLOWS = "when_memory_allows"
    NEVER = "never"


class ManagerMode(str, Enum):
    OFF = "off"              # monitor only
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class MemoryConsumer:
    """Anything that holds a meaningful amount of memory.

    ``usage`` must be cheap (a cached estimate, never a deep size walk).
    ``release`` returns the bytes it believes it freed. ``thread="gui"``
    consumers are released on the Qt thread through the memory bridge.
    """
    id: str
    label: str
    kind: str
    usage: Callable[[], int]
    release: Callable[[ReleaseLevel], int]
    busy: Callable[[], bool] = lambda: False
    idle_seconds: Callable[[], float] = lambda: 0.0
    priority: int = 50
    thread: str = "any"
    load: Optional[Callable[[], None]] = None
    plugin_id: str = ""
    idle_role: str = ""          # "chat" | "embed" | "" — selects the idle-unload limit


@dataclass(frozen=True)
class ConsumerUsage:
    id: str
    label: str
    kind: str
    bytes: int
    state: str = "loaded"        # loaded | idle | in_use | empty
    idle_seconds: float = 0.0
    can_release: bool = True
    can_load: bool = False
    plugin_id: str = ""
    priority: int = 50
    idle_role: str = ""


@dataclass(frozen=True)
class MemorySnapshot:
    total: int = 0
    used: int = 0
    available: int = 0
    swap_total: int = 0
    swap_used: int = 0
    app_rss: int = 0
    children_rss: int = 0
    consumers: Tuple[ConsumerUsage, ...] = ()
    pressure: str = Pressure.NORMAL.value
    taken_at: float = 0.0

    @property
    def used_fraction(self) -> float:
        return self.used / self.total if self.total else 0.0

    @property
    def available_fraction(self) -> float:
        return self.available / self.total if self.total else 1.0

    def consumer(self, consumer_id: str) -> Optional[ConsumerUsage]:
        return next((item for item in self.consumers if item.id == consumer_id), None)


@dataclass(frozen=True)
class MemoryAction:
    at: float
    consumer_id: str
    label: str
    action: str
    freed: int
    reason: str
    automatic: bool = True


@dataclass(frozen=True)
class Grant:
    """Answer to ``reserve()``: whether to proceed and how many workers to use."""
    ok: bool
    workers: int = 1
    available: int = 0
    message: str = ""


@dataclass
class MemorySettings:
    mode: str = ManagerMode.BALANCED.value
    reserve_percent: int = 15
    idle_minutes_chat: int = 10
    idle_minutes_embed: int = 3
    preload_policy: str = PreloadPolicy.WHEN_MEMORY_ALLOWS.value
    max_workers: int = 0                 # 0 = automatic
    pause_background_on_critical: bool = True
    unload_models_on_exit: bool = True
    viewer_cache_mb: int = 0             # 0 = automatic (≈3% of RAM)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "MemorySettings":
        names = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in (data or {}).items() if key in names})

    def to_dict(self) -> dict:
        return {item.name: getattr(self, item.name) for item in fields(self)}
