"""
core/registries/ai_widget_definitions.py

Registers all built-in AI output widget types into the AIWidgetRegistry and
seeds their PromptManager entries.

Each parser function is the canonical implementation for converting a raw LLM
output string into a list of replayable UI payload dicts.  They were previously
inline branches inside build_ui_payloads(); keeping them here means the logic
lives in exactly one place.

Call register_builtin_widgets(registry, prompt_manager) once during app startup
(in PapyrusCore.__init__) before any workflow runs.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from core.registries.ai_widget_registry import AIWidgetRegistry
    from core.prompt_manager import PromptManager


# ---------------------------------------------------------------------------
# Shared helpers (previously private in ui_payloads.py — kept as module-level
# so ai_widget_definitions and ui_payloads can both import them)
# ---------------------------------------------------------------------------

def _normalize_items(items):
    """Normalise a parsed JSON value into a flat list of dicts for citation cards."""
    if isinstance(items, dict):
        for val in items.values():
            if isinstance(val, list):
                items = val
                break
        if isinstance(items, dict):
            items = [items]
    return items if isinstance(items, list) else [items]


# ---------------------------------------------------------------------------
# Payload parsers — one per widget type
# ---------------------------------------------------------------------------

def _parse_live_stream(result_str: str, *, step=None, trace_id: Optional[str] = None, title: Optional[str] = None) -> List[dict]:
    from core.utils.citation_utils import extract_inline_citations, strip_inline_citation_block
    from core.utils.json_utils import strip_tagged_block

    result_str = strip_tagged_block(result_str, "UPDATE_MANIFEST")
    should_extract = getattr(step, "inline_citations", False) if step is not None else True
    success, citations = extract_inline_citations(result_str) if should_extract else (False, [])
    text = strip_inline_citation_block(result_str) if success else result_str
    payloads: List[dict] = [{"type": "replace_stream_text", "text": text}]
    if success:
        payloads.append({"type": "citation_cards", "items": citations, "trace_id": trace_id})
    if trace_id:
        payloads.append({"type": "prompt_trace_available", "trace_id": trace_id})
    payloads.append({"type": "hide_status"})
    return payloads


def _parse_chat_widgets(result_str: str, *, step=None, trace_id: Optional[str] = None, title: Optional[str] = None) -> List[dict]:
    from core.utils.citation_utils import extract_inline_citations
    from core.utils.json_utils import extract_and_heal_json

    success, items = extract_and_heal_json(result_str)
    if not success:
        success, items = extract_inline_citations(result_str)
    payloads: List[dict] = []
    if success:
        payloads.append({"type": "citation_cards", "items": _normalize_items(items), "trace_id": trace_id})
    if trace_id:
        payloads.append({"type": "prompt_trace_available", "trace_id": trace_id})
    payloads.append({"type": "hide_status"})
    return payloads


def _parse_data_table(result_str: str, *, step=None, trace_id: Optional[str] = None, title: Optional[str] = None) -> List[dict]:
    return [{"type": "data_table", "content": result_str}]


def _parse_card_grid(result_str: str, *, step=None, trace_id: Optional[str] = None, title: Optional[str] = None) -> List[dict]:
    return [{"type": "card_grid", "content": result_str}]


def _parse_nested_outline(result_str: str, *, step=None, trace_id: Optional[str] = None, title: Optional[str] = None) -> List[dict]:
    from core.utils.json_utils import extract_and_heal_json

    outline_title = title or getattr(step, "ui_title", "AI Analysis")
    success, parsed_data = extract_and_heal_json(result_str)
    payloads: List[dict] = []
    if success and isinstance(parsed_data, list):
        for i, item in enumerate(parsed_data):
            item_str = json.dumps(item)
            payloads.append({
                "type": "outline",
                "title": f"{outline_title} (Part {i + 1})",
                "content": item_str,
                "raw_ai_data": item_str,
            })
    else:
        payloads.append({
            "type": "outline",
            "title": outline_title,
            "content": result_str,
            "raw_ai_data": result_str,
        })
    return payloads


def _parse_search_terms(result_str: str, *, step=None, trace_id: Optional[str] = None, title: Optional[str] = None) -> List[dict]:
    from core.utils.json_utils import extract_and_heal_json

    success, items = extract_and_heal_json(result_str)
    return [{"type": "search_terms", "items": items if success else result_str, "success": success}]


def _parse_bias_metrics(result_str: str, *, step=None, trace_id: Optional[str] = None, title: Optional[str] = None) -> List[dict]:
    from core.utils.json_utils import extract_and_heal_json

    success, parsed_data = extract_and_heal_json(result_str)
    return [{"type": "bias_metrics", "metrics": parsed_data if success else {}}]


# ---------------------------------------------------------------------------
# Default prompt text for new keys that don't already exist in PromptManager
# ---------------------------------------------------------------------------

_NEW_DEFAULT_PROMPTS = {
    "Widget - Citation Step": (
        "You extract source citations. For every verifiable claim in ANSWER supported, qualified, or contradicted by SOURCE: "
        "output one citation object. Rules: "
        "(1) quote is exact verbatim text copied from SOURCE — never paraphrase. "
        "(2) note names the specific ANSWER claim this quote proves. "
        "(3) copy doc_name, source_type, source_id, and source_locator exactly from the CITATION_* and CITATION_LOCATOR_* metadata lines of the matching SOURCE block — never invent them. "
        "(4) no duplicates. "
        "(5) no topical quotes that do not back a specific claim. "
        "Return the minimum complete set that covers all supported claims."
    ),
    "Format Enforcer - Nested Outline": (
        "CRITICAL: Your output must be a JSON array of nested outline objects. "
        "Each top-level item represents a major section with a 'title' string and an 'items' array. "
        "Items can nest further using the same 'title'/'items' structure. "
        "Do not include any text outside the JSON array."
    ),
    "Format Enforcer - Search Terms": (
        "CRITICAL: Your output must be a JSON array of search term objects. "
        "Each object must have a 'term' key (the search query) and a 'description' key "
        "(one sentence explaining why this search is useful). "
        "Output ONLY the JSON array. No markdown, no explanation."
    ),
    "Format Enforcer - Bias Metrics": (
        "CRITICAL: Your output must be a JSON object with bias metric keys. "
        "Each key is a bias dimension (e.g. 'political_lean', 'emotional_tone', 'source_diversity') "
        "and its value is an object with 'score' (0.0–1.0), 'direction' (string), "
        "and 'explanation' (one sentence). Output ONLY the JSON object."
    ),
}


# ---------------------------------------------------------------------------
# Registration entry point
# ---------------------------------------------------------------------------

def register_builtin_widgets(registry: "AIWidgetRegistry", prompt_manager: Optional["PromptManager"] = None) -> None:
    """
    Register all built-in AI widget definitions.

    Also seeds any missing PromptManager DEFAULT_PROMPTS entries so that
    restore-to-default works for new keys introduced by this module.

    Call once from PapyrusCore.__init__ after PromptManager is initialised.
    """
    from core.registries.ai_widget_registry import AIWidgetDefinition

    # Seed new default prompts into PromptManager so restore-to-default works.
    if prompt_manager is not None:
        for key, text in _NEW_DEFAULT_PROMPTS.items():
            if key not in prompt_manager.DEFAULT_PROMPTS:
                prompt_manager.DEFAULT_PROMPTS[key] = text

    # --- Research category --------------------------------------------------
    registry.register(AIWidgetDefinition(
        widget_id="live_stream",
        label="Live Stream",
        description="Streams the LLM response as readable Markdown. Inline citations appear as note bubbles below the text when a RAG source is present.",
        category="Research",
        icon="📡",
        prompt_key="Format Enforcer - Live Stream",
        default_prompt="Format the visible answer as readable Markdown. Use short paragraphs, headings, and lists where they improve clarity.",
        payload_parser=_parse_live_stream,
    ))

    registry.register(AIWidgetDefinition(
        widget_id="chat_widgets",
        label="Citation Bubbles",
        description="The LLM outputs a JSON array of verbatim quotes with document names and notes. Each quote is rendered as an interactive note bubble.",
        category="Research",
        icon="💬",
        prompt_key="Format Enforcer - Chat Widgets",
        default_prompt="CRITICAL: You are extracting exact citations. Your output must strictly represent verbatim quotes from the source text. Provide the exact 'quote', the 'doc_name', and a brief 'note' explaining its relevance.",
        payload_parser=_parse_chat_widgets,
        auto_citations=True,
    ))

    registry.register(AIWidgetDefinition(
        widget_id="search_terms",
        label="Search Terms",
        description="The LLM generates a JSON list of academic search queries. Each term is shown as a card with external search engine buttons.",
        category="Research",
        icon="🔍",
        prompt_key="Format Enforcer - Search Terms",
        default_prompt=_NEW_DEFAULT_PROMPTS["Format Enforcer - Search Terms"],
        payload_parser=_parse_search_terms,
    ))

    # --- Data category ------------------------------------------------------
    registry.register(AIWidgetDefinition(
        widget_id="data_table",
        label="Data Table",
        description="The LLM outputs a uniform JSON array of flat objects rendered as a spreadsheet-style table.",
        category="Data",
        icon="📊",
        prompt_key="Format Enforcer - Data Table",
        default_prompt="CRITICAL: Your output must be a uniform array of flat JSON objects suitable for a spreadsheet. Do not nest arrays or objects within the rows. Keep keys consistent across all objects.",
        payload_parser=_parse_data_table,
    ))

    registry.register(AIWidgetDefinition(
        widget_id="card_grid",
        label="Card Grid",
        description="The LLM outputs a JSON array of objects displayed as a responsive grid of summary cards.",
        category="Data",
        icon="🗂️",
        prompt_key="Format Enforcer - Card Grid",
        default_prompt="CRITICAL: Your output must be an array of objects. Make the first key a concise 'title' for the card, followed by relevant summary keys.",
        payload_parser=_parse_card_grid,
    ))

    registry.register(AIWidgetDefinition(
        widget_id="nested_outline",
        label="Nested Outline",
        description="The LLM outputs a hierarchical JSON structure displayed as a collapsible interactive outline with clickable RAG search nodes.",
        category="Data",
        icon="📋",
        prompt_key="Format Enforcer - Nested Outline",
        default_prompt=_NEW_DEFAULT_PROMPTS["Format Enforcer - Nested Outline"],
        payload_parser=_parse_nested_outline,
    ))

    # --- Analysis category --------------------------------------------------
    registry.register(AIWidgetDefinition(
        widget_id="bias_metrics",
        label="Bias Metrics",
        description="The LLM scores the text on bias dimensions (e.g. political lean, emotional tone) and results are shown as scored metric bars.",
        category="Analysis",
        icon="⚖️",
        prompt_key="Format Enforcer - Bias Metrics",
        default_prompt=_NEW_DEFAULT_PROMPTS["Format Enforcer - Bias Metrics"],
        payload_parser=_parse_bias_metrics,
    ))

    # --- System category ----------------------------------------------------
    registry.register(AIWidgetDefinition(
        widget_id="silent",
        label="Silent (no output widget)",
        description="The LLM result is stored in the workflow state but not displayed. Use for intermediate computation steps.",
        category="System",
        icon="🔇",
        prompt_key="",
        payload_parser=None,
    ))
