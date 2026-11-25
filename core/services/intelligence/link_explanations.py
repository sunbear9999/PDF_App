"""Plain-language reasons for recorded links, shown wherever a link is reviewed.

Writers store ``metadata.reason = {rule, surfaces, cue_text, ...}`` on each edge
assertion. Older links have no reason; they say so honestly instead of guessing.
Plugins register explainers for their own relation types.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

Explainer = Callable[[Dict[str, Any], str, str], str]
_EXPLAINERS: Dict[str, tuple] = {}
NOT_RECORDED = " Its exact reason was not recorded by the older Entities version; rerun Entities to record it."


def register_explainer(edge_type: str, explainer: Explainer, plugin_id: Optional[str] = None) -> None:
    _EXPLAINERS[edge_type] = (explainer, plugin_id)


def remove_by_plugin(plugin_id: str) -> None:
    for key in [k for k, (_, owner) in _EXPLAINERS.items() if owner == plugin_id]:
        _EXPLAINERS.pop(key, None)


def explain(assertion: Dict[str, Any], source: Optional[Dict[str, Any]], target: Optional[Dict[str, Any]]) -> str:
    """One or two sentences: why the link exists and what confirming it means."""
    assertion = assertion or {}
    metadata = dict(assertion.get("metadata") or {})
    if isinstance(metadata.get("reason"), str):          # older writers stored a sentence
        metadata["reason"] = {"text": metadata["reason"]}
    edge_type = assertion.get("edge_type") or ""
    first, second = _name(source), _name(target)
    custom = _EXPLAINERS.get(edge_type)
    if custom:
        return custom[0](metadata, first, second)
    handler = _BUILTIN.get(edge_type)
    if handler:
        return handler(metadata, first, second)
    label = edge_type.replace("_", " ") or "link"
    recorded = (metadata.get("reason") or {}).get("text")
    return f"A recorded “{label}” link from {first} to {second}" + (f": {recorded}" if recorded else ", supported by the passage below.")


def _name(row) -> str:
    text = ((row or {}).get("exact_text") or "").strip().splitlines()
    return f"“{text[0][:80]}”" if text else "the item"


def _co_occurrence(metadata, first, second) -> str:
    text = (f"{first} and {second} are named in the same sentence, shown below. Being named together does not "
            "mean they are related: confirm only if the sentence states a real connection, otherwise reject.")
    return text if metadata.get("reason") else text + NOT_RECORDED


def _affiliation(metadata, first, second) -> str:
    cue = (metadata.get("reason") or {}).get("cue_text")
    lead = f"The word “{cue}” joins" if cue else "A word such as “of”, “at” or “from” joins"
    text = (f"{lead} the person {first} and the organisation {second} in the sentence below, so the person may "
            "belong to or work for it. Confirm if the sentence says so.")
    return text if metadata.get("reason") else text + NOT_RECORDED


def _same_as(metadata, first, second) -> str:
    score = metadata.get("score") or (metadata.get("reason") or {}).get("score")
    detail = f" (similarity {float(score):.2f})" if isinstance(score, (int, float)) else ""
    return (f"{first} and {second} look like the same entity written differently{detail}. "
            "Confirm to treat them as one; reject if they are different entities.")


def _argument(metadata, first, second) -> str:
    cue = metadata.get("marker") or metadata.get("cue") or (metadata.get("reason") or {}).get("cue_text")
    why = f" The marker “{cue}” suggested it." if isinstance(cue, str) and cue and cue != "same_sentence" else ""
    return f"A suggested argument link between {first} and {second}.{why} Confirm if the passage really argues this."


_BUILTIN = {"co_occurs_with": _co_occurrence, "affiliation_candidate": _affiliation,
            "same_as_candidate": _same_as, "supports": _argument, "attacks": _argument,
            "reasons_for": _argument, "limits": _argument, "qualifies": _argument}
