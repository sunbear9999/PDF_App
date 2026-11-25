"""Turn raster-read note markers into in-text citations, validated against the notes.

A reading becomes a marker only when (1) its number names exactly one note in
the marker's scope (``note_targets``), (2) no other marker already claims that
note, and (3) it keeps the scope's markers in increasing reading order — the
longest increasing run is kept, so one misreading cannot evict good ones. For a
run of plain digits the text layer's own number wins when it is valid; the image
only corrects an invalid one ("78" → 28). Numbers are never inferred from the
sequence. Candidates that fail are listed as unreadable for review.
"""
from __future__ import annotations

import bisect
import uuid
from typing import Dict, List, Tuple

from core.models.extraction_models import ExtractionProvenance, InTextCitation, SourceLocation
from core.services.extraction.block_roles import content_text
from core.services.extraction.raster_markers import MarkerCandidate, recover
from core.services.extraction.structure.reference_style import note_targets

CONFIDENCE = 0.7
CONTEXT = 80


def recover_markers(body_blocks, doc_id: str, in_text: List[InTextCitation], notes, images,
                    tesseract: str) -> Tuple[List[InTextCitation], List[Dict]]:
    """(the new marker list replacing overlapping text-layer markers, unreadable candidates)."""
    if not notes:
        return in_text, []
    by_block = {block.block_id: block for block in body_blocks}
    found = recover(body_blocks, images, tesseract, accept=lambda _candidate: True)
    replaced = {id(c) for c in in_text for cand in found if _overlaps(c, cand)}
    kept = [c for c in in_text if id(c) not in replaced]
    claimed = {_target_id(c, notes, by_block) for c in kept if c.citation_type == "superscript"} - {None}
    chosen: List[Tuple[MarkerCandidate, int, object]] = []
    unreadable = []
    for candidate in found:
        number, note = _number(candidate, doc_id, notes, by_block)
        if note is None or note.entry_id in claimed:
            unreadable.append(_report(candidate, "claimed" if note is not None else candidate.reason or "not_a_note_in_scope"))
            continue
        chosen.append((candidate, number, note))
    accepted = _increasing(chosen)
    for candidate, number, note in chosen:
        if (candidate, number, note) not in accepted:
            unreadable.append(_report(candidate, "out_of_order"))
    return kept + [_citation(doc_id, c, n) for c, n, _note in accepted], unreadable


def _number(candidate: MarkerCandidate, doc_id: str, notes, by_block):
    options = []
    if candidate.glyphs.isdigit():
        options.append(int(candidate.glyphs))
    if candidate.number is not None:
        options.append(candidate.number)
    for number in options:
        targets = note_targets(_citation(doc_id, candidate, number), notes, by_block)
        if len(targets) == 1:
            return number, targets[0]
    return None, None


def _increasing(chosen):
    """Longest strictly increasing run of numbers per scope, in reading order."""
    by_scope: Dict[str, list] = {}
    for item in chosen:
        by_scope.setdefault(str(item[2].normalized_keys.get("scope")), []).append(item)
    keep = []
    for items in by_scope.values():
        items.sort(key=lambda item: _order(item[0]))
        keep.extend(_lis(items))
    return keep


def _lis(items):
    tails, tail_index, parents = [], [], [None] * len(items)
    for index, item in enumerate(items):
        position = bisect.bisect_left(tails, item[1])
        if position:
            parents[index] = tail_index[position - 1]
        if position == len(tails):
            tails.append(item[1]); tail_index.append(index)
        else:
            tails[position], tail_index[position] = item[1], index
    out, index = [], tail_index[-1] if tail_index else None
    while index is not None:
        out.append(items[index]); index = parents[index]
    return out[::-1]


def _order(candidate: MarkerCandidate):
    block = candidate.block
    return (block.page_num or 0, int(str(block.block_id).rsplit("_b", 1)[-1] or 0)
            if str(block.block_id).rsplit("_b", 1)[-1].isdigit() else 0, candidate.start)


def _citation(doc_id: str, candidate: MarkerCandidate, number: int) -> InTextCitation:
    block, text = candidate.block, content_text(candidate.block)
    return InTextCitation(
        citation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}|raster-marker|{block.block_id}|{candidate.start}")),
        doc_id=doc_id, raw_text=candidate.glyphs, citation_type="superscript", numeric_keys=[str(number)],
        context_text=text[max(0, candidate.start - CONTEXT):candidate.end + CONTEXT],
        location=SourceLocation(doc_id, block.page_num, candidate.start, candidate.end, block_id=block.block_id),
        provenance=ExtractionProvenance("citation_extraction", "2.1", "raster_ocr", "marker_digits", CONFIDENCE),
        resolution_method="raster_digits" if not candidate.glyphs.isdigit() else "text_layer_digits",
    )


def _target_id(citation, notes, by_block):
    targets = note_targets(citation, notes, by_block)
    return targets[0].entry_id if len(targets) == 1 else None


def _overlaps(citation: InTextCitation, candidate: MarkerCandidate) -> bool:
    location = citation.location
    return bool(location and location.block_id == candidate.block.block_id
                and location.char_start < candidate.end and candidate.start < location.char_end)


def _report(candidate: MarkerCandidate, reason: str) -> Dict:
    return {"block_id": candidate.block.block_id, "page": candidate.block.page_num, "char_start": candidate.start,
            "char_end": candidate.end, "glyphs": candidate.glyphs, "readings": candidate.readings, "reason": reason}
