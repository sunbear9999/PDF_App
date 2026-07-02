"""
core/engine/ui_payloads.py

Converts raw LLM result strings into replayable UI payload dicts.

build_ui_payloads() is now a thin dispatcher that delegates all format-specific
parsing to the AIWidgetRegistry.  Each AIWidgetDefinition registered there
carries its own payload_parser callable, extracted from the per-format branches
that previously lived in this file.

Backward-compatibility notes
─────────────────────────────
• All payload dict 'type' keys ('citation_cards', 'data_table', etc.) are
  unchanged — serialised DB payloads replay without any migration.
• RESTORABLE_UI_FORMATS is now derived from the registry at call time so that
  plugin-registered widget types are automatically included.
• _normalize_items() remains here as a shared utility used by parsers in
  ai_widget_definitions.py (imported from there via the re-export below).
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from core.utils.citation_utils import extract_inline_citations, strip_inline_citation_block
from core.utils.json_utils import extract_and_heal_json, strip_tagged_block


# ---------------------------------------------------------------------------
# Shared helpers — re-exported for callers that still import from here
# ---------------------------------------------------------------------------

def _normalize_items(items):
    """Normalise a parsed JSON value into a flat list of dicts."""
    if isinstance(items, dict):
        for val in items.values():
            if isinstance(val, list):
                items = val
                break
        if isinstance(items, dict):
            items = [items]
    return items if isinstance(items, list) else [items]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_ui_payloads(
    ui_format: str,
    result_str: str,
    *,
    step=None,
    trace_id: Optional[str] = None,
    title: Optional[str] = None,
) -> List[dict]:
    """Build replayable UI payloads from a blueprint step result.

    Delegates to the parser registered for *ui_format* in AIWidgetRegistry.
    If no parser is registered (e.g. 'silent' or an unknown format) an empty
    list is returned.
    """
    from core.registries.ai_widget_registry import AIWidgetRegistry
    registry = AIWidgetRegistry.get_instance()
    defn = registry.get(ui_format)
    if defn is not None and defn.payload_parser is not None:
        return defn.payload_parser(result_str or "", step=step, trace_id=trace_id, title=title)
    return []


def restorable_ui_formats() -> set:
    """Return the set of ui_format strings whose payloads can be replayed from DB."""
    from core.registries.ai_widget_registry import AIWidgetRegistry
    return AIWidgetRegistry.get_instance().restorable_formats()


# Keep the old constant as a module-level attribute for any code that does
#   from core.engine.ui_payloads import RESTORABLE_UI_FORMATS
# At import time the registry may not be fully populated yet, so we expose a
# sentinel set of the known built-in values.  For runtime checks use
# restorable_ui_formats() instead.
RESTORABLE_UI_FORMATS = {
    "live_stream",
    "chat_widgets",
    "data_table",
    "card_grid",
    "nested_outline",
    "search_terms",
    "bias_metrics",
}


def coerce_saved_payloads(raw_payloads: Any) -> List[dict]:
    if not raw_payloads:
        return []
    if isinstance(raw_payloads, list):
        return [item for item in raw_payloads if isinstance(item, dict)]
    try:
        parsed = json.loads(raw_payloads)
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def serialize_payloads(payloads: List[dict]) -> Optional[str]:
    if not payloads:
        return None
    return json.dumps(payloads)
