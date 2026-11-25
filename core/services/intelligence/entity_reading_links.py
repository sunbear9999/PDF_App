"""Entity mentions as reading links: hover a name to see its entity, jump to the next mention.

Each mention on the requested pages becomes a hit of kind ``entity``. Its card
shows the entity's type, name, other spellings, how often it occurs here and in
how many sources, and why it got its type. "Go to" moves to the next mention of
the same entity in this document (wrapping to the first), so repeated hovers and
clicks walk through every mention. Geometry is read once per page and once per
block, because a dense page has dozens of mentions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.services.extraction.pdf_layout_parser import block_char_boxes, merge_line_rects, page_layout, parse_block_id
from core.models.entity_categories import TYPE_LABELS
from core.services.intelligence.mention_coordinates import mention_spans

MAX_ALIASES = 6
CITED_PREVIEW = 120
REASONS = {"usage": "how the document uses the name", "dominant_type": "its usual type in this document",
           "nominal_coref": "it refers back to the name"}


def entity_links(db, document, source_id: str, pages, blocks: Dict[str, Any]) -> List[Dict[str, Any]]:
    mentions = db.document_entity_mentions(source_id, pages)
    if not mentions:
        return []
    sentences = {row["artifact_id"]: row for row in db.list_artifacts_by_ids(
        sorted({m["metadata"].get("evidence_artifact_id") for m in mentions} - {None}))}
    canonicals = {row["artifact_id"]: row for row in db.list_artifacts_by_ids(
        sorted({m["parent_artifact_id"] for m in mentions} - {None}))}
    geometry = _Geometry(document, blocks)
    from core.services.intelligence.entity_citations import cited_entries
    cited = cited_entries(db, sorted(canonicals))
    from core.services.intelligence.entity_hierarchy import parents
    above = parents(db, sorted(canonicals))
    order = _mention_order(db, source_id, canonicals)
    hits = []
    for mention in mentions:
        canonical = canonicals.get(mention["parent_artifact_id"])
        sentence = sentences.get(mention["metadata"].get("evidence_artifact_id"))
        if canonical is None or sentence is None:
            continue
        rects = geometry.rects(mention_spans(sentence, mention["locator"], blocks.get))
        if not rects:
            continue
        rows = order.get(canonical["artifact_id"], [])
        target, previous = _step(rows, mention["artifact_id"], blocks, 1), _step(rows, mention["artifact_id"], blocks, -1)
        hits.append({"page": mention["locator"].get("page"), "rects": rects, "kind": "entity",
                     "label": _label(canonical), "source_artifact_id": mention["artifact_id"],
                     "target_artifact_id": canonical["artifact_id"],
                     "preview_text": _card(canonical, mention, order.get(canonical["artifact_id"], []),
                                           cited.get(canonical["artifact_id"]), above.get(canonical["artifact_id"])),
                     "target_page": target[0], "target_rects": target[1],
                     "previous_page": previous[0], "previous_rects": previous[1],
                     "next_mention_id": target[2], "previous_mention_id": previous[2],
                     "cited_entry_id": (cited.get(canonical["artifact_id"]) or {}).get("artifact_id")})
    return hits


class _Geometry:
    def __init__(self, document, blocks) -> None:
        self.document, self.blocks = document, blocks
        self._pages: Dict[int, Any] = {}
        self._boxes: Dict[str, Optional[tuple]] = {}

    def rects(self, spans) -> List[List[float]]:
        out = []
        for span in spans:
            boxes = self._block(span["block_id"])
            if boxes is None:
                return []
            out.extend(merge_line_rects(boxes[span["char_start"]:span["char_end"]]))
        return out

    def _block(self, block_id: str):
        if block_id not in self._boxes:
            self._boxes[block_id] = self._load(block_id)
        return self._boxes[block_id]

    def _load(self, block_id: str):
        block, parsed = self.blocks.get(block_id), parse_block_id(block_id or "")
        page = getattr(block, "page_num", None)
        if block is None or parsed is None or not isinstance(page, int) or not 0 <= page < len(self.document):
            return None
        if page not in self._pages:
            self._pages[page] = page_layout(self.document[page])
        found = block_char_boxes(self.document[page], parsed[1], self._pages[page])
        # Text must equal the stored block text, or the offsets would point at the wrong characters.
        return found[1] if found and found[0] == block.exact_text else None


def _mention_order(db, source_id: str, canonicals) -> Dict[str, List[Dict[str, Any]]]:
    """This document's mentions of each entity on these pages, in reading order (ids and positions only)."""
    order: Dict[str, List[Dict[str, Any]]] = {identity: [] for identity in canonicals}
    for row in db.document_entity_positions(source_id, sorted(canonicals)):
        order[row["parent_artifact_id"]].append(row)
    return {identity: sorted(rows, key=_reading_key) for identity, rows in order.items()}


def _reading_key(row) -> Tuple:
    locator = row.get("locator") or {}
    parsed = parse_block_id(str(locator.get("block_id") or "")) or (0, 0)
    return (locator.get("page") or 0, parsed[1], str(locator.get("sentence_id") or ""), locator.get("char_start") or 0)


def _step(rows, mention_id: str, blocks, step: int) -> Tuple[Optional[int], List[List[float]], str]:
    """The following (``step`` 1) or preceding (-1) mention, wrapping; its block outline is the target."""
    ids = [row["artifact_id"] for row in rows]
    if not ids:
        return None, [], ""
    index = (ids.index(mention_id) + step) % len(ids) if mention_id in ids else 0
    locator = rows[index].get("locator") or {}
    block = blocks.get(locator.get("block_id"))
    bbox = list(getattr(block, "bbox", None) or [])
    return locator.get("page"), [bbox] if bbox else [], ids[index]


def _label(canonical) -> str:
    meta = canonical.get("metadata") or {}
    return f"{canonical.get('exact_text') or ''} ({TYPE_LABELS.get(meta.get('entity_type'), meta.get('entity_type') or 'Entity')})"


def _card(canonical, mention, rows, cited=None, parent=None) -> str:
    meta = canonical.get("metadata") or {}
    lines = [_label(canonical)]
    aliases = [a for a in meta.get("aliases") or [] if a != canonical.get("exact_text")][:MAX_ALIASES]
    if aliases:
        lines.append("Also written: " + ", ".join(aliases))
    ids = [row["artifact_id"] for row in rows]
    position = f"mention {ids.index(mention['artifact_id']) + 1} of {len(ids)} here" if mention["artifact_id"] in ids \
        else f"{len(ids)} mentions here"
    sources = int(meta.get("source_count") or 1)
    lines.append(f"{position} · {meta.get('mention_count') or len(ids)} in the project, across {sources} "
                 f"source{'s' if sources != 1 else ''}")
    if parent:
        lines.append("Part of: " + parent["exact_text"])
    if cited:
        lines.append("Cites: " + cited["exact_text"][:CITED_PREVIEW])
    lines.append(_why(mention["metadata"]))
    lines.append("Go to: next mention in this document.")
    return "\n".join(line for line in lines if line)


def _why(metadata) -> str:
    method = metadata.get("method") or ""
    if method == "propagated":
        return f"Found by matching the name “{metadata.get('matched_surface') or ''}” found elsewhere in this document."
    if method == "project_propagated":
        return "Found by matching a name established in another source of this project."
    if method == "nominal_coref":
        antecedent = (metadata.get("coref") or {}).get("antecedent_surface")
        return f"A description referring back to “{antecedent}”." if antecedent else ""
    retyped = metadata.get("retyped_by")
    if retyped:
        return f"Type decided by {REASONS.get(retyped, retyped)}."
    return ""
