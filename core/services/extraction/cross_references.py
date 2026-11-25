"""Cross-references inside a document: "see Table 7.2", "Figure 3", "Chapter 5",
"Appendix B", "§ 4", "note 12", "supra note 12".

A reference is a label in running text. Its target is found from the
document's own structure only:

* Table / Figure / Map / Plate / Exhibit n → the caption that opens with that label;
* Chapter / Part / Appendix / Section / § n → the heading that opens with it;
* note n / n. n / supra note n → the note with that number in the reference's scope.

A reference links only when exactly one target matches; with none or several it
abstains and records why. A label at the very start of its own caption or
heading is the target, not a reference. Label words come from the
``cross_ref_mention`` pattern pack, so plugins can add their own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from core.models.extraction_models import DocumentBlock, InTextCitation, SourceLocation
from core.services.extraction.block_roles import content_text, is_furniture

PACK_ID = "cross_ref_mention"
# Label word -> kind. Plugins extend the words with the pack; kinds outside this map
# are treated as their own (lower-cased) kind.
KINDS = {
    "table": "table", "tab": "table", "tables": "table", "figure": "figure", "fig": "figure", "figs": "figure",
    "figures": "figure", "map": "map", "maps": "map", "plate": "plate", "plates": "plate", "exhibit": "exhibit",
    "ex": "exhibit", "chapter": "chapter", "chap": "chapter", "chapters": "chapter", "part": "part",
    "appendix": "appendix", "app": "appendix", "section": "section", "sect": "section", "sec": "section",
    "§": "section", "§§": "section", "note": "note", "notes": "note", "n": "note", "nn": "note",
}
CAPTION_KINDS = frozenset({"table", "figure", "map", "plate", "exhibit"})
HEADING_KINDS = frozenset({"chapter", "part", "appendix", "section"})
MAX_CAPTION_CHARS = 400           # a longer block that opens with "Table 3 shows …" is prose
IDENTIFIER = r"(\d+[a-z]?(?:[.\-–]\d+[a-z]?)*|[IVXLC]+\b|[A-Z]\b)"
DEFAULT_PATTERN = (
    r"(?i:(?<![\w§])(?:(supra|infra)\s+)?(tables?|tab|figures?|figs?|maps?|plates?|exhibit|ex|chapters?|chap|"
    r"part|appendix|app|section|sect|sec|notes?|nn?)(?:\.\s*|\s+)|(§§?)\s*)" + IDENTIFIER
)
LETTER_KINDS = frozenset({"appendix", "part", "exhibit"})   # only these take a letter ("Appendix B")
_UNITS = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
         "seventeen eighteen nineteen".split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORD_NUMBER = re.compile(r"(?i)^\s*(chapter|part|appendix|section)\s+(" + "|".join([*_TENS, *_UNITS]) +
                          r")(?:[\s-]+(" + "|".join(_UNITS[:9]) + r"))?\b")
# Back-matter headings repeat chapter titles ("Notes to Chapter Ten"); they are not targets.
NON_TARGET_REGIONS = frozenset({"notes", "references", "index", "contents", "glossary"})
# "Roys 1939: part 2", "Lockhart 1992, chap. 4": a part of the cited work, not of this document.
_CITED_WORK = re.compile(r"(?i)(?:\b\d{4}[a-z]?|ibid\.?|op\.\s*cit\.?|loc\.\s*cit\.?)\s*[:,]?\s*$")
CITED_WINDOW = 20
# "TABLE 4.2 (continued)": the same table again, not a second target.
_CONTINUED = re.compile(r"(?i)\s*[(\[]?\s*cont(?:inued|['’]d|\.)")


@dataclass
class CrossReference:
    block: DocumentBlock
    start: int
    end: int
    kind: str
    label: str
    target_block_id: str = ""
    target_note_id: str = ""
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.target_block_id or self.target_note_id)


def patterns(registry: Any = None) -> List[re.Pattern]:
    pack = registry.get_pack(PACK_ID) if registry else None
    return list(pack.compiled) if pack is not None and pack.compiled else [re.compile(DEFAULT_PATTERN)]


def find(blocks: List[DocumentBlock], notes=(), registry: Any = None, note_targets=None) -> List[CrossReference]:
    """Every label reference in body text, with its unique target or the reason it has none."""
    captions, headings = _targets(blocks)
    found = []
    for block in blocks:
        if is_furniture(block) or block.block_type in {"bibliography", "toc"}:
            continue
        text = content_text(block)
        for pattern in patterns(registry):
            for match in pattern.finditer(text):
                reference = _reference(block, match)
                if reference is None or _is_own_label(reference, captions, headings):
                    continue
                if _CITED_WORK.search(text[max(0, match.start() - CITED_WINDOW):match.start()]):
                    continue
                _resolve(reference, captions, headings, notes, note_targets)
                found.append(reference)
    return found


def _reference(block: DocumentBlock, match: re.Match) -> Optional[CrossReference]:
    word = (match.group(2) or match.group(3) or "").lower()
    kind = KINDS.get(word, word)
    label = match.group(4)
    if not label or (label.isalpha() and len(label) == 1 and kind not in LETTER_KINDS):
        return None
    return CrossReference(block, match.start(), match.end(), kind, _normal(label))


def _targets(blocks: List[DocumentBlock]) -> Tuple[Dict[tuple, List[DocumentBlock]], Dict[tuple, List[DocumentBlock]]]:
    captions: Dict[tuple, List[DocumentBlock]] = {}
    headings: Dict[tuple, List[DocumentBlock]] = {}
    for block in blocks:
        if block.structure.get("region") in NON_TARGET_REGIONS:
            continue
        text = content_text(block).lstrip()
        reference = _word_numbered(block, text)
        match = re.match(DEFAULT_PATTERN, text) if reference is None else None
        if reference is None and match is not None:
            reference = _reference(block, match)
        if reference is None or _CONTINUED.match(text[match.end():] if match else ""):
            continue
        heading = block.block_type == "heading" or bool(block.structure.get("heading_level"))
        if reference.kind in CAPTION_KINDS and (block.block_type in {"caption", "heading"}
                                                or len(block.exact_text) <= MAX_CAPTION_CHARS):
            captions.setdefault((reference.kind, reference.label), []).append(block)
        elif reference.kind in HEADING_KINDS and heading:
            headings.setdefault((reference.kind, reference.label), []).append(block)
    return captions, headings


def _word_numbered(block: DocumentBlock, text: str) -> Optional[CrossReference]:
    """A heading that spells its number ("CHAPTER TWENTY-THREE", "Part One")."""
    match = _WORD_NUMBER.match(text)
    if match is None:
        return None
    words = [w.lower() for w in match.groups()[1:] if w]
    number = _TENS.get(words[0], 0) or _UNITS.index(words[0]) + 1
    if len(words) > 1:
        number += _UNITS.index(words[1]) + 1
    return CrossReference(block, match.start(1), match.end(), KINDS[match.group(1).lower()], str(number))


def _is_own_label(reference: CrossReference, captions, headings) -> bool:
    own = captions.get((reference.kind, reference.label), []) + headings.get((reference.kind, reference.label), [])
    leading = len(content_text(reference.block)) - len(content_text(reference.block).lstrip())
    return reference.block in own and reference.start <= leading


def _resolve(reference: CrossReference, captions, headings, notes, note_targets) -> None:
    key = (reference.kind, reference.label)
    if reference.kind == "note":
        _resolve_note(reference, notes, note_targets)
        return
    pool = captions if reference.kind in CAPTION_KINDS else headings if reference.kind in HEADING_KINDS else {}
    targets = pool.get(key, [])
    if len(targets) == 1:
        reference.target_block_id = targets[0].block_id
    else:
        reference.reason = "no_target" if not targets else "several_targets"


def _resolve_note(reference: CrossReference, notes, note_targets) -> None:
    if not notes or note_targets is None or not reference.label.isdigit():
        reference.reason = "no_target"
        return
    probe = InTextCitation("xref", reference.block.doc_id, reference.label, citation_type="superscript",
                           numeric_keys=[reference.label],
                           location=SourceLocation(reference.block.doc_id, reference.block.page_num,
                                                   reference.start, reference.end, block_id=reference.block.block_id))
    targets = note_targets(probe, notes, {reference.block.block_id: reference.block})
    if len(targets) == 1:
        reference.target_note_id = targets[0].entry_id
    else:
        reference.reason = "no_target" if not targets else "several_targets"


def _normal(label: str) -> str:
    return label.replace("–", "-").upper() if label.isalpha() else label.replace("–", "-").lower()
