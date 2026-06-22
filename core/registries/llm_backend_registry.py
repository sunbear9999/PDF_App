"""
core/registries/llm_backend_registry.py

Registry of pluggable LLM backend specs.  Pure Python — no Qt, no GUI imports.

Usage (built-in registration in papyrus_core.py):
    registry = LLMBackendRegistry()
    registry.register(make_ollama_backend_spec())
    registry.register(make_llamacpp_backend_spec())

Plugin usage (in plugin on_load):
    api.llm_backends.register(BackendSpec(id="my_backend", ...))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.llm_manager import LLMProvider


@dataclass
class ModelInfo:
    """Metadata for a single model (downloadable or installed)."""
    id: str
    name: str
    size_gb: float
    description: str
    tags: List[str]             # e.g. ["chat", "embedding", "vision"]
    recommended: bool = False
    quantization: str = ""      # e.g. "Q4_K_M"
    requires_vram_gb: float = 0.0


@dataclass
class BackendSpec:
    """
    All information and callables needed to manage one LLM backend.

    Install/uninstall/pull_model callables receive a ``progress_cb(message, pct)``
    callback and must run synchronously (they are called from a background thread).
    """
    id: str
    name: str
    description: str
    homepage_url: str

    # --- Detection ---
    is_installed: Callable[[], bool]
    get_version: Callable[[], Optional[str]]

    # --- Installation (progress_cb: Callable[[str, int], None]) ---
    install: Callable[[Callable], None]
    uninstall: Callable[[], None]

    # --- Model management ---
    list_installed_models: Callable[[], List[ModelInfo]]
    catalog: List[ModelInfo]
    pull_model: Callable[[str, Callable], None]     # (model_id, progress_cb)
    remove_model: Callable[[str], None]

    # --- Provider factory ---
    create_provider: Callable[[], "LLMProvider"]

    # --- Optional ordering hint (lower = shown first) ---
    sort_order: int = 0

    # --- Post-install notes shown to the user in the settings tab ---
    install_notes: str = ""


class LLMBackendRegistry:
    """Ordered registry of BackendSpec objects."""

    def __init__(self) -> None:
        self._specs: dict[str, BackendSpec] = {}

    def register(self, spec: BackendSpec) -> None:
        self._specs[spec.id] = spec

    def get(self, backend_id: str) -> Optional[BackendSpec]:
        return self._specs.get(backend_id)

    def get_all(self) -> List[BackendSpec]:
        return sorted(self._specs.values(), key=lambda s: s.sort_order)

    def ids(self) -> List[str]:
        return [s.id for s in self.get_all()]
