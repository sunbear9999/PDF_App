"""
core/registries/ai_widget_registry.py

Central registry for AI output widget types.  Each AIWidgetDefinition
describes one widget format end-to-end:

  - The PromptManager key whose text is injected into the LLM system prompt
    so the model knows how to format its output for this widget.
  - A payload_parser callable that converts the raw LLM result string into
    the list of replayable payload dicts consumed by base_tab.receive_ai_payload().
  - Metadata (label, description, category, icon) used by the blueprint editor
    and prompt editor to present the widget to the user.

Built-in widgets are registered in ai_widget_definitions.py at startup.
Plugins register via  api.widgets.register(AIWidgetSpec(...)).

Widget IDs are kept identical to the existing ui_format strings so all
persisted blueprints and serialized DB payloads continue to work without
any migration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class AIWidgetDefinition:
    """Describes one AI output widget type — backend-safe (no Qt imports)."""

    # ---- Identity ----------------------------------------------------------
    widget_id: str
    """Unique key; equals the ui_format string used in ActionStep (e.g. 'live_stream')."""

    label: str
    """Human-readable name shown in the blueprint editor and prompt editor."""

    description: str = ""
    """One-sentence description shown below the format picker in the step inspector."""

    category: str = "Research"
    """Groups widgets in the picker.  Built-in categories: Research, Data, Analysis, System."""

    icon: str = "⬜"
    """Short emoji or text displayed alongside the label."""

    # ---- Prompt integration ------------------------------------------------
    prompt_key: str = ""
    """PromptManager key whose text is appended to the LLM system prompt for this format."""

    default_prompt: str = ""
    """Default prompt text — used by PromptManager.restore_default() and registered on startup."""

    # ---- Processing --------------------------------------------------------
    payload_parser: Optional[Callable] = None
    """
    Callable(result_str: str, *, step=None, trace_id=None, title=None) -> List[dict]
    Converts the raw LLM output string into replayable UI payload dicts.
    Must be thread-safe (called from QThread via MasterActionRunner).
    """

    auto_citations: bool = False
    """
    When True and RAG context is present, automatically inject the
    'Inline Citation Directive' prompt even if the step doesn't set
    inline_citations=True explicitly.
    """

    # ---- Metadata ----------------------------------------------------------
    plugin_id: Optional[str] = None
    """Set by api.widgets.register() for hot-reload cleanup via remove_by_plugin()."""

    tags: List[str] = field(default_factory=list)
    """Optional extra tags for future filtering."""


class AIWidgetRegistry:
    """
    Singleton registry mapping widget_id → AIWidgetDefinition.

    Thread-safety note: registrations happen only during app startup and plugin
    load (single-threaded); reads happen from any thread.  Python's GIL makes
    dict reads safe, but we hold a plain dict for simplicity.
    """

    _instance: Optional["AIWidgetRegistry"] = None

    def __init__(self) -> None:
        self._definitions: Dict[str, AIWidgetDefinition] = {}

    # ------------------------------------------------------------------ singleton
    @classmethod
    def get_instance(cls) -> "AIWidgetRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------ registration
    def register(self, defn: AIWidgetDefinition) -> None:
        """Register or replace a widget definition."""
        self._definitions[defn.widget_id] = defn

    def remove_by_plugin(self, plugin_id: str) -> None:
        """Remove all definitions contributed by a plugin (called on plugin unload)."""
        to_remove = [wid for wid, d in self._definitions.items() if d.plugin_id == plugin_id]
        for wid in to_remove:
            del self._definitions[wid]

    # ------------------------------------------------------------------ lookups
    def get(self, widget_id: str) -> Optional[AIWidgetDefinition]:
        return self._definitions.get(widget_id)

    def all(self) -> List[AIWidgetDefinition]:
        return list(self._definitions.values())

    def by_category(self) -> Dict[str, List[AIWidgetDefinition]]:
        """Return definitions grouped by category, preserving insertion order within each group."""
        result: Dict[str, List[AIWidgetDefinition]] = {}
        for defn in self._definitions.values():
            result.setdefault(defn.category, []).append(defn)
        return result

    def widget_ids(self) -> List[str]:
        return list(self._definitions.keys())

    def restorable_formats(self) -> set:
        """Dynamic equivalent of the old RESTORABLE_UI_FORMATS constant."""
        return {
            wid for wid, d in self._definitions.items()
            if d.payload_parser is not None
        }

    def __contains__(self, widget_id: str) -> bool:
        return widget_id in self._definitions
