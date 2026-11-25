"""Footnotes / endnotes as citable notes.

A superscript marker in running text ("…an independent body.²") usually
points at a numbered note at the foot of the page or the end of the text —
not at reference-list entry 2. Notes are extracted here as their own entries
(``citation_style="footnote"``) with exact spans, and
:func:`partition_for_matching` sends superscript marks to notes when the
document has them, while bracketed numbers keep matching the reference list.
A document whose superscripts *are* reference numbers (no numbered notes)
is unaffected.
"""
from __future__ import annotations

import re
import uuid
from typing import List, Tuple

from core.models.extraction_models import (
    BibliographyEntry, DocumentBlock, ExtractionProvenance, InTextCitation, SourceLocation,
)
from core.services.extraction.structure.scopes import chapter_scope
from core.services.extraction.block_roles import content_text, is_furniture, is_ocr_text

# A superscript label may touch its text ("⁶²“Oaths of Loyalty…"); a plain
# digit needs a separator ("16 December 1966" vs "16December").
_NOTE_START = re.compile(r"^\s*(?:([¹²³⁴⁵⁶⁷⁸⁹⁰]{1,3})(?:[.)]|\s)?|(\d{1,3})(?:[.)]+(?!\d)|\t|\s))\s*(?=[^\s⁰¹²³⁴⁵⁶⁷⁸⁹,.;:])")
_SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_NOTE_TYPES = {"footnote"}
_MIN_NOTE_CHARS = 8
_VERSION = "2.1"


def extract_notes(blocks: List[DocumentBlock], doc_id: str) -> List[BibliographyEntry]:
    """Numbered notes in footnote-region blocks, numbered in document order
    (a wrapped line starting "16 December 1966" is not note 16 after note 3)."""
    body = _body_size(blocks)
    heights = _page_bottoms(blocks)
    notes, seen = [], {}
    scope, sequence = "endnotes:1", 1
    page_range = None
    for block in blocks:
        raw = block.exact_text.strip()
        explicit = chapter_scope(raw)
        pages = re.search(r"(?i)notes\s+to\s+pages?\s+(\d+)\s*[-–—]\s*(\d+)", raw)
        if pages:
            start, end = pages.groups()
            if len(end) < len(start):
                expanded = int(start[:-len(end)] + end)
                end = str(expanded + (10**len(end) if expanded < int(start) else 0))
            page_range = start, end
        if explicit and block.structure.get("region") == "notes":
            scope, seen = explicit, {}
            continue
        if is_furniture(block) or not _note_region(block, body, heights):
            continue
        endnote = block.structure.get("region") == "notes"
        if not endnote:
            seen = {}
        if endnote and re.match(r"^\s*1[.)]+\s", raw) and max(seen, default=0) > 1:
            seen = {}
        found = _block_notes(block, doc_id, seen)
        if endnote and not found and notes and notes[-1].normalized_keys.get("scope") == scope:
            notes[-1].raw_text += "\n" + content_text(block).strip()
            notes[-1].source_spans.append({"block_id": block.block_id, "page": block.page_num, "char_start": 0, "char_end": len(block.exact_text)})
        for note in found:
            if endnote and note.reference_number == "1" and any(n.normalized_keys.get("scope") == scope for n in notes):
                sequence += 1
                scope = "endnotes:" + str(sequence)
            note.normalized_keys.update(scope=scope if endnote else "page:" + str(block.page_num),
                                        scope_kind="chapter" if endnote else "page")
            if endnote and page_range:
                note.normalized_keys.update(page_start=page_range[0], page_end=page_range[1])
            notes.append(note)
    return _longest_per_number(notes)


def _longest_per_number(notes: List[BibliographyEntry]) -> List[BibliographyEntry]:
    """An affiliation line ("¹ London, UK") can claim a number before the real
    note does; the note with that number and the most text wins."""
    best: dict = {}
    for note in notes:
        key = (note.normalized_keys.get("scope"), note.reference_number)
        held = best.get(key)
        if held is None or len(note.raw_text) > len(held.raw_text):
            best[key] = note
    return [note for note in notes if best[(note.normalized_keys.get("scope"), note.reference_number)] is note]


def _body_size(blocks: List[DocumentBlock]) -> float:
    weights = {}
    for block in blocks:
        if block.font_size:
            weights[round(block.font_size, 1)] = weights.get(round(block.font_size, 1), 0) + len(block.exact_text)
    return max(weights, key=weights.get) if weights else 0.0


def _page_bottoms(blocks: List[DocumentBlock]) -> dict:
    bottoms = {}
    for block in blocks:
        if block.bbox and block.page_num is not None:
            bottoms[block.page_num] = max(bottoms.get(block.page_num, 0.0), block.bbox[3])
    return bottoms


def _note_region(block: DocumentBlock, body: float, bottoms: dict) -> bool:
    """Typed footnote, or small text in the lower part of the page that
    starts with a note number (publishers rarely mark footnotes)."""
    text = content_text(block)
    if block.page_num == 0 and re.search(r"(?i)@|correspondence|department|university|institute|school of|received:|accepted:", text):
        return False
    if block.block_type == "bibliography" or block.structure.get("region") == "references":
        return False
    if block.structure.get("region") == "notes":
        return True
    # OCR font sizes are estimates. A bare digit in a small newspaper/table
    # block needs a visible note delimiter before geometry can identify it.
    if is_ocr_text(block) and not re.match(r"^\s*(?:[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\d{1,3}[.)](?!\d))", text):
        return False
    if block.block_type in _NOTE_TYPES:
        return True
    if not (block.font_size and body and block.font_size < body * 0.93 and block.bbox):
        return False
    bottom = bottoms.get(block.page_num) or block.bbox[3]
    return block.bbox[1] > 0.55 * bottom and bool(_NOTE_START.match(content_text(block)))


def partition_for_matching(
    citations: List[InTextCitation], entries: List[BibliographyEntry], notes: List[BibliographyEntry],
) -> List[Tuple[List[InTextCitation], List[BibliographyEntry]]]:
    """Superscripts → notes when ≥ half their keys name an existing note;
    everything else → the reference list."""
    note_numbers = {note.reference_number for note in notes}
    superscripts = [c for c in citations if c.citation_type == "superscript"]
    keys = [k for c in superscripts for k in c.numeric_keys]
    if notes and keys and sum(k in note_numbers for k in keys) >= 0.5 * len(keys):
        others = [c for c in citations if c.citation_type != "superscript"]
        return [(superscripts, notes), (others, entries)]
    return [(citations, entries)]


_NUMBER_ONLY = re.compile(r"^\s*([¹²³⁴⁵⁶⁷⁸⁹⁰]{1,3}|\d{1,3})[.)]?\s*$")


def _number_line(line: str, lines: List[str], index: int):
    """"¹" alone on a line, note text on the next one (CIGI/JSTOR layout)."""
    match = _NUMBER_ONLY.match(line)
    following = lines[index + 1].strip() if index + 1 < len(lines) else ""
    return match if match and following[:1].isalpha() else None


def _plausible(number: int, seen: dict, block_id: str) -> bool:
    """Next in sequence give or take two-column reordering (note 11 may be
    read before note 10); a far jump is a wrapped line, not a note. A number
    already seen may recur only in another block (see _longest_per_number)."""
    top = max(seen) if seen else 0
    repeat = number in seen and seen[number] == block_id
    return not repeat and number >= 1 and (not seen or number == 1 or top - 3 <= number <= top + 3)


def _block_notes(block: DocumentBlock, doc_id: str, seen: dict) -> List[BibliographyEntry]:
    text = content_text(block)
    starts = []
    offset = 0
    lines = text.split("\n")
    for index, line in enumerate(lines):
        match = _NOTE_START.match(line) or _number_line(line, lines, index)
        if match:
            group = 1 if match.group(1) else 2
            number = int(match.group(group).translate(_SUPERSCRIPT_MAP))
            if number == 1 and starts and block.structure.get("region") == "notes":
                seen.clear()
            if _plausible(number, seen, block.block_id):
                starts.append((offset + match.start(group), str(number)))
                seen.setdefault(number, block.block_id)
        offset += len(line) + 1
    notes = []
    for k, (start, number) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(text)
        raw = text[start:end].strip()
        if block.page_num == 0 and block.block_type != "footnote":
            # Article-number + DOI fragments in publication metadata are not
            # prose notes. Explicitly typed notes remain available.
            body = _NOTE_START.sub("", raw, count=1)
            if re.fullmatch(r"https?://\S+", "".join(body.split())):
                continue
        if len(raw) >= _MIN_NOTE_CHARS:
            notes.append(_note(block, doc_id, number, raw, start, start + len(text[start:end].rstrip())))
    return notes


def _note(block, doc_id, number, raw, start, end) -> BibliographyEntry:
    return BibliographyEntry(
        entry_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}|note|{block.block_id}|{start}|{number}")),
        doc_id=doc_id, raw_text=raw, reference_number=number, citation_style="footnote",
        normalized_keys={"numeric": number, "footnote": number},
        location=SourceLocation(doc_id=doc_id, page_num=block.page_num, block_id=block.block_id,
                                char_start=start, char_end=end),
        source_spans=[{"block_id": block.block_id, "page": block.page_num, "char_start": start, "char_end": end}],
        provenance=ExtractionProvenance(
            extractor_name="footnote_notes", extractor_version=_VERSION,
            parser_provider="pymupdf_layout", method="layout", confidence=0.8,
        ),
    )
