"""
core/services/content/deterministic_extractor.py

Deterministic (regex/rule-based) entity extraction engine.

Exposes:
  - ExtractedMention      — one occurrence of an entity in the text
  - ExtractedEntityGroup  — all occurrences of a single deduplicated entity
  - ExtractorSpec         — descriptor for one extraction type (plugin-ready)
  - DeterministicExtractorRegistry — singleton registry; plugins call .register()

Built-in extractors registered at module import:
  - PersonOrgExtractor  →  entity.person_org
  - DateEventExtractor  →  entity.timeline_event
  - CitationExtractor   →  entity.source (with citation metadata)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ExtractedMention:
    text: str          # exact matched text in the document
    context: str       # full sentence containing the match
    page_num: int      # 0-based page index


@dataclass
class ExtractedEntityGroup:
    canonical_text: str              # normalised / deduplicated key shown in UI
    entity_type: str                 # e.g. "entity.person_org"
    mentions: List[ExtractedMention]
    extra_properties: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class ExtractorSpec:
    entity_type: str      # must match an OntologyRegistry key
    display_name: str     # shown in the UI dropdown
    extractor: Callable[  # fn(full_text, page_texts) -> list[ExtractedEntityGroup]
        [str, List[Tuple[int, str]]],
        List[ExtractedEntityGroup],
    ]


class DeterministicExtractorRegistry:
    _extractors: ClassVar[Dict[str, ExtractorSpec]] = {}

    @classmethod
    def register(cls, spec: ExtractorSpec) -> None:
        cls._extractors[spec.entity_type] = spec

    @classmethod
    def unregister(cls, entity_type: str) -> None:
        cls._extractors.pop(entity_type, None)

    @classmethod
    def all_specs(cls) -> List[ExtractorSpec]:
        return list(cls._extractors.values())

    @classmethod
    def get(cls, entity_type: str) -> Optional[ExtractorSpec]:
        return cls._extractors.get(entity_type)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _sentence_for_span(text: str, start: int, end: int, max_chars: int = 600) -> str:
    """Return a 2–3 sentence window centred on the character span [start, end).

    Looks back up to one sentence boundary and forward up to two sentence
    boundaries so citations, people, and events all have enough surrounding
    context for both the UI and the LLM analysis prompt.
    """
    # Walk backward to find start of the sentence that contains `start`
    look_back = max(0, start - 500)
    pre = text[look_back:start]
    last_boundary = max(pre.rfind(". "), pre.rfind(".\n"), pre.rfind("! "), pre.rfind("? "))
    sent_start = look_back + last_boundary + 2 if last_boundary >= 0 else look_back

    # Walk forward: grab up to two sentence boundaries after the entity ends
    remaining = text[end: min(len(text), end + max_chars)]
    boundaries = []
    for pat in (". ", ".\n", "! ", "? "):
        idx = 0
        while True:
            pos = remaining.find(pat, idx)
            if pos == -1:
                break
            boundaries.append(pos + len(pat))
            idx = pos + 1
    boundaries.sort()
    # Use the second boundary when available (two sentences of trailing context)
    if len(boundaries) >= 2:
        sent_end = end + boundaries[1]
    elif len(boundaries) == 1:
        sent_end = end + boundaries[0]
    else:
        sent_end = min(end + max_chars, len(text))

    return text[sent_start:sent_end].strip()


def _page_for_offset(offset: int, page_offsets: List[Tuple[int, int, int]]) -> int:
    """Binary-search page_offsets ([(page_num, start, end), ...]) for a char offset."""
    lo, hi = 0, len(page_offsets) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        _, s, e = page_offsets[mid]
        if offset < s:
            hi = mid - 1
        elif offset >= e:
            lo = mid + 1
        else:
            return page_offsets[mid][0]
    return page_offsets[-1][0] if page_offsets else 0


def _build_full_text(page_texts: List[Tuple[int, str]]) -> Tuple[str, List[Tuple[int, int, int]]]:
    """Join page texts with newlines; return (full_text, page_offsets)."""
    parts = []
    offsets = []
    cursor = 0
    for page_num, page_text in page_texts:
        start = cursor
        parts.append(page_text)
        cursor += len(page_text) + 1  # +1 for the joining newline
        offsets.append((page_num, start, cursor))
    return "\n".join(p for _, p in page_texts), offsets


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# PersonOrgExtractor
# ---------------------------------------------------------------------------

_TITLE_PREFIXES = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sir|Lady|Lord|Senator|President|Captain|General|"
    r"Colonel|Major|Lieutenant|Sergeant|Rev|Reverend|Bishop|Pope|Sheikh|Saint|St)\b\.?"
)
_ORG_SUFFIXES = re.compile(
    r"\b(?:Inc|Corp|LLC|Ltd|GmbH|Co|Group|Foundation|Institute|University|College|"
    r"Hospital|Center|Centre|Bureau|Department|Ministry|Committee|Association|"
    r"Agency|Authority|Council|Commission|Society|Organization|Organisation)\b\.?"
)
_CAPITALIZED_WORD = re.compile(r"[A-Z][a-zéàèùâêîôûëïüç\-']+")
_NOISE_WORDS = frozenset({
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "The", "This", "That", "These", "Those", "There", "Their", "They",
    "Abstract", "Introduction", "Conclusion", "Discussion", "Results", "Methods",
    "References", "Bibliography", "Chapter", "Section", "Figure", "Table",
    "Appendix", "Index", "Preface", "Acknowledgements",
    "North", "South", "East", "West", "United", "States", "Kingdom",
    "American", "European", "Asian", "African", "Australian",
})


def _extract_persons(full_text: str, page_texts: List[Tuple[int, str]],
                     author_exclusions: frozenset) -> List[ExtractedEntityGroup]:
    _, page_offsets = _build_full_text(page_texts)

    # Pattern: optional title + 1-4 capitalized words
    pattern = re.compile(
        r"(?:(?:" + _TITLE_PREFIXES.pattern + r")\s+)?"
        r"(?:[A-Z][a-zéàèùâêîôûëïüç\-']+\.?\s+){1,3}"
        r"[A-Z][a-zéàèùâêîôûëïüç\-']+",
        re.VERBOSE,
    )

    raw_hits: List[Tuple[str, int, int]] = []  # (text, start, end)
    for m in pattern.finditer(full_text):
        name = m.group().strip()
        words = name.split()
        if len(words) < 2:
            continue
        if any(w in _NOISE_WORDS for w in words):
            continue
        # Skip names that are pure bibliography author strings (already in exclusions)
        norm = name.lower().strip(".,;")
        if any(_similar(norm, ex) > 0.85 for ex in author_exclusions):
            continue
        raw_hits.append((name, m.start(), m.end()))

    # Deduplicate: cluster by canonical form (lowercase, strip punctuation)
    groups: Dict[str, List[Tuple[str, int, int]]] = {}
    for name, start, end in raw_hits:
        key = re.sub(r"[^\w\s]", "", name.lower()).strip()
        # Fuzzy merge: find existing key within edit distance
        merged = False
        for existing_key in list(groups.keys()):
            if _similar(key, existing_key) > 0.88:
                groups[existing_key].append((name, start, end))
                merged = True
                break
        if not merged:
            groups[key] = [(name, start, end)]

    result = []
    for key, hits in groups.items():
        # Canonical text = most common surface form
        counts: Dict[str, int] = {}
        for name, _, _ in hits:
            counts[name] = counts.get(name, 0) + 1
        canonical = max(counts, key=counts.__getitem__)

        mentions = []
        seen_ctx: set = set()
        for name, start, end in hits:
            ctx = _sentence_for_span(full_text, start, end)
            if ctx in seen_ctx:
                continue
            seen_ctx.add(ctx)
            page_num = _page_for_offset(start, page_offsets)
            mentions.append(ExtractedMention(text=name, context=ctx, page_num=page_num))

        if mentions:
            # Auto-summary: first two unique context sentences joined, for the editable description field
            auto_desc = " ".join(dict.fromkeys(m.context[:200] for m in mentions[:2]))[:400]
            result.append(ExtractedEntityGroup(
                canonical_text=canonical,
                entity_type="entity.person_org",
                mentions=mentions,
                extra_properties={"role": "", "description": auto_desc},
            ))

    return sorted(result, key=lambda g: -len(g.mentions))


def _run_person_org_extractor(
    full_text: str, page_texts: List[Tuple[int, str]]
) -> List[ExtractedEntityGroup]:
    # Pre-collect bibliography author names for exclusion
    bib_authors = _collect_bibliography_authors(full_text)
    return _extract_persons(full_text, page_texts, bib_authors)


# ---------------------------------------------------------------------------
# DateEventExtractor
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    # Full date: January 15, 2020 / 15 January 2020
    (re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},?\s+\d{4}\b"
    ), lambda m: _normalize_month_day_year(m.group())),
    (re.compile(
        r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{4}\b"
    ), lambda m: _normalize_day_month_year(m.group())),
    # ISO: 2020-01-15
    (re.compile(r"\b(1[89]\d{2}|20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b"),
     lambda m: f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"),
    # Numeric: 01/15/2020 or 15/01/2020 (ambiguous — store as-is)
    (re.compile(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/(1[89]\d{2}|20\d{2})\b"),
     lambda m: f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"),
    # Year-month: January 2020
    (re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(1[89]\d{2}|20\d{2})\b"
    ), lambda m: _normalize_month_year(m.group())),
    # Bare year: in 1995 / in 2020 / of 2019
    (re.compile(r"\b(?:in|of|by|since|until|before|after|around|circa|c\.)\s+(1[89]\d{2}|20\d{2})\b"),
     lambda m: m.group(1)),
    # Decade: the 1990s / early 2000s
    (re.compile(r"\b(?:the\s+)?(?:early|mid|late\s+)?(1[89]\d|20\d)0s\b"),
     lambda m: m.group(0).strip()),
]

_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
           "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}


def _normalize_month_day_year(s: str) -> str:
    parts = re.split(r"[\s,]+", s.strip())
    if len(parts) >= 3:
        m = _MONTHS.get(parts[0].lower(), 0)
        return f"{parts[-1]}-{m:02d}-{int(parts[1].rstrip(',').rstrip('.')):02d}"
    return s


def _normalize_day_month_year(s: str) -> str:
    parts = re.split(r"[\s,]+", s.strip())
    if len(parts) >= 3:
        m = _MONTHS.get(parts[1].lower(), 0)
        return f"{parts[2]}-{m:02d}-{int(parts[0]):02d}"
    return s


def _normalize_month_year(s: str) -> str:
    parts = re.split(r"\s+", s.strip())
    if len(parts) >= 2:
        m = _MONTHS.get(parts[0].lower(), 0)
        return f"{parts[1]}-{m:02d}"
    return s


def _run_date_extractor(
    full_text: str, page_texts: List[Tuple[int, str]]
) -> List[ExtractedEntityGroup]:
    _, page_offsets = _build_full_text(page_texts)

    groups: Dict[str, List[Tuple[str, int, int]]] = {}  # norm_key → [(surface, start, end)]

    for pattern, normaliser in _DATE_PATTERNS:
        for m in pattern.finditer(full_text):
            surface = m.group().strip()
            try:
                norm = normaliser(m)
            except Exception:
                norm = surface
            groups.setdefault(norm, []).append((surface, m.start(), m.end()))

    result = []
    for norm_key, hits in groups.items():
        canonical = hits[0][0]  # first occurrence surface form
        mentions = []
        seen_ctx: set = set()
        for surface, start, end in hits:
            ctx = _sentence_for_span(full_text, start, end)
            if ctx in seen_ctx:
                continue
            seen_ctx.add(ctx)
            page_num = _page_for_offset(start, page_offsets)
            mentions.append(ExtractedMention(text=surface, context=ctx, page_num=page_num))
        if mentions:
            auto_desc = " ".join(dict.fromkeys(m.context[:200] for m in mentions[:2]))[:400]
            result.append(ExtractedEntityGroup(
                canonical_text=canonical,
                entity_type="entity.timeline_event",
                mentions=mentions,
                extra_properties={"date": norm_key, "certainty": "confirmed", "description": auto_desc},
            ))

    return sorted(result, key=lambda g: g.extra_properties.get("date", ""))


# ---------------------------------------------------------------------------
# CitationExtractor
# ---------------------------------------------------------------------------

_BIB_HEADERS = re.compile(
    r"(?im)^[ \t]*"
    r"(?:(?:list\s+of\s+)?references?(?:\s+cited|\s+and\s+notes?|\s+and\s+bibliography)?|"
    r"bibliography(?:\s+and\s+further\s+reading)?|"
    r"works?\s+cited|"
    r"literature\s+cited|"
    r"(?:list\s+of\s+)?sources?(?:\s+cited)?|"
    r"footnotes?|"
    r"endnotes?|"
    r"(?:list\s+of\s+)?citations?)"
    r"[\s.:]*$"
)
_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
_NUMERIC_ENTRY = re.compile(r"^\s*[\[\(]?(\d+)[\]\)\.]\s+")

# Parenthetical: (Smith, 2020), (Smith et al., 2020), (Smith & Jones, 2020),
#                (Smith 2020), (Smith, Jones & Brown, 2020, p. 45)
_AUTHOR_YEAR_INTEXT = re.compile(
    r"\("
    r"([A-Z][a-záéíóúñüÀ-Ö\-]+"                      # first author surname
    r"(?:(?:\s*[,&]\s*|\s+and\s+)"                     # author separator
    r"[A-Z][a-záéíóúñüÀ-Ö\-]+)*"                      # additional authors
    r"(?:,?\s+et\s+al\.?)?"                             # optional et al.
    r")"
    r"[\s,;]+"                                           # separator(s) before year
    r"(1[5-9]\d{2}|20\d{2}[a-z]?)"                    # year
    r"(?:[,;][^)]{0,30})?"                              # optional page/chapter ref
    r"\)",
    re.UNICODE,
)

# Inline: Smith (2020), Smith et al. (2020), Smith and Jones (2020)
_AUTHOR_YEAR_INLINE = re.compile(
    r"([A-Z][a-záéíóúñüÀ-Ö\-]+"
    r"(?:(?:\s+(?:and|&)\s+[A-Z][a-záéíóúñüÀ-Ö\-]+)*)"
    r"(?:,?\s+et\s+al\.?)?"
    r")"
    r"\s+\((1[5-9]\d{2}|20\d{2}[a-z]?)\)",
    re.UNICODE,
)

_NUMERIC_INTEXT = re.compile(r"\[(\d+(?:[,;\s\-–]+\d+)*)\]")

# Unicode superscript citation markers — e.g. ¹²  ³ ⁴⁵ etc.
# These are injected by _extract_pages_fitz via HTML <sup> detection, so they
# represent actual PDF superscripts rather than arbitrary digits.
_SUPERSCRIPT_INTEXT = re.compile(r"([⁰¹²³⁴⁵⁶⁷⁸⁹]+(?:[,\s\-–]*[⁰¹²³⁴⁵⁶⁷⁸⁹]+)*)")


@dataclass
class _CitationEntry:
    key: str
    authors: List[str]
    year: str
    title: str
    journal: str
    raw_text: str
    is_numeric: bool = False
    bib_page_num: int = -1   # 0-based page in the source PDF where the bib entry lives


def _collect_bibliography_authors(full_text: str) -> frozenset:
    """Extract author names from bibliography for PersonOrg exclusion."""
    # Use LAST match so ToC "References" on page 2 doesn't shadow the real section
    bib_matches = list(_BIB_HEADERS.finditer(full_text))
    if not bib_matches:
        return frozenset()
    bib_text = full_text[bib_matches[-1].start():]
    authors = set()
    for line in bib_text.splitlines():
        # Author names precede the year in most citation styles
        year_m = _YEAR_RE.search(line)
        if year_m:
            author_part = line[:year_m.start()].strip().strip("[]()0123456789. ")
            # Split by comma / semicolon / "&" / "and"
            for raw in re.split(r"[,;]|\band\b|&", author_part):
                name = raw.strip().strip(".")
                if 2 <= len(name) <= 50:
                    authors.add(name.lower())
    return frozenset(authors)


_BIB_ENTRY_NUMERIC = re.compile(r"^\s*[\[\(]?(\d{1,3})[\]\)\.]\s+")
_BIB_ENTRY_AUTHOR_START = re.compile(r"^[A-ZÀ-Ö][a-záéíóúñü\-]+(?:,\s+[A-ZÀ-Ö]|\s+[A-ZÀ-Ö])")
_FOOTNOTE_SUPER = re.compile(r"^\s*(\d{1,3})\s+[A-Z]")  # "1 Smith..." footnote style
_SUPERSCRIPT_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUPERSCRIPT_MAP = str.maketrans(_SUPERSCRIPT_CHARS, "0123456789")
# Bib entry that starts with a Unicode superscript number (e.g. "¹Smith, J...")
_BIB_ENTRY_SUPERSCRIPT = re.compile(r"^\s*([⁰¹²³⁴⁵⁶⁷⁸⁹]+)\s*")


def _parse_entry(raw: str) -> Optional["_CitationEntry"]:
    """Parse one raw bibliography/footnote entry string into a _CitationEntry."""
    raw = raw.strip()
    if len(raw) < 10:
        return None

    # Strip numeric prefix [1], (1), 1., 1)
    num_m = _BIB_ENTRY_NUMERIC.match(raw)
    entry_num: Optional[str] = None
    clean = raw
    if num_m:
        entry_num = num_m.group(1)
        clean = raw[num_m.end():]
    else:
        # Try footnote-style: "1 Smith..." (digit-space-Uppercase)
        fn_m = _FOOTNOTE_SUPER.match(raw)
        if fn_m:
            entry_num = fn_m.group(1)
            clean = raw[fn_m.end() - len(fn_m.group(0).lstrip().split()[1]):]  # after digit+space
        else:
            # Try Unicode superscript prefix: "¹Smith..." or "¹ Smith..."
            sup_m = _BIB_ENTRY_SUPERSCRIPT.match(raw)
            if sup_m:
                entry_num = sup_m.group(1).translate(_SUPERSCRIPT_MAP)
                clean = raw[sup_m.end():]

    clean = clean.strip()

    # Find the year (handles APA parenthetical, Chicago inline, or bare year)
    year_m = _YEAR_RE.search(clean)
    year = year_m.group(1) if year_m else ""

    if year_m:
        author_raw = clean[:year_m.start()].strip().strip("().,; ")
        after_year = clean[year_m.end():].strip().strip(".,)( ")
    else:
        # No year found — treat all as author_raw, extract title heuristically
        author_raw = ""
        after_year = clean

    # Author parsing: handles "Last, F.", "First Last", semicolon-separated lists
    def _split_authors(text: str) -> List[str]:
        parts = re.split(r"\s*;\s*|\s+and\s+|\s*&\s*", text)
        out = []
        for p in parts:
            p = p.strip().strip(".,")
            # Each part might be "Last, First" or "First Last" or abbreviation
            if not p or len(p) < 2:
                continue
            # Strip trailing initials artefacts like "J." alone
            if re.fullmatch(r"[A-ZÀ-Ö]\.?", p):
                continue
            out.append(p)
        return out or ([text.strip()] if text.strip() else [])

    # If author_raw contains commas heavily, split on "LastName, F." sub-patterns
    if "," in author_raw:
        # Try APA-style: "Smith, J., Jones, M., & Doe, A."
        # Better: split on ", " when followed by an uppercase initial (author separator)
        apa_parts = re.split(r",\s+(?=[A-ZÀ-Ö]\.?\s|(?:and|&)\s)", author_raw)
        authors_list = [p.strip().strip(".,&").strip() for p in apa_parts if p.strip()]
        # Remove pure-initial entries that slipped through
        authors_list = [a for a in authors_list if len(a) > 2 and not re.fullmatch(r"[A-ZÀ-Ö]\.?", a)]
        if not authors_list:
            authors_list = _split_authors(author_raw)
    else:
        authors_list = _split_authors(author_raw)

    if not authors_list and author_raw:
        authors_list = [author_raw]

    first_author = authors_list[0] if authors_list else ""
    # Derive last name: "Smith, J." → "Smith"; "John Smith" → "Smith"
    if "," in first_author:
        first_author_last = first_author.split(",")[0].strip()
    else:
        parts = first_author.split()
        first_author_last = parts[-1] if parts else first_author

    # Title: first sentence-ending clause after year, or first 120 chars
    title = ""
    journal = ""
    if after_year:
        dot_pos = after_year.find(". ", 2)
        title = after_year[:dot_pos].strip() if dot_pos > 0 else after_year[:120].strip()
        journal = after_year[dot_pos + 2:].strip() if dot_pos > 0 else ""
        # Remove surrounding quotes from title (e.g. MLA style)
        title = title.strip('"\'""''')

    key = f"[{entry_num}]" if entry_num else f"{first_author_last}{year}"

    return _CitationEntry(
        key=key,
        authors=authors_list,
        year=year,
        title=title,
        journal=journal[:120],
        raw_text=raw,
        is_numeric=bool(entry_num),
    )


def _parse_bibliography(full_text: str) -> Tuple[List[_CitationEntry], bool, int]:
    """
    Locate bibliography / footnote sections in full_text and parse all entries.

    Returns (entries, numeric_style, bib_char_offset).
    bib_char_offset is -1 when no bibliography section was found.

    Handles: numbered [N]/(N)/N., author-year, footnote-style, entries that
    span page breaks (i.e. are split across lines in the PDF extraction).
    """
    # Collect all bibliography section start offsets.
    # Use the LAST match — "References" in a Table of Contents appears early;
    # the actual bibliography is always near the end of the document.
    bib_starts = [(m.start(), m.end()) for m in _BIB_HEADERS.finditer(full_text)]
    if not bib_starts:
        return [], False, -1

    bib_offset = bib_starts[-1][0]  # last occurrence = actual bibliography
    bib_text = full_text[bib_offset:][:40_000]

    lines = bib_text.splitlines()
    if not lines:
        return [], False

    # Detect style from first non-header, non-blank line
    numeric_style = False
    for line in lines[1:6]:
        stripped = line.strip()
        if not stripped:
            continue
        if (_BIB_ENTRY_NUMERIC.match(stripped) or _FOOTNOTE_SUPER.match(stripped)
                or _BIB_ENTRY_SUPERSCRIPT.match(stripped)):
            numeric_style = True
        break

    # Segment into raw entry blocks.
    # A new entry starts when:
    #   - A blank line is encountered (flush current, start fresh)
    #   - A numeric marker appears [N]/(N)/N. (numeric style, any position)
    #   - A line starts with an author-name pattern AND contains a year
    #     (author-year style, with or without preceding blank line)
    raw_entries: List[str] = []
    current: List[str] = []
    prev_blank = False

    for line in lines[1:]:
        stripped = line.strip()
        normalised = stripped.translate(_SUPERSCRIPT_MAP)

        if not stripped:
            prev_blank = True
            if current:
                raw_entries.append(" ".join(current))
                current = []
            continue

        is_new_numbered = bool(_BIB_ENTRY_NUMERIC.match(normalised) or _FOOTNOTE_SUPER.match(normalised))
        # Author-year: split when line starts with "Last, I" or "Last First" AND has a year.
        # The year requirement avoids splitting on continuation lines that happen to start
        # with a capitalised proper noun.
        is_new_author = (
            bool(_BIB_ENTRY_AUTHOR_START.match(normalised))
            and bool(_YEAR_RE.search(normalised))
        )

        starts_new_entry = (
            (numeric_style and is_new_numbered)
            or (not numeric_style and is_new_author and current)
        )

        if starts_new_entry:
            raw_entries.append(" ".join(current))
            current = [stripped]
        else:
            current.append(stripped)

        prev_blank = False

    if current:
        raw_entries.append(" ".join(current))

    # Fallback: if still only 1 block, try splitting on numeric markers inline
    if len(raw_entries) <= 1 and len(bib_text) > 200:
        combined = " ".join(line.strip() for line in lines[1:])
        splits = re.split(r"(?<=\s)[\[\(]?\d{1,3}[\]\)\.]\s+(?=[A-Z])", combined)
        if len(splits) > 2:
            raw_entries = [s.strip() for s in splits if s.strip()]

    entries: List[_CitationEntry] = []
    seen_keys: set = set()
    for raw in raw_entries:
        entry = _parse_entry(raw)
        if entry and entry.key not in seen_keys:
            seen_keys.add(entry.key)
            entries.append(entry)

    return entries, numeric_style, bib_offset


def _run_citation_extractor(
    full_text: str, page_texts: List[Tuple[int, str]]
) -> List[ExtractedEntityGroup]:
    _, page_offsets = _build_full_text(page_texts)

    # Phase 1: parse bibliography
    entries, numeric_style, bib_char_offset = _parse_bibliography(full_text)

    # Page number where the bibliography section begins (0-based)
    bib_page_num = _page_for_offset(bib_char_offset, page_offsets) if bib_char_offset >= 0 else -1

    # Restrict in-text scanning to body text (before the LAST bibliography header).
    # Using the last match avoids false positives from ToC entries like "References...58".
    bib_matches = list(_BIB_HEADERS.finditer(full_text))
    bib_start = bib_matches[-1].start() if bib_matches else len(full_text)
    body_text = full_text[:bib_start]

    # Phase 2: build lookup tables for matching in-text → bib entry
    key_map: Dict[str, _CitationEntry] = {e.key: e for e in entries}
    author_year_map: Dict[str, _CitationEntry] = {}
    for e in entries:
        if not e.is_numeric and e.year:
            # Index ALL authors so "Jones (2020)" matches a "Smith & Jones (2020)" entry
            for author in e.authors:
                author = author.strip()
                if not author:
                    continue
                if "," in author:
                    last_name = author.split(",")[0].strip()
                else:
                    parts = author.split()
                    last_name = parts[-1] if parts else author
                if len(last_name) > 1:
                    ak = f"{last_name.lower()}{e.year}"
                    author_year_map.setdefault(ak, e)  # first-author wins ties

    # Phase 3: collect in-text hits from body text only
    hits: Dict[str, List[Tuple[str, int, int]]] = {}

    if numeric_style or not entries:
        # 1. Standard bracketed: [1], [2, 3], [4-6]
        for m in _NUMERIC_INTEXT.finditer(body_text):
            inner = m.group(1)
            # Find all standalone numbers and ranges
            tokens = re.findall(r'\d+(?:\s*[\-–]\s*\d+)?', inner)
            for token in tokens:
                token = token.strip()
                if '-' in token or '–' in token:
                    # Expand ranges (e.g. "4-6" becomes 4, 5, 6)
                    parts = re.split(r'[\-–]', token)
                    start, end = int(parts[0].strip()), int(parts[-1].strip())
                    if 1 <= start < end <= 200: # Sanity bound
                        for n in range(start, end + 1):
                            hits.setdefault(f"[{n}]", []).append((m.group(), m.start(), m.end()))
                elif token.isdigit():
                    hits.setdefault(f"[{token}]", []).append((m.group(), m.start(), m.end()))

        # 2. Unicode superscript chars: ¹², ³⁻⁵ etc.
        for m in _SUPERSCRIPT_INTEXT.finditer(body_text):
            nums_str = m.group(1).translate(_SUPERSCRIPT_MAP)
            tokens = re.findall(r'\d+(?:\s*[\-–]\s*\d+)?', nums_str)
            for token in tokens:
                token = token.strip()
                if '-' in token or '–' in token:
                    parts = re.split(r'[\-–]', token)
                    start, end = int(parts[0].strip()), int(parts[-1].strip())
                    if 1 <= start < end <= 200:
                        for n in range(start, end + 1):
                            hits.setdefault(f"[{n}]", []).append((m.group(1), m.start(), m.end()))
                elif token.isdigit():
                    # Treat contiguous digits as a single number to prevent false splits
                    hits.setdefault(f"[{token}]", []).append((m.group(1), m.start(), m.end()))
    # Author-year patterns — skip if numeric style (would create false orphan groups)
    if not numeric_style:
        # Parenthetical: (Smith, 2020) or (Smith et al., 2020)
        for m in _AUTHOR_YEAR_INTEXT.finditer(body_text):
            last = m.group(1).split()[0].rstrip(",.;:")
            year = m.group(2)
            ak = f"{last.lower()}{year[:4]}"
            entry = author_year_map.get(ak)
            k = entry.key if entry else f"{last}{year[:4]}"
            hits.setdefault(k, []).append((m.group(), m.start(), m.end()))

        # Inline: Smith (2020) found that... — only add when matched to a known bib entry
        # (too noisy as orphan detector; avoids false "Jones2020" from "Smith and Jones (2020)")
        for m in _AUTHOR_YEAR_INLINE.finditer(body_text):
            last = m.group(1).split()[0].rstrip(",.;:")
            year = m.group(2)
            ak = f"{last.lower()}{year[:4]}"
            entry = author_year_map.get(ak)
            if entry:  # only record when we can tie it to a known bibliography entry
                hits.setdefault(entry.key, []).append((m.group(), m.start(), m.end()))

    # Phase 4: build one group per bibliography entry, attaching all citing sentences
    def _make_mentions(hit_list: List[Tuple[str, int, int]]) -> List[ExtractedMention]:
        seen_ctx: set = set()
        result_mentions = []
        for surface, start, end in hit_list:
            ctx = _sentence_for_span(full_text, start, end)
            if ctx in seen_ctx:
                continue
            seen_ctx.add(ctx)
            result_mentions.append(ExtractedMention(
                text=surface,
                context=ctx,
                page_num=_page_for_offset(start, page_offsets),
            ))
        return result_mentions

    def _make_group(entry: _CitationEntry, mentions: List[ExtractedMention]) -> ExtractedEntityGroup:
        return ExtractedEntityGroup(
            canonical_text=entry.title or entry.key,
            entity_type="entity.source",
            mentions=mentions,
            extra_properties={
                "citation_key": entry.key,
                "authors": entry.authors,
                "year": entry.year,
                "title": entry.title,
                "journal": entry.journal,
                "bibliography_raw": entry.raw_text,
                "bib_page_num": bib_page_num,
            },
        )

    result: List[ExtractedEntityGroup] = []
    matched_keys: set = set()

    # Entries with bibliography — one card each, body = citing sentences
    for entry in entries:
        matched_keys.add(entry.key)
        mentions = _make_mentions(hits.get(entry.key, []))
        result.append(_make_group(entry, mentions))

    # In-text citations with no matching bib entry (orphans) — still show them
    for k, hit_list in hits.items():
        if k in matched_keys:
            continue
        orphan = _CitationEntry(key=k, authors=[], year="", title=k, journal="", raw_text=k)
        mentions = _make_mentions(hit_list)
        if mentions:
            result.append(_make_group(orphan, mentions))

    # Sort: entries with in-text matches first, then bib-only, sorted by mention count desc
    return sorted(result, key=lambda g: -len(g.mentions))


# ---------------------------------------------------------------------------
# Register built-in extractors at import time
# ---------------------------------------------------------------------------

DeterministicExtractorRegistry.register(ExtractorSpec(
    entity_type="entity.person_org",
    display_name="People & Organizations",
    extractor=_run_person_org_extractor,
))

DeterministicExtractorRegistry.register(ExtractorSpec(
    entity_type="entity.timeline_event",
    display_name="Dates & Events",
    extractor=_run_date_extractor,
))

DeterministicExtractorRegistry.register(ExtractorSpec(
    entity_type="entity.source",
    display_name="Citations",
    extractor=_run_citation_extractor,
))
