"""Short source labels in model context; the app keeps where each passage came from.

The model sees ``[S3] Title · p. 6 · Section`` above each passage and cites ``S3``. Source
ids, artifact ids and locators stay out of the prompt (they cost several hundred bytes
per passage and small models copy them badly) and are kept in run state under
``STATE_KEY``, keyed by label, so citations are restored from the label or the quote.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

STATE_KEY = "context_sources"
LABEL_PREFIX = "S"
HEADER = re.compile(r"^\[(" + LABEL_PREFIX + r"\d+)\]\s?(.*)$")
SECTION_PARTS_SHOWN = 2


def label(number: int) -> str:
    return f"{LABEL_PREFIX}{number}"


def next_number(sources: Optional[Dict[str, Any]]) -> int:
    """Labels stay unique across several searches in one run."""
    return len(sources or {}) + 1


def page_text(locator: Dict[str, Any]) -> str:
    page = (locator or {}).get("page")
    if isinstance(page, int):
        return f"p. {page + 1}"                 # stored pages are 0-based
    return f"p. {page}" if page not in (None, "") else ""


def header(item, tag: str, extra: str = "") -> str:
    section = list(item.section_path or [])[-SECTION_PARTS_SHOWN:]
    parts = [item.source_title or "", page_text(item.locator or {}), " > ".join(section), extra]
    return f"[{tag}] " + " · ".join(part for part in parts if part)


def record(item) -> Dict[str, Any]:
    meta = item.metadata or {}
    # Every locator field, including a plugin's own (jurisdiction, section): it is not in the prompt.
    locator = {key: value for key, value in (item.locator or {}).items() if value is not None}
    return {"doc_name": item.source_title or "", "source_type": meta.get("source_type") or item.artifact_type,
            "source_id": item.source_id or "", "artifact_id": item.artifact_id, "artifact_type": item.artifact_type,
            "page": locator.get("page"), "source_locator": locator}


def blocks(text: str, sources: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Labelled passages in ``text`` joined with their kept source records."""
    found: List[Dict[str, Any]] = []
    current = None
    for line in (text or "").splitlines():
        match = HEADER.match(line)
        if match:
            current = {"label": match.group(1), "header": match.group(2), "lines": []}
            found.append(current)
        elif current is not None:
            current["lines"].append(line)
    result = []
    for block in found:
        body = "\n".join(block["lines"]).strip() or block["header"]
        source = dict((sources or {}).get(block["label"]) or {})
        result.append({**source, "label": block["label"], "text": body,
                       "locator": source.get("source_locator") or {}})
    return result


def match(citation: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The passage a model citation points at: its label first, else its exact quote, else its document."""
    tag = str(citation.get("source") or citation.get("label") or "").strip().strip("[]")
    by_label = next((block for block in candidates if tag and block["label"] == tag), None)
    if by_label:
        return by_label
    quote = " ".join(str(citation.get("quote", "")).lower().split())
    doc_name = str(citation.get("doc_name", "")).strip().lower()

    def score(block):
        return (100 if quote and quote in " ".join(block["text"].lower().split()) else 0) + \
               (20 if doc_name and doc_name == str(block.get("doc_name", "")).lower() else 0)
    best = max(candidates, key=score, default=None)
    return best if best is not None and score(best) else None
