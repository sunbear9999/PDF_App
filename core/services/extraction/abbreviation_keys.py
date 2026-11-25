"""Read a document's abbreviation key: "AGN  Archivo General de la Nación", "AD, Alzheimer's disease;".

A key is found by its heading or introducing phrase (``abbreviation_key_intro`` pack). Three
layouts are read: run-in lists ("Abbreviations: AD, Alzheimer's disease; BD, bipolar
disorder."), one entry per line ("ANEY  Archivo Notarial ..."), and aligned columns, read from
the PDF's line geometry, where a sub-key under a key ("-T  Tierras" under "AGN") becomes
"AGN-T". A region is accepted only with at least ``MIN_ENTRIES`` entries, so prose that
happens to mention "abbreviations" is not read as a key. Each entry keeps its block and the
key's character span, so it can be shown and jumped to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.models.extraction_patterns import ABBREVIATION_KEY_INTRO

MIN_ENTRIES = 3
MIN_INITIALS_SHARE = 0.5     # of a region's keys must come from their full names' initials
MIN_WORD_CAPS = 7            # all-capital words this long are headings, not abbreviations
MAX_KEY_CHARS = 12
MAX_PAGES_AFTER = 2          # a key may continue onto the following pages
ROW_TOLERANCE = 0.45         # of a line height: lines this close in height form one row
ROW_INDENT = 24              # points: a wrapped full name starts at (about) its column
MAX_EXPANSION_CHARS = 200
RUN_IN_BLOCKS = 40           # a run-in list may continue over this many following blocks
INTRO_PACK = "abbreviation_key_intro"

_KEY = re.compile(r"^[A-Z][A-Za-z0-9&/.\-–γαβ]{0,%d}$" % (MAX_KEY_CHARS - 1))
# A sub-key under the key above ("-T  Tierras" under "AGN"); its leading mark is required.
_SUB = re.compile(r"^[\s_]*[\-–—~<=]+\s*([A-Z]{1,3})[\s\-–—<_]*$")
_RUN_IN = re.compile(r"\s*([A-Z][A-Za-z0-9&/.\-–γαβ]{0,11})\s*[,;:]\s*([^;]{2,%d}?)\s*(?:;|\.\s*$|\.(?=\s+[A-Z0-9])|$)"
                     % MAX_EXPANSION_CHARS)


@dataclass
class AbbreviationEntry:
    key: str
    expansion: str
    block_id: str = ""
    page: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    parent_key: str = ""
    layout: str = "lines"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """The full name without a trailing bracketed or parenthetical note."""
        return re.split(r"\s+[\[(]", self.expansion, 1)[0].strip(" ,;:.=")


def is_key(text: str) -> bool:
    text = text.strip()
    if text.isalpha() and text.isupper() and len(text) >= MIN_WORD_CAPS:
        return False                      # a capitalised word ("APPENDIX", "NOTES"), not a short form
    capitals = sum(c.isupper() for c in text)
    return bool(_KEY.match(text)) and (capitals >= 2 or (capitals == 1 and any(c.isdigit() for c in text)))


def initials_match(key: str, name: str) -> bool:
    """The key's letters appear, in order, among the full name's word initials:
    AGN / Archivo General de la Nación, CBC / Book of Chilam Balam of Chumayel."""
    letters = [c.casefold() for c in key if c.isalpha()]
    initials = iter(word[0].casefold() for word in re.findall(r"[^\W\d_]+", name))
    if letters and all(any(letter == initial for initial in initials) for letter in letters):
        return True
    # A compound term: same first letter, then the key's letters in order (FOS / fructooligosaccharides).
    folded = [c.casefold() for c in name if c.isalpha()]
    if not letters or not folded or folded[0] != letters[0]:
        return False
    rest = iter(folded[1:])
    return all(any(letter == c for c in rest) for letter in letters[1:])


def _looks_like_key(entries) -> bool:
    """Most entries' keys come from their full names; a glossary or table (LAND / house plot,
    FLORA / palm for roofs) does not, and is not read as a key."""
    top = [entry for entry in entries if not entry.parent_key]
    return bool(top) and sum(initials_match(entry.key, entry.name) for entry in top) >= MIN_INITIALS_SHARE * len(top)


def _intro_patterns(registry) -> List[re.Pattern]:
    packs = registry.get_packs_for_type(INTRO_PACK) if registry else []
    return [p for pack in packs for p in pack.compiled] or [re.compile(ABBREVIATION_KEY_INTRO)]


def find(blocks: Sequence[Any], registry=None, page_lines=None) -> List[AbbreviationEntry]:
    """Every entry of every key in the document. ``page_lines(page)`` returns the page's
    lines as (x0, y0, y1, text) for column layouts; without it only text layouts are read."""
    intros = _intro_patterns(registry)
    entries: List[AbbreviationEntry] = []
    seen_pages = set()
    for index, block in enumerate(blocks):
        text = block.exact_text or ""
        match = next((m for p in intros for m in [p.search(text)] if m), None)
        if not match:
            continue
        found = _run_in(blocks, index, match.end())
        if len(found) < MIN_ENTRIES:
            found = _line_entries(blocks[index + 1:index + 1 + 200], block)
        if len(found) < MIN_ENTRIES and page_lines is not None and block.page_num not in seen_pages:
            found = _columns(block, blocks, page_lines)
            seen_pages.add(block.page_num)
        if len(found) >= MIN_ENTRIES and _looks_like_key(found):
            entries.extend(found)
    unique: Dict[str, AbbreviationEntry] = {}
    for entry in entries:
        unique.setdefault(entry.key, entry)
    for entry in unique.values():          # "AGN-T Tierras" is the Tierras section of the AGN
        parent = unique.get(entry.parent_key)
        if parent is not None:
            entry.expansion = f"{parent.name} – {entry.expansion}"
    return list(unique.values())


def _run_in(blocks, index, start: int) -> List[AbbreviationEntry]:
    """'Abbreviations: AD, Alzheimer's disease; BD, ...' read on through the following blocks
    (a list often spans several); offsets map each key back to its own block."""
    intro = blocks[index]
    segments, parts, length = [], [], 0
    for block in blocks[index:index + RUN_IN_BLOCKS]:
        if block.page_num != intro.page_num and block is not intro and intro.page_num is not None \
                and block.page_num is not None and block.page_num > intro.page_num + 1:
            break
        text = (block.exact_text or "").replace("\n", " ")       # same length: offsets stay valid
        segments.append((length, block))
        parts.append(text)
        length += len(text) + 1
    flat = " ".join(parts)
    position = re.compile(r"[\s:.\-–—]*").match(flat, start).end()
    out = []
    while True:
        match = _RUN_IN.match(flat, position)
        if not match or not is_key(match.group(1)):
            break
        base, block = next((b, blk) for b, blk in reversed(segments) if b <= match.start(1))
        expansion = re.sub(r"-\s+(?=[a-z])", "-", " ".join(match.group(2).split()))
        out.append(AbbreviationEntry(match.group(1), expansion, block.block_id, block.page_num,
                                     match.start(1) - base, match.end(1) - base, layout="run_in"))
        position = match.end()
    return out


def _line_entries(following, intro) -> List[AbbreviationEntry]:
    """One entry per line in the blocks after the introduction: 'ANEY  Archivo Notarial ...'."""
    out = []
    for block in following:
        if block.page_num is not None and intro.page_num is not None and block.page_num > intro.page_num + MAX_PAGES_AFTER:
            break
        offset = 0
        for line in (block.exact_text or "").split("\n"):
            parts = re.split(r"\s{2,}|\t|\s+[=–—-]\s+", line.strip(), 1)
            if len(parts) == 2 and is_key(parts[0]) and len(parts[1]) > 2:
                start = (block.exact_text or "").find(parts[0], offset)
                out.append(AbbreviationEntry(parts[0], parts[1].strip(), block.block_id, block.page_num,
                                             start, start + len(parts[0])))
            offset += len(line) + 1
    return out


def _columns(intro, blocks, page_lines) -> List[AbbreviationEntry]:
    """Aligned columns: rows by line height, key on the left, full name on the right."""
    out: List[AbbreviationEntry] = []
    pages = [intro.page_num + step for step in range(MAX_PAGES_AFTER + 1)] if intro.page_num is not None else []
    for page in pages:
        lines = page_lines(page) or []
        if page == intro.page_num:
            intro_bottom = _intro_bottom(lines, intro)
            lines = [line for line in lines if line[1] > intro_bottom]
        found = _rows_to_entries(_rows(lines), page, [b for b in blocks if b.page_num == page])
        if not found and out:
            break
        out.extend(found)
    return out


def _intro_bottom(lines, intro) -> float:
    words = (intro.exact_text or "").split()
    tail = " ".join(words[-3:]) if words else ""
    bottoms = [y1 for _, _, y1, text in lines if tail and tail in text]
    return max(bottoms) if bottoms else (intro.bbox or [0, 0, 0, 0])[3]


def _rows(lines) -> List[List[Tuple[float, str]]]:
    rows: List[Tuple[float, float, List[Tuple[float, str]]]] = []
    for x0, y0, y1, text in sorted(lines, key=lambda line: (line[1], line[0])):
        height = max(1.0, y1 - y0)
        if rows and abs(y0 - rows[-1][0]) <= ROW_TOLERANCE * height:
            rows[-1][2].append((x0, text.strip()))
        else:
            rows.append((y0, height, [(x0, text.strip())]))
    return [sorted(cells) for _, _, cells in rows]


def _rows_to_entries(rows, page, blocks) -> List[AbbreviationEntry]:
    out: List[AbbreviationEntry] = []
    parent, pending, expansion_x = "", "", None
    for cells in rows:
        cells = [(x, t) for x, t in cells if t]
        if not cells:
            continue
        first_x, first = cells[0]
        rest = " ".join(t for _, t in cells[1:]).strip()
        whole = " ".join(t for _, t in cells)
        sub = _SUB.match(first)
        key = first.strip(" _")
        if not sub and is_key(key):
            parent, pending = key, ("" if rest else key)       # alone: its name is on the next row
            if rest:
                out.append(_located(key, rest, page, blocks, ""))
                expansion_x = cells[1][0]
        elif sub and parent and rest:
            out.append(_located(f"{parent}-{sub.group(1)}", rest.lstrip("= ").strip(), page, blocks, parent))
        elif pending:
            out.append(_located(pending, whole, page, blocks, ""))
            pending, expansion_x = "", first_x
        elif out and expansion_x is not None and first_x >= expansion_x - ROW_INDENT:
            _continue(out[-1], whole)                          # a wrapped full name
        elif out:
            break                                              # prose: the key has ended
    return [entry for entry in out if entry.expansion]


def _continue(entry: AbbreviationEntry, text: str) -> None:
    joined = entry.expansion[:-1] + text if entry.expansion.endswith("-") else entry.expansion + " " + text
    entry.expansion = joined[:MAX_EXPANSION_CHARS]


def _located(key, expansion, page, blocks, parent) -> AbbreviationEntry:
    """The entry anchored on the stored block whose text holds the key (for jumping to it)."""
    probe = parent or key
    for block in blocks:
        text = block.exact_text or ""
        match = re.search(r"(?<![A-Za-z])" + re.escape(probe) + r"(?![A-Za-z])", text)
        if match:
            return AbbreviationEntry(key, expansion, block.block_id, page, match.start(), match.end(),
                                     parent_key=parent, layout="columns")
    return AbbreviationEntry(key, expansion, "", page, None, None, parent_key=parent, layout="columns")
