"""Column-local reference indentation inferred from author starts."""
from collections import Counter
from typing import Any, Dict, List, Optional
from core.models.extraction_models import DocumentBlock
from core.services.extraction.block_roles import content_text


def _lines(text: str):
    start = 0
    for line in (text or "").split("\n"):
        yield start, start + len(line), line
        start += len(line) + 1


_MARGIN_SLOP = 3.0       # points
_HANGING_INDENT = 8.0


def _with_x0(block: DocumentBlock):
    lines = list(_lines(content_text(block)))
    x0s = list(block.line_x0 or [])
    return [(line, x0s[k] if len(x0s) == len(lines) else None) for k, line in enumerate(lines)]


def _at_margin(margins, block: DocumentBlock, x0) -> Optional[bool]:
    if margins is None or x0 is None:
        return None
    key = margins["columns"].get(block.block_id)
    if key not in margins:
        return None
    if key in margins.get("first_line_indented", {}):
        return abs(x0 - margins[key] - margins["first_line_indented"][key]) <= _MARGIN_SLOP
    return x0 <= margins[key] + _MARGIN_SLOP


_COLUMN_GAP = 100.0       # points between column left edges


def _column_keys(bib_blocks: List[DocumentBlock]) -> Dict[str, Any]:
    """block id → (page, column): left edges within _COLUMN_GAP of a
    column's leftmost block belong to it (a hanging indent is ~20-40 pt)."""
    keys: Dict[str, Any] = {}
    by_page: Dict[Any, List[DocumentBlock]] = {}
    for block in bib_blocks:
        by_page.setdefault(block.page_num, []).append(block)
    for page, blocks in by_page.items():
        column, start = -1, None
        for block in sorted(blocks, key=lambda b: b.bbox[0] if b.bbox else 0.0):
            left = block.bbox[0] if block.bbox else 0.0
            if start is None or left - start > _COLUMN_GAP:
                column, start = column + 1, left
            keys[block.block_id] = (page, column)
    return keys


def _hanging_margins(bib_blocks: List[DocumentBlock], author_start) -> Optional[Dict[Any, float]]:
    """Left edge of a hanging-indent list per page column, or None when the
    list is not set with hanging indents (or the source has no geometry)."""
    columns = _column_keys(bib_blocks)
    by_column: Dict[Any, List[float]] = {}
    for block in bib_blocks:
        for (_s, _e, line), x0 in _with_x0(block):
            if x0 is not None and line.strip():
                by_column.setdefault(columns[block.block_id], []).append(x0)
    if not by_column:
        return None
    margins = {key: min(xs) for key, xs in by_column.items()}
    starts = {}
    previous = ""
    for block in bib_blocks:
        key = columns[block.block_id]
        if key not in margins:
            continue
        for (_s, _e, line), x0 in _with_x0(block):
            if x0 is not None and author_start(line.strip()) and not previous.rstrip().endswith((",", " and")):
                starts.setdefault(key, []).append(x0 - margins[key])
            if line.strip():
                previous = line
    # Some publishers indent the first line rather than continuation lines.
    # Infer direction from author starts independently for each page/column.
    first_indented = {}
    for key, xs in starts.items():
        offset, count = Counter(round(x/2)*2 for x in xs).most_common(1)[0]
        if len(xs) >= 3 and offset >= _HANGING_INDENT and count >= 0.7*len(xs):
            first_indented[key] = offset
    indented = sum(1 for key, xs in by_column.items() for x in xs if x > margins[key] + _HANGING_INDENT)
    total = sum(len(xs) for xs in by_column.values())
    if total < 6 or (indented < 0.25 * total and not first_indented):
        return None
    margins["columns"] = columns
    margins["first_line_indented"] = first_indented
    return margins


