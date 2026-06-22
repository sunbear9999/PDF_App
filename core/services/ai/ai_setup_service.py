"""
core/services/ai/ai_setup_service.py

Central coordinator for AI backend and TTS setup.

Responsibilities:
- Startup detection of installed backends
- Runtime provider switching in LocalLLMManager
- Async install / download / remove operations (via QThread workers)
- Persisting default model selections
- Emitting EventBus signals for all state changes

The service is the ONLY place that mutates llm_manager.switch_provider().
It communicates with the GUI exclusively through EventBus signals.
"""
from __future__ import annotations

import json
import os
import traceback
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal


class _Worker(QThread):
    """Generic background worker that calls a callable and emits progress."""
    progress = Signal(str, int)   # message, percent
    finished = Signal()
    error = Signal(str)

    def __init__(self, fn: Callable, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self._fn()
            self.finished.emit()
        except Exception as exc:
            self.error.emit(f"{exc}\n{traceback.format_exc()}")


class AISetupService(QObject):
    """
    Manages LLM backend lifecycle and TTS voice setup.

    Wired in PapyrusCore.__init__() after all managers are created.
    ``startup_check()`` must be called once after construction.
    """

    def __init__(
        self,
        llm_backend_registry,
        llm_manager,
        user_data_dir: str,
        bus,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._registry = llm_backend_registry
        self._llm = llm_manager
        self._user_data_dir = user_data_dir
        self._bus = bus
        self._settings_path = os.path.join(user_data_dir, "ai_settings.json")
        self._settings: dict = self._load_settings()
        self._active_workers: set = set()

        # Subscribe to GUI-driven action requests
        self._bus.ai_setup_action_requested.connect(self._on_action_requested)
        # Global AI override: when True, ai_availability_changed stays False regardless of backend
        self._ai_disabled: bool = bool(self._settings.get("ai_disabled", False))

    # -----------------------------------------------------------------------
    # Startup
    # -----------------------------------------------------------------------

    def startup_check(self) -> None:
        """
        Detect installed backends, wire the first usable provider, emit
        ai_availability_changed.  Called synchronously at app startup.
        """
        import threading
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload

        preferred = self._settings.get("active_backend")
        specs = self._registry.get_all()

        # Try preferred backend first, then any installed backend
        ordered = []
        if preferred:
            spec = self._registry.get(preferred)
            if spec:
                ordered.append(spec)
        ordered += [s for s in specs if s.id != preferred]

        provider_set = False
        for spec in ordered:
            try:
                if spec.is_installed():
                    provider = spec.create_provider()
                    self._llm.switch_provider(provider)
                    self._settings["active_backend"] = spec.id
                    provider_set = True

                    # Backends that start asynchronously (e.g. llama-server) report
                    # ai_enabled=False immediately.  Watch in background and re-emit
                    # ai_availability_changed once the server is healthy.
                    if hasattr(provider, "wait_for_ready") and not provider.ai_enabled:
                        def _watch(p=provider, bid=spec.id):
                            ready = p.wait_for_ready(timeout=120)
                            if ready and not self._ai_disabled:
                                self._bus.ai_availability_changed.emit(True)
                                self._bus.ai_setup_state_changed.emit(
                                    AISetupEvent.BACKEND_STATUS_CHANGED,
                                    AISetupPayload(backend_id=bid, message="startup_check_complete"),
                                )
                        threading.Thread(target=_watch, daemon=True, name="startup-readiness").start()

                    break
            except Exception as exc:
                print(f"[AISetupService] Could not start backend '{spec.id}': {exc}")

        available = provider_set and self._llm.ai_enabled and not self._ai_disabled
        self._bus.ai_availability_changed.emit(available)
        self._bus.ai_setup_state_changed.emit(
            AISetupEvent.BACKEND_STATUS_CHANGED,
            AISetupPayload(message="startup_check_complete"),
        )
        self._bus.ai_setup_state_changed.emit(
            AISetupEvent.AI_TOGGLE_CHANGED,
            AISetupPayload(data={"enabled": not self._ai_disabled}),
        )

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    def is_ai_available(self) -> bool:
        return (not self._ai_disabled) and self._llm.ai_enabled

    def is_ai_disabled_by_user(self) -> bool:
        return self._ai_disabled

    def get_hardware_info(self) -> dict:
        import subprocess
        info: dict = {"ram_gb": 0, "gpu_name": "Not detected", "vram_gb": 0}

        # RAM — try psutil first, fall back to /proc/meminfo (Linux)
        try:
            import psutil
            info["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        except Exception:
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            info["ram_gb"] = round(kb / (1024 ** 2), 1)
                            break
            except Exception:
                pass

        # GPU — NVIDIA via nvidia-smi
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(",")
                if len(parts) >= 2:
                    info["gpu_name"] = parts[0].strip()
                    info["vram_gb"] = round(float(parts[1].strip()) / 1024, 1)
        except Exception:
            pass

        # GPU — AMD via rocm-smi (with VRAM)
        if info["gpu_name"] == "Not detected":
            try:
                result = subprocess.run(
                    ["rocm-smi", "--showproductname", "--showmeminfo", "vram"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        if "GPU" in line and ":" in line:
                            info["gpu_name"] = line.split(":", 1)[-1].strip()
                        if "vram" in line.lower() and "total" in line.lower():
                            try:
                                mb = int("".join(filter(str.isdigit, line.split()[-1])))
                                info["vram_gb"] = round(mb / 1024, 1)
                            except Exception:
                                pass
            except Exception:
                pass

        # GPU — fallback: lspci (name only, no VRAM info)
        if info["gpu_name"] == "Not detected":
            try:
                result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        low = line.lower()
                        if any(k in low for k in ("vga", "3d controller", "display controller")):
                            # Strip the PCI address prefix, keep the description
                            desc = line.split(":", 2)[-1].strip()
                            info["gpu_name"] = desc
                            break
            except Exception:
                pass

        return info

    def get_default_chat_model(self) -> str:
        return self._settings.get("default_chat_model", "")

    def get_default_embedding_model(self) -> str:
        return self._settings.get("default_embedding_model", "nomic-embed-text")

    def get_active_backend_id(self) -> str:
        return self._settings.get("active_backend", "")

    # -----------------------------------------------------------------------
    # Intent dispatch (from EventBus)
    # -----------------------------------------------------------------------

    def _on_action_requested(self, intent, payload) -> None:
        from core.events.domains.ai_setup_events import AISetupIntent

        if intent == AISetupIntent.INSTALL_BACKEND:
            self._async_install_backend(payload.backend_id)
        elif intent == AISetupIntent.UNINSTALL_BACKEND:
            self._async_uninstall_backend(payload.backend_id)
        elif intent == AISetupIntent.PULL_MODEL:
            self._async_pull_model(payload.backend_id, payload.model_id)
        elif intent == AISetupIntent.REMOVE_MODEL:
            self._remove_model(payload.backend_id, payload.model_id)
        elif intent == AISetupIntent.PULL_TTS_VOICE:
            self._async_pull_tts_voice(payload.voice_id)
        elif intent == AISetupIntent.REMOVE_TTS_VOICE:
            self._remove_tts_voice(payload.voice_id)
        elif intent == AISetupIntent.SET_ACTIVE_BACKEND:
            self._async_set_active_backend(payload.backend_id)
        elif intent == AISetupIntent.SET_DEFAULT_MODEL:
            self._set_default_model(payload.model_id, payload.data or {})
        elif intent == AISetupIntent.REFRESH:
            self._emit_full_refresh()
        elif intent == AISetupIntent.TOGGLE_AI:
            self._toggle_ai(bool((payload.data or {}).get("enabled", True)))

    # -----------------------------------------------------------------------
    # Backend installation
    # -----------------------------------------------------------------------

    def _async_install_backend(self, backend_id: str) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload

        spec = self._registry.get(backend_id)
        if not spec:
            return

        def _progress(msg: str, pct: int) -> None:
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.PROGRESS,
                AISetupPayload(backend_id=backend_id, message=msg, pct=pct),
            )

        def _run():
            spec.install(_progress)

        worker = _Worker(_run, self)

        def _on_done():
            try:
                if spec.is_installed():
                    self._async_set_active_backend(backend_id)
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.BACKEND_STATUS_CHANGED,
                    AISetupPayload(backend_id=backend_id, message="installed"),
                )
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.OPERATION_COMPLETE,
                    AISetupPayload(backend_id=backend_id, message=f"{spec.name} installed successfully."),
                )
            finally:
                self._active_workers.discard(worker)

        def _on_err(msg: str):
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.OPERATION_FAILED,
                AISetupPayload(backend_id=backend_id, message=msg),
            )
            self._active_workers.discard(worker)

        worker.finished.connect(_on_done)
        worker.error.connect(_on_err)
        self._active_workers.add(worker)
        worker.start()

    def _async_uninstall_backend(self, backend_id: str) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload

        spec = self._registry.get(backend_id)
        if not spec:
            return

        def _progress(msg: str, pct: int) -> None:
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.PROGRESS,
                AISetupPayload(backend_id=backend_id, message=msg, pct=pct),
            )

        def _run():
            spec.uninstall(_progress)

        worker = _Worker(_run, self)

        def _on_done():
            try:
                if self._settings.get("active_backend") == backend_id:
                    self._settings.pop("active_backend", None)
                    self._save_settings()
                    self.startup_check()
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.BACKEND_STATUS_CHANGED,
                    AISetupPayload(backend_id=backend_id, message="uninstalled"),
                )
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.OPERATION_COMPLETE,
                    AISetupPayload(backend_id=backend_id, message=f"{spec.name} uninstalled."),
                )
            finally:
                self._active_workers.discard(worker)

        worker.finished.connect(_on_done)
        def _on_err_uninst(msg: str):
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.OPERATION_FAILED,
                AISetupPayload(backend_id=backend_id, message=msg),
            )
            self._active_workers.discard(worker)

        worker.error.connect(_on_err_uninst)
        self._active_workers.add(worker)
        worker.start()

    def _async_set_active_backend(self, backend_id: str) -> None:
        """Switch backends in a background thread to avoid freezing the GUI."""
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload

        spec = self._registry.get(backend_id)
        if not spec or not spec.is_installed():
            return

        def _progress(msg: str, pct: int) -> None:
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.PROGRESS,
                AISetupPayload(backend_id=backend_id, message=msg, pct=pct),
            )

        def _run() -> None:
            _progress("Releasing current backend…", 5)
            # Free existing provider's models from RAM/VRAM BEFORE loading the new one
            self._llm.release_provider()

            _progress(f"Starting {spec.name}…", 20)
            provider = spec.create_provider()

            # Wait for backends that need startup time (e.g. llama-server)
            if hasattr(provider, "wait_for_ready"):
                _progress(f"Loading model into {spec.name}…", 40)
                ready = provider.wait_for_ready(timeout=120)
                if not ready:
                    raise RuntimeError(
                        f"{spec.name} did not become ready within 120s. "
                        "Check that you have downloaded a model and have enough free RAM."
                    )

            _progress("Activating…", 90)
            self._llm.switch_provider(provider)
            self._settings["active_backend"] = backend_id
            self._save_settings()

        worker = _Worker(_run, self)

        def _on_done() -> None:
            try:
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.BACKEND_STATUS_CHANGED,
                    AISetupPayload(backend_id=backend_id, message="active"),
                )
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.OPERATION_COMPLETE,
                    AISetupPayload(backend_id=backend_id, message=f"Switched to {spec.name}."),
                )
            finally:
                self._active_workers.discard(worker)

        def _on_err(msg: str) -> None:
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.OPERATION_FAILED,
                AISetupPayload(backend_id=backend_id, message=msg),
            )
            self._active_workers.discard(worker)

        worker.finished.connect(_on_done)
        worker.error.connect(_on_err)
        self._active_workers.add(worker)
        worker.start()

    def _toggle_ai(self, enabled: bool) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload

        self._ai_disabled = not enabled
        self._settings["ai_disabled"] = self._ai_disabled
        self._save_settings()

        available = enabled and self._llm.ai_enabled
        self._bus.ai_availability_changed.emit(available)
        self._bus.ai_setup_state_changed.emit(
            AISetupEvent.AI_TOGGLE_CHANGED,
            AISetupPayload(data={"enabled": enabled}),
        )

    # -----------------------------------------------------------------------
    # Model management
    # -----------------------------------------------------------------------

    def _async_pull_model(self, backend_id: str, model_id: str) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload

        spec = self._registry.get(backend_id)
        if not spec:
            return

        def _progress(msg: str, pct: int) -> None:
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.PROGRESS,
                AISetupPayload(backend_id=backend_id, model_id=model_id, message=msg, pct=pct),
            )

        def _run():
            spec.pull_model(model_id, _progress)

        worker = _Worker(_run, self)

        def _on_done():
            try:
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.MODEL_LIST_CHANGED,
                    AISetupPayload(backend_id=backend_id, model_id=model_id),
                )
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.OPERATION_COMPLETE,
                    AISetupPayload(backend_id=backend_id, model_id=model_id, message=f"{model_id} downloaded."),
                )
            finally:
                self._active_workers.discard(worker)

        def _on_err(msg: str):
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.OPERATION_FAILED,
                AISetupPayload(backend_id=backend_id, model_id=model_id, message=msg),
            )
            self._active_workers.discard(worker)

        worker.finished.connect(_on_done)
        worker.error.connect(_on_err)
        self._active_workers.add(worker)
        worker.start()

    def _remove_model(self, backend_id: str, model_id: str) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload

        spec = self._registry.get(backend_id)
        if not spec:
            return
        try:
            spec.remove_model(model_id)
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.MODEL_LIST_CHANGED,
                AISetupPayload(backend_id=backend_id, model_id=model_id),
            )
        except Exception as exc:
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.OPERATION_FAILED,
                AISetupPayload(backend_id=backend_id, model_id=model_id, message=str(exc)),
            )

    def _set_default_model(self, model_id: str, opts: dict) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload

        model_type = opts.get("type", "chat")
        key = "default_chat_model" if model_type == "chat" else "default_embedding_model"
        self._settings[key] = model_id
        self._save_settings()
        self._bus.active_model_changed.emit(key, model_id)
        self._bus.ai_setup_state_changed.emit(
            AISetupEvent.DEFAULTS_CHANGED,
            AISetupPayload(model_id=model_id, data={"type": model_type}),
        )

    # -----------------------------------------------------------------------
    # TTS voice management
    # -----------------------------------------------------------------------

    def _async_pull_tts_voice(self, voice_id: str) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload
        from core.backends.tts_backend import pull_voice

        def _progress(msg: str, pct: int) -> None:
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.PROGRESS,
                AISetupPayload(voice_id=voice_id, message=msg, pct=pct),
            )

        def _run():
            pull_voice(voice_id, _progress, self._user_data_dir)

        worker = _Worker(_run, self)

        def _on_done():
            try:
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.TTS_VOICE_LIST_CHANGED,
                    AISetupPayload(voice_id=voice_id),
                )
                self._bus.ai_setup_state_changed.emit(
                    AISetupEvent.OPERATION_COMPLETE,
                    AISetupPayload(voice_id=voice_id, message=f"Voice '{voice_id}' downloaded."),
                )
            finally:
                self._active_workers.discard(worker)

        def _on_err(msg: str):
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.OPERATION_FAILED,
                AISetupPayload(voice_id=voice_id, message=msg),
            )
            self._active_workers.discard(worker)

        worker.finished.connect(_on_done)
        worker.error.connect(_on_err)
        self._active_workers.add(worker)
        worker.start()

    def _remove_tts_voice(self, voice_id: str) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload
        from core.backends.tts_backend import remove_voice

        try:
            remove_voice(voice_id, self._user_data_dir)
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.TTS_VOICE_LIST_CHANGED,
                AISetupPayload(voice_id=voice_id),
            )
        except Exception as exc:
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.OPERATION_FAILED,
                AISetupPayload(voice_id=voice_id, message=str(exc)),
            )

    # -----------------------------------------------------------------------
    # Full refresh (re-emit all status)
    # -----------------------------------------------------------------------

    def _emit_full_refresh(self) -> None:
        from core.events.domains.ai_setup_events import AISetupEvent, AISetupPayload

        for spec in self._registry.get_all():
            self._bus.ai_setup_state_changed.emit(
                AISetupEvent.BACKEND_STATUS_CHANGED,
                AISetupPayload(backend_id=spec.id, message="refresh"),
            )
        self._bus.ai_setup_state_changed.emit(
            AISetupEvent.MODEL_LIST_CHANGED,
            AISetupPayload(message="refresh"),
        )
        self._bus.ai_setup_state_changed.emit(
            AISetupEvent.TTS_VOICE_LIST_CHANGED,
            AISetupPayload(message="refresh"),
        )

    # -----------------------------------------------------------------------
    # Settings persistence
    # -----------------------------------------------------------------------

    def _load_settings(self) -> dict:
        if os.path.exists(self._settings_path):
            try:
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_settings(self) -> None:
        os.makedirs(os.path.dirname(self._settings_path), exist_ok=True)
        try:
            with open(self._settings_path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2)
        except Exception as exc:
            print(f"[AISetupService] Failed to save settings: {exc}")
