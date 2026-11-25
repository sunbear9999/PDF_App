"""Pure decisions for the memory manager: pressure, what to release, worker caps.

No I/O here — everything is computed from a snapshot and settings, so the
behaviour is unit-tested directly.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from core.models.memory_models import (
    ConsumerKind, ManagerMode, MemorySettings, MemorySnapshot, Pressure, ReleaseLevel,
)

ELEVATED_MARGIN = 0.10        # elevated when free memory is within 10% of the reserve
HYSTERESIS = 0.03             # leave a level only after 3% of RAM is regained
SWAP_GROWTH_BYTES = 256 * 1024 * 1024
AGGRESSIVE_EXTRA = 0.05       # aggressive mode frees an extra 5% of RAM
MODEL_SIZE_HEADROOM = 1.2     # a model needs ~20% beyond its file size once loaded
MIN_WORKERS = 1

Release = Tuple[str, str, str]  # (consumer_id, ReleaseLevel, reason)


def pressure(snapshot: MemorySnapshot, settings: MemorySettings,
             previous: str = Pressure.NORMAL.value, swap_growth: int = 0) -> str:
    free = snapshot.available_fraction
    critical = settings.reserve_percent / 100
    elevated = critical + ELEVATED_MARGIN
    if free < critical or (previous == Pressure.CRITICAL.value and free < critical + HYSTERESIS):
        return Pressure.CRITICAL.value
    was_raised = previous in {Pressure.ELEVATED.value, Pressure.CRITICAL.value}
    if free < elevated or swap_growth > SWAP_GROWTH_BYTES or (was_raised and free < elevated + HYSTERESIS):
        return Pressure.ELEVATED.value
    return Pressure.NORMAL.value


def bytes_to_free(snapshot: MemorySnapshot, settings: MemorySettings, needed: int = 0) -> int:
    """How much must be released so ``needed`` fits while keeping the reserve."""
    target_free = snapshot.total * settings.reserve_percent / 100 + needed
    if settings.mode == ManagerMode.AGGRESSIVE.value:
        target_free += snapshot.total * AGGRESSIVE_EXTRA
    return max(0, int(target_free - snapshot.available))


def plan_releases(snapshot: MemorySnapshot, amount: int,
                  protect: Iterable[str] = ()) -> List[Release]:
    """Cheapest-to-rebuild first, never anything busy or protected, until
    ``amount`` bytes are covered."""
    if amount <= 0:
        return []
    protected = set(protect)
    candidates = sorted(
        (item for item in snapshot.consumers
         if item.can_release and item.bytes > 0 and item.state != "in_use"
         and item.id not in protected),
        key=lambda item: (item.priority, -item.idle_seconds),
    )
    plan, freed = [], 0
    for item in candidates:
        level = ReleaseLevel.TRIM.value if item.kind == ConsumerKind.CACHE.value else ReleaseLevel.UNLOAD.value
        plan.append((item.id, level, f"free {_gb(amount)} for memory"))
        freed += item.bytes
        if freed >= amount:
            break
    return plan


def idle_releases(snapshot: MemorySnapshot, settings: MemorySettings) -> List[Release]:
    """Models idle longer than their configured limit."""
    if settings.mode == ManagerMode.OFF.value:
        return []
    limits = {"chat": settings.idle_minutes_chat * 60, "embed": settings.idle_minutes_embed * 60}
    plan = []
    for item in snapshot.consumers:
        limit = limits.get(item.idle_role)
        if (limit and item.bytes > 0 and item.can_release and item.state != "in_use"
                and item.idle_seconds >= limit):
            plan.append((item.id, ReleaseLevel.UNLOAD.value,
                         f"idle for {int(item.idle_seconds // 60)} min"))
    return plan


def fits(snapshot: MemorySnapshot, settings: MemorySettings, size: int,
         releasable: int = 0) -> bool:
    """Would a ``size``-byte model fit (with headroom) while keeping the reserve?"""
    reserve = snapshot.total * settings.reserve_percent / 100
    return snapshot.available + releasable - reserve >= size * MODEL_SIZE_HEADROOM


def worker_cap(snapshot: MemorySnapshot, settings: MemorySettings,
               per_worker: int, maximum: int, pressure_level: Optional[str] = None) -> int:
    if settings.max_workers:
        return max(MIN_WORKERS, min(maximum, settings.max_workers))
    if (pressure_level or snapshot.pressure) == Pressure.CRITICAL.value:
        return MIN_WORKERS
    if per_worker <= 0 or not snapshot.total:
        return maximum
    spare = snapshot.available - snapshot.total * settings.reserve_percent / 100
    return max(MIN_WORKERS, min(maximum, int(spare // per_worker)))


def _gb(value: int) -> str:
    return f"{value / 1024 ** 3:.1f} GB"
