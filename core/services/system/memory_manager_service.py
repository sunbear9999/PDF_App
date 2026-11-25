"""One owner for memory: measure it, keep a reserve free, load and unload on demand.

Everything that holds significant memory registers a ``MemoryConsumer``; LLM
models resident in the Ollama server are added from the provider each sample.
A single background thread samples (psutil, ~7 ms every 2–10 s), applies idle limits and
pressure responses, and runs manual requests from the GUI, so nothing here
blocks the Qt thread (rules.md §1.3). Heavy work calls ``reserve()`` first and
gets told how many workers it may use. Decisions live in ``memory_policy``.
"""
from __future__ import annotations

import gc
import json
import os
import queue
import threading
import time
from collections import deque
from dataclasses import replace
from typing import Callable, Deque, Iterable, List, Optional

from PySide6.QtCore import QThreadPool

from core.events.domains.memory_events import MemoryEvent, MemoryIntent, MemoryPayload
from core.models.memory_models import (
    ConsumerKind, ConsumerUsage, Grant, ManagerMode, MemoryAction, MemoryConsumer,
    MemorySettings, MemorySnapshot, PreloadPolicy, Pressure, ReleaseLevel,
)
from core.registries.memory_consumer_registry import MemoryConsumerRegistry
from core.services.system import memory_policy as policy
from core.services.system.memory_sampling import sample_system

SETTINGS_FILE = "memory_settings.json"
IDLE_INTERVAL_S = 10.0
FAST_INTERVAL_S = 2.0
BUSY_WINDOW_S = 60.0          # sample fast for a minute after any reserve()
MODEL_POLL_EVERY = 3          # ask the LLM server what is loaded every Nth tick
SIGNIFICANT_CHANGE = 0.01     # emit a snapshot when used memory moves by 1% of RAM
MAX_ACTIONS = 50
CHAT_PRIORITY, EMBED_PRIORITY = 80, 60
EXPRESS_JOB_BYTES = 300 * 1024 * 1024   # a plugin/vision background job
MAX_EXPRESS_JOBS = 4
POOL_TASK_BYTES = 400 * 1024 * 1024     # a Whisper/ingestion task on QThreadPool
SHUTDOWN_JOIN_S = 0.5
EXIT_UNLOAD_TIMEOUT_S = 0.5             # send the unload; do not wait for the server


class MemoryManagerService:
    # ArtifactTaskRunner contract: usable with no project open.
    allow_without_project = True
    manages_project_locks = True
    project_manager = None
    db = None

    def __init__(self, *, user_data_dir: str, bus=None, llm_manager=None,
                 registry: Optional[MemoryConsumerRegistry] = None) -> None:
        self.bus = bus
        self.llm_manager = llm_manager
        self.registry = registry or MemoryConsumerRegistry()
        self._settings_path = os.path.join(user_data_dir, SETTINGS_FILE)
        self.settings = self._load_settings()
        self._snapshot = MemorySnapshot()
        self._emitted_used = -1
        self._last_swap = 0
        self._llm_models: List[dict] = []
        self._tick = 0
        self._viewers = 0
        self._busy_until = 0.0
        self._published_pressure = ""
        self._actions: Deque[MemoryAction] = deque(maxlen=MAX_ACTIONS)
        self._commands: "queue.Queue" = queue.Queue()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._gui_dispatch: Optional[Callable[[Callable[[], None]], None]] = None
        self.attach_llm()
        if bus is not None:
            bus.memory_action_requested.connect(self._on_intent)

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="memory-manager", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        """Fast exit: the sampler is a daemon, and unload requests are sent
        without waiting for the server to finish freeing memory."""
        self._stop.set()
        self._commands.put(None)
        if self._thread is not None:
            self._thread.join(timeout=SHUTDOWN_JOIN_S)
        provider = self._provider()
        if self.settings.unload_models_on_exit and provider is not None:
            for name in list(getattr(provider, "loaded_by_papyrus", ())):
                try:
                    provider.unload_model(name, timeout=EXIT_UNLOAD_TIMEOUT_S)
                except TypeError:          # a provider without the timeout argument
                    provider.unload_model(name)

    def set_gui_dispatcher(self, dispatch: Callable[[Callable[[], None]], None]) -> None:
        """Runs GUI-owned releases on the Qt thread (see MemoryConsumerBridge)."""
        self._gui_dispatch = dispatch

    # ------------------------------------------------------------- settings

    def get_settings(self) -> dict:
        return self.settings.to_dict()

    def save_settings(self, values: dict) -> dict:
        with self._lock:
            self.settings = MemorySettings.from_dict({**self.settings.to_dict(), **(values or {})})
            os.makedirs(os.path.dirname(self._settings_path), exist_ok=True)
            with open(self._settings_path, "w", encoding="utf-8") as handle:
                json.dump(self.settings.to_dict(), handle, indent=2)
        self._emit(MemoryEvent.SETTINGS_CHANGED, MemoryPayload())
        self._commands.put(("refresh",))
        return self.settings.to_dict()

    def _load_settings(self) -> MemorySettings:
        try:
            with open(self._settings_path, encoding="utf-8") as handle:
                return MemorySettings.from_dict(json.load(handle))
        except (OSError, ValueError):
            return MemorySettings()

    def keep_alive(self, role: str) -> str:
        minutes = self.settings.idle_minutes_embed if role == "embed" else self.settings.idle_minutes_chat
        return f"{max(1, int(minutes))}m"

    # ----------------------------------------------------------- consumers

    def register_consumer(self, consumer: MemoryConsumer) -> None:
        self.registry.register(consumer)

    def unregister_consumer(self, consumer_id: str) -> None:
        self.registry.unregister(consumer_id)

    def latest_snapshot(self) -> MemorySnapshot:
        """The last sample, never sampling now (safe on the Qt thread)."""
        return self._snapshot

    def snapshot(self) -> MemorySnapshot:
        return self._snapshot if self._snapshot.total else self.sample()

    def recent_actions(self) -> List[MemoryAction]:
        return list(self._actions)

    # -------------------------------------------------------------- sampling

    def sample(self, poll_models: bool = True) -> MemorySnapshot:
        system = sample_system()
        if poll_models:
            self._llm_models = self._provider_models()
        consumers = tuple(self._usage(item) for item in self._consumers())
        snapshot = MemorySnapshot(
            total=system["total"], used=system["used"], available=system["available"],
            swap_total=system["swap_total"], swap_used=system["swap_used"],
            app_rss=system["app_rss"], children_rss=system["children_rss"],
            consumers=consumers, taken_at=time.time(),
        )
        level = policy.pressure(snapshot, self.settings, self._snapshot.pressure,
                                max(0, system["swap_used"] - self._last_swap) if self._last_swap else 0)
        self._last_swap = system["swap_used"]
        snapshot = replace(snapshot, pressure=level)
        self._snapshot = snapshot
        return snapshot

    def _consumers(self) -> List[MemoryConsumer]:
        return self.registry.all() + [self._llm_consumer(model) for model in self._llm_models]

    @staticmethod
    def _usage(consumer: MemoryConsumer) -> ConsumerUsage:
        try:
            size, busy, idle = int(consumer.usage() or 0), bool(consumer.busy()), float(consumer.idle_seconds())
        except Exception:
            size, busy, idle = 0, False, 0.0
        state = "in_use" if busy else ("empty" if not size else ("idle" if idle >= 60 else "loaded"))
        return ConsumerUsage(
            id=consumer.id, label=consumer.label, kind=consumer.kind, bytes=size, state=state,
            idle_seconds=idle, can_release=consumer.priority >= 0, can_load=consumer.load is not None,
            plugin_id=consumer.plugin_id, priority=consumer.priority, idle_role=consumer.idle_role,
        )

    # --------------------------------------------------------- admission

    def reserve(self, size: int, purpose: str, *, protect: Iterable[str] = (),
                per_worker: int = 0, max_workers: int = 1) -> Grant:
        """Make room for work about to start. Call from a background thread."""
        self._busy_until = time.monotonic() + BUSY_WINDOW_S
        snapshot = self.sample()
        if self.settings.mode != ManagerMode.OFF.value:
            needed = max(0, int(size)) + max(0, per_worker)
            amount = policy.bytes_to_free(snapshot, self.settings, needed)
            for consumer_id, level, _reason in policy.plan_releases(snapshot, amount, protect):
                self.release(consumer_id, level, f"{purpose} needed memory", automatic=True)
            if amount:
                snapshot = self.sample()
        workers = policy.worker_cap(snapshot, self.settings, per_worker, max_workers)
        spare = snapshot.available - snapshot.total * self.settings.reserve_percent / 100
        return Grant(ok=spare >= size, workers=workers, available=snapshot.available,
                     message="" if spare >= size else f"Low memory for {purpose}")

    def admit_job(self, job) -> bool:
        """ProcessRegistry asks before starting a queued job: hold background
        work while memory is critical (user-started work always runs)."""
        if (self.settings.mode == ManagerMode.OFF.value or not self.settings.pause_background_on_critical
                or not getattr(job, "is_background", False)):
            return True
        return self.snapshot().pressure != Pressure.CRITICAL.value

    def express_limit(self) -> int:
        return self.worker_cap(EXPRESS_JOB_BYTES, MAX_EXPRESS_JOBS)

    def apply_thread_pool_cap(self) -> None:
        """Qt's global pool runs transcription/ingestion tasks; size it to memory."""
        try:
            cores = os.cpu_count() or 2
            QThreadPool.globalInstance().setMaxThreadCount(self.worker_cap(POOL_TASK_BYTES, cores))
        except Exception:
            pass

    def has_headroom(self, size: int) -> bool:
        """Is there room for ``size`` bytes of optional work above the reserve?"""
        return policy.fits(self.snapshot(), self.settings, size)

    def worker_cap(self, per_worker: int, maximum: int) -> int:
        return policy.worker_cap(self.snapshot(), self.settings, per_worker, maximum)

    def request_preload(self, model_name: str) -> bool:
        provider = self._provider()
        if not model_name or provider is None:
            return False
        choice = self.settings.preload_policy
        if choice == PreloadPolicy.NEVER.value:
            self._record("llm:" + model_name, model_name, "skipped preload", 0, "preloading is turned off")
            return False
        size = getattr(provider, "model_size", lambda _name: 0)(model_name)
        if choice == PreloadPolicy.WHEN_MEMORY_ALLOWS.value and size:
            snapshot = self.sample()
            if not policy.fits(snapshot, self.settings, size):
                spare = snapshot.available - snapshot.total * self.settings.reserve_percent / 100
                self._record("llm:" + model_name, model_name, "skipped preload", 0,
                             f"needs ~{_gb(size * policy.MODEL_SIZE_HEADROOM)} but only {_gb(max(0, spare))} "
                             f"is free above the {self.settings.reserve_percent}% reserve; "
                             "it loads when first used")
                return False
        provider.preload_model(model_name)
        self._commands.put(("refresh",))
        return True

    # --------------------------------------------------------- releasing

    def release(self, consumer_id: str, level: str = ReleaseLevel.UNLOAD.value,
                reason: str = "requested", automatic: bool = False) -> int:
        consumer = next((item for item in self._consumers() if item.id == consumer_id), None)
        if consumer is None:
            return 0
        usage = self._usage(consumer)
        if usage.state == "in_use":
            self._record(consumer_id, consumer.label, "kept", 0, "in use right now — try again shortly", automatic)
            return 0
        try:
            if consumer.thread == "gui" and self._gui_dispatch is not None:
                self._gui_dispatch(lambda: consumer.release(ReleaseLevel(level)))
                freed = usage.bytes   # runs later on the Qt thread
            else:
                freed = int(consumer.release(ReleaseLevel(level)) or 0)
        except Exception as exc:
            self._record(consumer_id, consumer.label, "release failed", 0, str(exc), automatic)
            return 0
        if consumer.id.startswith("llm:"):
            self._llm_models = [m for m in self._llm_models if f"llm:{m['name']}" != consumer.id]
        self._record(consumer_id, consumer.label,
                     "trimmed" if level == ReleaseLevel.TRIM.value else "unloaded", freed, reason, automatic)
        return freed

    def unload_all_models(self, reason: str = "requested") -> int:
        return sum(self.release(item.id, ReleaseLevel.UNLOAD.value, reason)
                   for item in self.sample().consumers
                   if item.kind == ConsumerKind.MODEL.value and item.bytes and item.state != "in_use")

    def trim_caches(self, reason: str = "requested") -> int:
        freed = sum(self.release(item.id, ReleaseLevel.TRIM.value, reason)
                    for item in self.sample(poll_models=False).consumers
                    if item.kind == ConsumerKind.CACHE.value and item.bytes)
        gc.collect()
        return freed

    def on_out_of_memory(self) -> None:
        """The LLM server reported an out-of-memory error."""
        for item in self.sample().consumers:
            if item.kind == ConsumerKind.MODEL.value and item.state != "in_use" and item.can_release:
                self.release(item.id, ReleaseLevel.UNLOAD.value, "the model server ran out of memory", True)

    # ----------------------------------------------------------- thread loop

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                command = self._commands.get(timeout=self._interval())
            except queue.Empty:
                command = ("tick",)
            if command is None:
                break
            try:
                self._handle(command)
            except Exception as exc:  # the manager must never die silently
                print(f"[MemoryManager] {command[0]} failed: {exc}")

    def _interval(self) -> float:
        fast = self._viewers > 0 or time.monotonic() < self._busy_until
        return FAST_INTERVAL_S if fast else IDLE_INTERVAL_S

    def _handle(self, command: tuple) -> None:
        name, changed = command[0], True
        if name == "tick":
            self._tick += 1
            changed = self._auto_adjust(self.sample(poll_models=self._tick % MODEL_POLL_EVERY == 0))
        elif name == "release":
            self.release(command[1], command[2], "unloaded from the memory panel")
        elif name == "load":
            self._load(command[1])
        elif name == "unload_models":
            self.unload_all_models("unloaded from the memory panel")
        elif name == "trim":
            self.trim_caches("freed from the memory panel")
        if changed:
            self.sample(poll_models=name == "refresh")
        self._publish(self._snapshot, force=name != "tick")

    def _auto_adjust(self, snapshot: MemorySnapshot) -> bool:
        plan = policy.idle_releases(snapshot, self.settings)
        if self.settings.mode != ManagerMode.OFF.value and snapshot.pressure != Pressure.NORMAL.value:
            plan += policy.plan_releases(snapshot, policy.bytes_to_free(snapshot, self.settings))
        done = set()
        for consumer_id, level, reason in plan:
            if consumer_id not in done:
                done.add(consumer_id)
                self.release(consumer_id, level, reason, automatic=True)
        return bool(done)

    def _load(self, consumer_id: str) -> None:
        consumer = next((item for item in self._consumers() if item.id == consumer_id), None)
        if consumer is not None and consumer.load is not None:
            consumer.load()

    def _publish(self, snapshot: MemorySnapshot, force: bool = False) -> None:
        previous = self._emitted_used
        moved = abs(snapshot.used - previous) >= snapshot.total * SIGNIFICANT_CHANGE
        if force or self._viewers or previous < 0 or moved:
            self._emitted_used = snapshot.used
            self._emit(MemoryEvent.SNAPSHOT, MemoryPayload(snapshot=snapshot))
        if self._published_pressure != snapshot.pressure:
            self._published_pressure = snapshot.pressure
            self.apply_thread_pool_cap()
            self._emit(MemoryEvent.PRESSURE_CHANGED, MemoryPayload(snapshot=snapshot))

    def _on_intent(self, intent, payload) -> None:
        """Bus requests arrive on the Qt thread; the work runs on ours."""
        consumer_id = getattr(payload, "consumer_id", "")
        if intent == MemoryIntent.SET_SAMPLING:
            self._viewers = max(0, self._viewers + (1 if getattr(payload, "fast", False) else -1))
            self._commands.put(("refresh",))
        elif intent == MemoryIntent.UNLOAD and consumer_id:
            self._commands.put(("release", consumer_id, getattr(payload, "level", "") or ReleaseLevel.UNLOAD.value))
        elif intent == MemoryIntent.LOAD and consumer_id:
            self._commands.put(("load", consumer_id))
        elif intent == MemoryIntent.UNLOAD_ALL_MODELS:
            self._commands.put(("unload_models",))
        elif intent == MemoryIntent.TRIM_CACHES:
            self._commands.put(("trim",))
        elif intent == MemoryIntent.REFRESH:
            self._commands.put(("refresh",))

    # ------------------------------------------------------------ LLM models

    def _provider(self):
        return getattr(self.llm_manager, "provider", None) if self.llm_manager else None

    def attach_llm(self) -> None:
        """Hook the active LLM provider (again after a backend switch)."""
        if self.llm_manager is None:
            return
        self.llm_manager.memory_manager = self
        provider = self._provider()
        if provider is not None and hasattr(provider, "keep_alive_source"):
            provider.keep_alive_source = self.keep_alive
            provider.on_out_of_memory = self.on_out_of_memory

    def _provider_models(self) -> List[dict]:
        provider = self._provider()
        lister = getattr(provider, "loaded_models", None)
        return list(lister() or []) if lister and getattr(provider, "ai_enabled", True) else []

    def _llm_consumer(self, model: dict) -> MemoryConsumer:
        provider, name = self._provider(), model["name"]
        embed = name == getattr(provider, "embedding_model_name", lambda: "")()
        ours = name in getattr(provider, "loaded_by_papyrus", set())
        return MemoryConsumer(
            id=f"llm:{name}", label=name if ours else f"{name} (loaded by another app)",
            kind=ConsumerKind.MODEL.value, usage=lambda size=model["size"]: size,
            release=lambda _level, n=name, size=model["size"]: provider.unload_model(n) or size,
            busy=lambda n=name: provider.model_busy(n),
            idle_seconds=lambda n=name: provider.model_idle_seconds(n),
            # Another app's model is never unloaded automatically (priority < 0).
            priority=(EMBED_PRIORITY if embed else CHAT_PRIORITY) if ours else -1,
            idle_role=("embed" if embed else "chat") if ours else "",
        )

    # ----------------------------------------------------------------- misc

    def _record(self, consumer_id, label, action, freed, reason, automatic=True) -> None:
        entry = MemoryAction(at=time.time(), consumer_id=consumer_id, label=label, action=action,
                             freed=int(freed or 0), reason=reason, automatic=automatic)
        self._actions.appendleft(entry)
        self._emit(MemoryEvent.ACTION_TAKEN, MemoryPayload(action=entry))
        if automatic and self.bus is not None and hasattr(self.bus, "status_message_requested"):
            self._safe(lambda: self.bus.status_message_requested.emit(
                f"Memory: {action} {label} — {reason}", 6000))

    def _emit(self, event, payload) -> None:
        if self.bus is not None:
            self._safe(lambda: self.bus.memory_state_changed.emit(event, payload))

    @staticmethod
    def _safe(call) -> None:
        try:
            call()
        except RuntimeError:
            pass   # the bus is gone during shutdown


def _gb(value: float) -> str:
    return f"{value / 1024 ** 3:.2f} GB"
