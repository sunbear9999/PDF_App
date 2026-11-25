"""Detect page furniture at line level, for every paginated source.

Signals (any one marks a line):

* **Repetition** — a short line whose shape (digits masked, case folded)
  recurs on at least 30% of pages (min. 3): running heads, journal titles,
  "Cite as: 591 U. S. ____", "Opinion of the Court", "Cohrs 3", "CME Article".
  Inline citation fragments ("17 Stat. 626") repeat too and are excluded.
* **Page numbers** — a bare number / "e126" / roman numeral / "Page x of y"
  that is a block's first or last line on a page's first or last block.
* **Strong phrases** — copyright and licence lines, download/access stamps,
  JSTOR and RightsLink banners, "View on CourtListener", slip-opinion notices.
* **Cover pages** — a JSTOR-style cover (stable URL + terms banner) is
  boilerplate in full; the article's own title page follows it.

Detected lines become ``furniture_spans`` on their block; a block whose every
line is furniture gets a furniture ``block_type``. ``exact_text`` is untouched.
Plugins add publisher phrases with :func:`register_phrase`.
"""
from __future__ import annotations

import re
from core.services.extraction.structure.running_heads import locally_repeated
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_STRONG = [
    # Stamp forms only: an opinion *about* copyright, or "data downloaded
    # from the census", is content.
    r"^\W*(?:copyright|©)\s*(?:©\s*)?(?:\d{4}|by|the author)|^\W*copyright\s*©|^\W*©\s*\w|©\s*\d{4}|all rights reserved",
    r"this content downloaded from|downloaded from\s+https?://|downloaded from .* on \w+ \d|all use subject to https?://",
    r"jstor is a not-for-profit|your use of the jstor archive|is collaborating with jstor",
    r"electronic copy available at|view on courtlistener|page proof pending publication",
    r"this opinion is subject to (?:formal )?revision|notify the reporter of decisions",
    r"^\s*cite as:|rightslink|check for updates",
    r"(?:this|the text of this) work is licen[cs]ed under|^\s*licen[cs]ed under (?:a |the )?(?:cc|creative commons)",
    r"^\s*vol\.:\(0123456789\)|for publications enquiries|^\s*published online:",
    r"^\s*received:.*accepted:",
    r"^\s*(?:not\s+)?for\s+publication\s*$",   # slip-opinion stamp
]
_STRONG_RE = re.compile("|".join(f"(?:{p})" for p in _STRONG), re.I)
# Subtype of a strong line; the block type stays "boilerplate" either way.
_STRONG_LABELS = [
    ("copyright", re.compile(_STRONG[0] + "|" + _STRONG[6], re.I)),
    ("download_stamp", re.compile(_STRONG[1] + "|" + _STRONG[2] + r"|electronic copy available at", re.I)),
]
_DOWNLOAD_STAMP = re.compile(_STRONG[1], re.I)
_EXTRA: List[re.Pattern] = []
_COVER_RE = re.compile(r"stable url:|jstor is a not-for-profit|your use of the jstor archive", re.I)
_PAGE_NUMBER = re.compile(r"^[\s\-–—]*(?:[a-zA-Z]?[\d⁰¹²³⁴⁵⁶⁷⁸⁹]{1,4}|[ivxlc]{1,6}|[Pp]age \d+(?: of \d+)?)[\s\-–—]*$")
# Page numbers, years and volumes vary between pages; a post id or DOI suffix
# (5+ digits) identifies a distinct line and is kept.
_DIGITS = re.compile(r"(?<![\d⁰¹²³⁴⁵⁶⁷⁸⁹])[\d⁰¹²³⁴⁵⁶⁷⁸⁹]{1,4}(?![\d⁰¹²³⁴⁵⁶⁷⁸⁹])")
_CITATION_FRAGMENT = re.compile(
    r"(u\.\s?s\.|f\.\s?(#d|supp)|s\.\s?ct|stat\.|c\.\s?f\.\s?r|§|l\.\s?ed).*#[\s,.;)–\-]*$")
# Section headings ("References", "Works Cited", "Notes") are never furniture.
_SECTION_HEADING = re.compile(
    r"^\W*(?:references?|works? cited|bibliography|notes|endnotes|abstract|introduction|conclusions?)\W*$")
# Watermark wording; rotation is not kept on blocks, so the words decide.
_WATERMARK = re.compile(r"^\W*(?:draft(?: copy)?|confidential|preprint|sample|do not (?:copy|distribute|cite)|"
                        r"not for (?:distribution|citation)|uncorrected proof|review copy)\W*$", re.I)
_REPEAT_SHARE = 0.3
_MIN_REPEAT_PAGES = 3
_MAX_LINE = 120
_STRONG_MAX_LINE = 240


def register_phrase(pattern: str) -> None:
    """Add a publisher-specific furniture phrase (regex, case-insensitive)."""
    _EXTRA.append(re.compile(pattern, re.I))


def unregister_phrase(pattern: str) -> None:
    _EXTRA[:] = [p for p in _EXTRA if p.pattern != pattern]


def detect(blocks: List[Any]) -> int:
    """Mark furniture on ``blocks`` in place; return the number of spans marked."""
    paged = [b for b in blocks if getattr(b, "page_num", None) is not None]
    pages = {b.page_num for b in paged}
    edges = _edge_blocks(paged)
    repeated = _repeated_shapes(paged, len(pages), edges)
    covers = _cover_pages(paged, repeated)
    band = _number_band(paged)
    contents_pages = {b.page_num for b in paged if any(re.fullmatch(r"(?i)\s*(?:table of contents|contents)\s*", line) for line in b.exact_text.splitlines())}
    marked = 0
    for block in blocks:
        in_contents = block.page_num in contents_pages
        spans = _block_spans(block, set() if in_contents else repeated, set() if in_contents else edges & band, covers)
        if spans:
            # keep spans the parser already marked (EPUB page markers, boilerplate)
            known = {(start, end) for start, end, _label in getattr(block, "furniture_spans", None) or []}
            block.furniture_spans = list(block.furniture_spans or []) + [
                span for span in spans if (span[0], span[1]) not in known]
            marked += len(spans)
            if _covers_all(block.exact_text, spans):
                block.block_type = _block_type(spans)
    return marked


def lines_with_offsets(text: str) -> Iterable[Tuple[int, int, str]]:
    start = 0
    for line in (text or "").split("\n"):
        yield start, start + len(line), line
        start += len(line) + 1


def shape(line: str) -> str:
    return _DIGITS.sub("#", " ".join((line or "").casefold().split()))


def _repeated_shapes(blocks: List[Any], page_count: int, edges: Set[str]) -> Set[str]:
    """Shapes repeated on enough pages, mostly at page edges (running heads
    live in the first/last blocks; a table's repeated column header does not)."""
    if page_count < _MIN_REPEAT_PAGES:
        return set()
    seen: Dict[str, Set[int]] = defaultdict(set)
    local_band = _number_band(blocks)
    edge_pages: Dict[str, Set[int]] = defaultdict(set)
    placed: Dict[str, Set[int]] = defaultdict(set)   # at a page edge, or a block of its own
    for block in blocks:
        own_block = "\n" not in block.exact_text.strip()
        for _start, _end, line in lines_with_offsets(block.exact_text):
            if 0 < len(line.strip()) <= _MAX_LINE and not (len(line.split()) > 8 and line.rstrip().endswith((".", "!", "?"))):
                seen[shape(line)].add(block.page_num)
                if block.block_id in edges & local_band and len(line.strip()) >= 8:
                    edge_pages[shape(line)].add(block.page_num)
                if block.block_id in edges or own_block:
                    placed[shape(line)].add(block.page_num)
    floor = max(_MIN_REPEAT_PAGES, _REPEAT_SHARE * page_count)
    # Web prints stitch pages, so heads also appear mid-page as their own block.
    return {s for s, found in seen.items()
            if ((len(found) >= floor and len(placed[s]) >= 0.5 * len(found))
                or locally_repeated(found, edge_pages[s]))
            # "1 3" publisher marks repeat with no letters; a lone number is
            # a page number or note number, judged by the page-number rule.
            and (s.strip("# .") or (len(s) <= 8 and " " in s.strip()))
            and not _CITATION_FRAGMENT.search(s) and (not _SECTION_HEADING.match(s) or locally_repeated(found, edge_pages[s]))}


def _edge_blocks(blocks: List[Any]) -> Set[str]:
    by_page: Dict[int, List[Any]] = defaultdict(list)
    for block in blocks:
        by_page[block.page_num].append(block)
    edges: Set[str] = set()
    for page_blocks in by_page.values():
        ordered = sorted(page_blocks, key=_position)
        edges.update(b.block_id for b in ordered[:3] + ordered[-3:])
    return edges


def _number_band(blocks: List[Any]) -> Set[str]:
    """Blocks in the top/bottom 12% of the document's text extent: the only
    place a bare number is a page number (not a table cell like "16")."""
    boxes = [b.bbox for b in blocks if getattr(b, "bbox", None)]
    if not boxes:
        return {b.block_id for b in blocks}
    top, bottom = min(b[1] for b in boxes), max(b[3] for b in boxes)
    reach = 0.12 * max(bottom - top, 1.0)
    return {b.block_id for b in blocks if b.bbox and (b.bbox[1] >= bottom - reach or b.bbox[3] <= top + reach)}


def _position(block: Any) -> Tuple[float, float]:
    bbox = getattr(block, "bbox", None) or [0, 0, 0, 0]
    return (bbox[1], bbox[0])


def _cover_pages(blocks: List[Any], repeated: Set[str] = frozenset()) -> Set[int]:
    """A repository/archive cover sheet: the first page carries the archive's
    stamps, or it is a short page whose title the next page repeats
    (bepress, SSRN, institutional repositories)."""
    text_by_page: Dict[int, str] = defaultdict(str)
    for block in blocks:
        text_by_page[block.page_num] += "\n" + block.exact_text
    if len(text_by_page) < 3:
        return set()
    first = min(text_by_page)
    text = text_by_page[first]
    if len(_COVER_RE.findall(text)) >= 2:
        return {first}
    following = " ".join(_words(text_by_page.get(first + 1, ""))[:80])
    titles = [b for b in blocks if b.page_num == first and len(_words(b.exact_text)) >= 4
              and not any(shape(line) in repeated for line in b.exact_text.split("\n"))]
    if not titles or len(_words(text)) > 150:
        return set()
    # Any substantial line of it (the title, under the archive's own banner)
    return {first} if any(" ".join(_words(b.exact_text)[:8]) in following for b in titles) else set()


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").casefold())


def _block_spans(block: Any, repeated: Set[str], edges: Set[str], covers: Set[int]) -> List[List[Any]]:
    text = getattr(block, "exact_text", "") or ""
    lines = list(lines_with_offsets(text))
    if getattr(block, "page_num", None) in covers:
        # The cover is one unit, except the per-download stamp it shares with every page.
        if not any(_DOWNLOAD_STAMP.search(line) for _s, _e, line in lines):
            return [[0, len(text), "cover_page"]]
        return [[s, e, "download_stamp" if _DOWNLOAD_STAMP.search(line) else "cover_page"]
                for s, e, line in lines if line.strip()]
    spans: List[List[Any]] = []
    for position, (start, end, line) in enumerate(lines):
        label = _line_label(line, position, len(lines), block, repeated, edges)
        if label:   # trimmed: a span covers the visible line, not its padding
            lead = len(line) - len(line.lstrip())
            spans.append([start + lead, start + len(line.rstrip()), label])
    return _drop_marker_numbers(spans, lines)


def _drop_marker_numbers(spans: List[List[Any]], lines) -> List[List[Any]]:
    """A superscript "number" sharing its block with real content is an
    affiliation or note marker ("¹ London, UK"), not a page number; a footer
    like "³ Generative AI, Democracy…" keeps it because the rest is furniture."""
    def owner(span):
        return next((line for s, e, line in lines if s <= span[0] <= e), "")
    marked = {id(owner(span)) for span in spans}
    content = [line for _s, _e, line in lines if line.strip() and id(line) not in marked]
    if not content:
        return spans
    return [span for span in spans
            if not (span[2] == "page_number" and re.search(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]", owner(span)))]


def _line_label(line, position, count, block, repeated, edges) -> Optional[str]:
    stripped = line.strip()
    if not stripped:
        return None
    if len(stripped) <= _STRONG_MAX_LINE and (_STRONG_RE.search(stripped) or any(p.search(stripped) for p in _EXTRA)):
        return next((label for label, pattern in _STRONG_LABELS if pattern.search(stripped)), "boilerplate")
    if len(stripped) <= _MAX_LINE and shape(stripped) in repeated and (_WATERMARK.match(stripped) or block.block_id in edges or position == 0 and count > 2):
        if _PAGE_NUMBER.match(stripped):     # "- 3 -", "Page 3" repeat once masked
            return "page_number"
        return "watermark" if _WATERMARK.match(stripped) else "running_head"
    # A publisher footer may put the page number between its journal title
    # and website. All surrounding lines must independently be repeated heads.
    footer_number = count <= 4 and all(
        not other.strip() or _PAGE_NUMBER.fullmatch(other.strip()) or shape(other) in repeated
        for other in block.exact_text.splitlines())
    at_edge = (position in (0, count - 1) or footer_number) and block.block_id in edges
    if at_edge and _PAGE_NUMBER.match(stripped):
        return "page_number"
    return None


def _covers_all(text: str, spans: List[List[Any]]) -> bool:
    covered = [False] * len(text)
    for start, end, _label in spans:
        for index in range(start, min(end, len(text))):
            covered[index] = True
    return all(covered[i] or not char.strip() for i, char in enumerate(text))


def _block_type(spans: List[List[Any]]) -> str:
    labels = {label for _s, _e, label in spans}
    if labels == {"page_number"}:
        return "page_number"
    if labels == {"watermark"}:
        return "watermark"
    if "running_head" in labels and labels <= {"running_head", "page_number"}:
        return "header"
    return "boilerplate"
