"""Paragraph reconstruction for PDF text blocks.

PyMuPDF often returns one *block per line* (web prints such as CourtListener,
reference lists, many publisher PDFs). Treating each line as a block breaks
sentences, names, dates, citations and bibliography entries at every line
end. ``group_blocks`` decides, from geometry and typography only, which
consecutive raw blocks on a page continue the same paragraph.

The function is pure and deterministic. The layout parser and the highlight
geometry (``block_char_rects``) both call it, so a merged block's stored text
and its reconstructed character boxes always agree. A merged block keeps the
raw index of its first member as its ID (``…_p{page}_b{first}``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

# A list item / reference / bullet starts a new unit even when it touches
# the previous line ("2.\tNaggan L, …", "[12] Smith …", "• item").
_ENUMERATOR = re.compile(r"^\s*(?:\(?\d{1,3}[.)]\s|\[\d{1,3}\]|[•●▪◦‣]\s|[a-z][.)]\s)")
# Line ends that close a paragraph when the line is also short.
# ":" and ";" are not paragraph ends: reference entries wrap after them.
_TERMINAL = re.compile(r"[.!?\"”’)\]](?:\d{1,3}|[¹²³⁴⁵⁶⁷⁸⁹⁰⁻,]+)?\s*$")   # "members.1" ends too
_NUMBERED = re.compile(r"^\s*\(?\[?(\d{1,3})[.)\]]")
_SIZE_TOLERANCE = 0.08     # same typography: ≤ 8% font-size difference
_MAX_GAP = 0.75            # vertical gap ≤ 0.75 × line height continues a paragraph
_MAX_OVERLAP = 0.6         # PyMuPDF boxes may overlap slightly
_MAX_SPACING = 1.6         # line spacing up to double-spaced counts as one paragraph
_SHORT_LINE = 0.85         # last line ending before 85% of column width = paragraph end
_COLUMN_SLOP = 30.0        # x0 within this many points → same column
_INDENT_SLOP = 40.0        # hanging/first-line indent tolerance (MLA hanging indent = 36pt)


@dataclass(frozen=True)
class BlockShape:
    """What grouping needs to know about one raw PyMuPDF text block."""
    index: int
    bbox: Sequence[float]
    last_line_bbox: Sequence[float]
    line_height: float
    text: str
    size: float
    bold: bool


def group_blocks(shapes: List[BlockShape]) -> List[List[int]]:
    """Return raw block indices grouped into paragraphs, in input order."""
    groups: List[List[int]] = []
    previous: Optional[BlockShape] = None
    last_number: Optional[int] = None
    line_gap = _typical_line_gap(shapes)
    for shape in shapes:
        number = _item_number(shape.text, last_number)
        if previous is not None and number is None and _continues(previous, shape, shapes, line_gap):
            groups[-1].append(shape.index)
        else:
            groups.append([shape.index])
        last_number = number if number is not None else last_number
        previous = shape
    return groups


def _item_number(text: str, last: Optional[int]) -> Optional[int]:
    """The list number this block starts, if it really starts the next item.
    A wrapped page range ("453. https://doi…") is not item 453 after item 8."""
    match = _NUMBERED.match(text)
    if not match:
        return None
    number = int(match.group(1))
    if last is None or last < number <= last + 3 or number == 1:
        return number
    return None


def _typical_line_gap(shapes: List[BlockShape]) -> Optional[float]:
    """Median gap between consecutive same-column lines on the page. Double-
    spaced essays put a full line height between lines; paragraph breaks are
    still larger than that, and also carry first-line indents."""
    gaps = [
        b.bbox[1] - a.last_line_bbox[3] for a, b in zip(shapes, shapes[1:])
        if _same_column(a, b) and abs(a.size - b.size) <= _SIZE_TOLERANCE * max(a.size, b.size, 1.0)
        and b.bbox[1] - a.last_line_bbox[3] > 0
    ]
    for shape in shapes:      # spacing inside multi-line blocks is the same evidence
        lines = shape.text.count("\n") + 1
        if lines >= 3:
            inner = (shape.bbox[3] - shape.bbox[1] - lines * shape.line_height) / (lines - 1)
            if inner > 0:
                gaps.extend([inner] * (lines - 1))
    gaps.sort()
    return gaps[len(gaps) // 2] if len(gaps) >= 4 else None


def _continues(a: BlockShape, b: BlockShape, page: List[BlockShape], line_gap: Optional[float] = None) -> bool:
    if not a.text.strip() or not b.text.strip():
        return False
    if _ENUMERATOR.match(b.text) and not _NUMBERED.match(b.text):
        return False   # bullets / lettered items always start a new unit
    if _same_line(a, b):
        return True    # a linked citation split into its own block mid-line
    if a.bold != b.bold or _heading_like(a, b, page):
        return False
    if abs(a.size - b.size) > _SIZE_TOLERANCE * max(a.size, b.size, 1.0):
        return False
    if not _same_column(a, b):
        return False
    height = max(a.line_height, 1.0)
    gap = b.bbox[1] - a.last_line_bbox[3]
    limit = _MAX_GAP * height
    if line_gap is not None and line_gap <= _MAX_SPACING * height:
        limit = max(limit, 1.25 * line_gap)
    if gap > limit or gap < -_MAX_OVERLAP * height:
        return False
    return not _paragraph_ends(a, b, page)


def _same_line(a: BlockShape, b: BlockShape) -> bool:
    """B starts on A's last line, right after it ("…officer.” 18 U.S.C. §
    3563(b)(6)…" printed as three blocks). A column gutter is wider than 6pt."""
    height = max(a.line_height, 1.0)
    return (abs(b.bbox[1] - a.last_line_bbox[1]) <= 0.5 * height
            and -3.0 <= b.bbox[0] - a.last_line_bbox[2] <= 6.0)


def _heading_like(a: BlockShape, b: BlockShape, page: List[BlockShape]) -> bool:
    """All-caps lines and short unpunctuated one-line labels ("Background")
    followed by a capitalised line are headings, not paragraph starts."""
    text = a.text.strip()
    letters = [c for c in text if c.isalpha()]
    words = re.findall(r"[A-Za-z]{3,}", text)   # "18 U.S.C. § 3563" has no heading words
    if words and len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.8 \
            and len(text.split()) <= 14:
        return True
    single_line = "\n" not in text
    width = max(_column_right_edge(a, page) - a.bbox[0], 1.0)
    short = a.bbox[2] - a.bbox[0] < 0.6 * width
    starts_upper = b.text.lstrip()[:1].isupper()
    return single_line and short and not _TERMINAL.search(text) and starts_upper and not text.endswith(",")


def _same_column(a: BlockShape, b: BlockShape) -> bool:
    """Hanging indents move right; after an indented first line the rest of a
    paragraph moves left. Either way it is the same column."""
    return abs(b.bbox[0] - a.bbox[0]) <= _INDENT_SLOP


def _paragraph_ends(a: BlockShape, b: BlockShape, page: List[BlockShape]) -> bool:
    """A short last line ending in terminal punctuation closes a paragraph;
    so does a first-line indent after terminal punctuation."""
    if not _TERMINAL.search(a.text):
        return False
    right = _column_right_edge(a, page)
    width = max(right - a.bbox[0], 1.0)
    short_last_line = a.last_line_bbox[2] < a.bbox[0] + _SHORT_LINE * width
    indented_next = b.bbox[0] - a.bbox[0] > 6.0 and not _ENUMERATOR.match(a.text)
    return short_last_line or indented_next


def _column_right_edge(a: BlockShape, page: List[BlockShape]) -> float:
    edges = [s.bbox[2] for s in page if abs(s.bbox[0] - a.bbox[0]) <= _COLUMN_SLOP]
    return max(edges) if edges else a.bbox[2]
