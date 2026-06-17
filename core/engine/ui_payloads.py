import json

from core.utils.citation_utils import extract_inline_citations, strip_inline_citation_block
from core.utils.json_utils import extract_and_heal_json


RESTORABLE_UI_FORMATS = {
    "live_stream",
    "chat_widgets",
    "data_table",
    "card_grid",
    "nested_outline",
    "search_terms",
}


def build_ui_payloads(ui_format, result_str, *, step=None, trace_id=None, title=None):
    """Build replayable UI payloads from a blueprint step result."""
    payloads = []
    result_str = result_str or ""

    if ui_format == "nested_outline":
        success, parsed_data = extract_and_heal_json(result_str)
        outline_title = title or getattr(step, "ui_title", "AI Analysis")
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

    elif ui_format == "data_table":
        payloads.append({"type": "data_table", "content": result_str})

    elif ui_format == "card_grid":
        payloads.append({"type": "card_grid", "content": result_str})

    elif ui_format == "search_terms":
        success, items = extract_and_heal_json(result_str)
        payloads.append({
            "type": "search_terms",
            "items": items if success else result_str,
            "success": success,
        })

    elif ui_format == "chat_widgets":
        success, items = extract_and_heal_json(result_str)
        if success:
            payloads.append({
                "type": "citation_cards",
                "items": _normalize_items(items),
                "trace_id": trace_id,
            })

    elif ui_format == "live_stream":
        should_extract = getattr(step, "inline_citations", False) if step is not None else True
        success, citations = extract_inline_citations(result_str) if should_extract else (False, [])
        text = strip_inline_citation_block(result_str) if success else result_str
        payloads.append({"type": "replace_stream_text", "text": text})
        if success:
            payloads.append({
                "type": "citation_cards",
                "items": citations,
                "trace_id": trace_id,
            })

    if trace_id and ui_format in {"live_stream", "chat_widgets"}:
        payloads.append({"type": "prompt_trace_available", "trace_id": trace_id})

    if ui_format in {"live_stream", "chat_widgets"}:
        payloads.append({"type": "hide_status"})

    return payloads


def coerce_saved_payloads(raw_payloads):
    if not raw_payloads:
        return []
    if isinstance(raw_payloads, list):
        return [item for item in raw_payloads if isinstance(item, dict)]
    try:
        parsed = json.loads(raw_payloads)
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def serialize_payloads(payloads):
    if not payloads:
        return None
    return json.dumps(payloads)


def _normalize_items(items):
    if isinstance(items, dict):
        for val in items.values():
            if isinstance(val, list):
                items = val
                break
        if isinstance(items, dict):
            items = [items]
    return items if isinstance(items, list) else [items]
