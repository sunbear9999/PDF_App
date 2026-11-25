"""Where a mention sits in its sentence, and a short snippet of that sentence for list rows."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

SNIPPET_SIDE = 60   # characters shown on each side of the mention


def locate(mention: Dict[str, Any], sentence: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """The mention's [start, end) in the sentence text: from the shared block offsets, else the
    first occurrence of its words (sentences spanning two blocks)."""
    text, surface = sentence.get("exact_text") or "", mention.get("exact_text") or ""
    where, locator = sentence.get("locator") or {}, mention.get("locator") or {}
    if (surface and where.get("block_id") == locator.get("block_id")
            and isinstance(where.get("char_start"), int) and isinstance(locator.get("char_start"), int)):
        offset = locator["char_start"] - where["char_start"]
        if text[offset:offset + len(surface)] == surface:
            return offset, offset + len(surface)
    found = text.find(surface) if surface else -1
    return (found, found + len(surface)) if found >= 0 else None


def snippet(text: str, span: Optional[Tuple[int, int]]) -> str:
    """One line: about SNIPPET_SIDE characters either side of the mention, with ellipses."""
    text = " ".join((text or "").split()) if span is None else text
    if span is None:
        return text[:2 * SNIPPET_SIDE] + ("…" if len(text) > 2 * SNIPPET_SIDE else "")
    start, end = max(0, span[0] - SNIPPET_SIDE), min(len(text), span[1] + SNIPPET_SIDE)
    body = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + body + ("…" if end < len(text) else "")


def attach(db, mentions: Iterable[Dict[str, Any]]) -> None:
    """Add ``context_snippet`` to each mention from its evidence sentence (one batched read)."""
    mentions = list(mentions)
    ids = sorted({(m.get("metadata") or {}).get("evidence_artifact_id") for m in mentions} - {None, ""})
    sentences = {row["artifact_id"]: row for row in db.list_artifacts_by_ids(ids)} if ids else {}
    for mention in mentions:
        sentence = sentences.get((mention.get("metadata") or {}).get("evidence_artifact_id"))
        if sentence and sentence.get("exact_text"):
            mention["context_snippet"] = snippet(sentence["exact_text"], locate(mention, sentence))
