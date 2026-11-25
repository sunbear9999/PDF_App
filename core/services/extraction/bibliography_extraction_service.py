"""
BibliographyExtractionService — detect, segment, and normalize bibliography entries.

Replaces the duplicated _parse_bibliography() logic from both
CitationExtractor (deterministic.py) and DocumentIngestionService.

Stages:
  1. detect_bibliography_blocks() — find blocks classified as bibliography
  2. segment_entries()            — split blocks into individual BibliographyEntry objects
  3. normalize_entry()            — extract authors, year, title, DOI, etc.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from core.services.extraction.author_year_citations import YEAR_RE as _YEAR_RE
from core.services.extraction.semantic_block_service import BIBLIOGRAPHY_HEADING_RE

from core.services.extraction.bibliography_layout import _lines, _with_x0, _at_margin, _hanging_margins
from core.services.extraction.block_roles import content_text
from core.models.extraction_models import (
    BibliographyEntry,
    DocumentBlock,
    ExtractionProvenance,
    SourceLocation,
)

_EXTRACTOR_VERSION = "1.8"
_MAX_TITLE_CHARS = 200
_MIN_ENTRY_CHARS = 10

# ---- Segmentation patterns ------------------------------------------------

_DOI_RE = re.compile(r"\b10\.\d{4,}/\S+", re.I)
_URL_RE = re.compile(r"https?://\S+")
# "Smith, J.", "Olaizola Rosenblat, Mariana", "Strategic Communications, Task …"
_AUTHOR_START_RE = re.compile(
    r"^[A-ZÀ-Þ][\w’'\-]+(?:\s+[A-ZÀ-Þ][\w’'\-]+){0,3},\s+[A-ZÀ-Þ]"
)
_AND_SEP_RE = re.compile(r"[;&]|\band\b", re.I)

# ---- Author name parsing ---------------------------------------------------

# Leading "[12] " or "12. " reference marker.
_REF_MARKER_RE = re.compile(r"^\s*(?:\[(\d+)\]\s*|\((\d+)\)\s*|(\d+)[.)]\s+|(\d{1,3})\s+(?=[A-Z]))")
# End of the title clause: a period (or the start of a journal/volume run)
# that is not part of an initial or a common abbreviation.
_TITLE_END_RE = re.compile(r"(?<=[a-z0-9\)\]])\.(?:\s|$)")
# A line that plausibly begins a new reference: a marker, or a name/word start.
# A line beginning mid-token (a bare digit run, a lowercase continuation) is a
# wrapped continuation of the entry above it.
_ENTRY_START_RE = re.compile(r"^(?:\[\d+\]|\d+\.\s|[A-Z\u00C0-\u024F])")
# "Adee, Sally. 2020." / "AI Elections Accord. 2024." — an author run closed by a
# period, then the publication year closed by a period. This is what separates a
# real entry start from a wrapped continuation line ("Spectrum, April 29.
# Updated March 8, 2024."), which line-level heuristics cannot tell apart.
_APA_ENTRY_START_RE = re.compile(
    r"^\s*[A-Z\u00C0-\u024F][^.]{1,80}?\.\s*(?:1[5-9]\d{2}|20\d{2})[a-z]?\."
)
# A block holding only the section heading is not a reference.
# Opening and closing quotes are kept separate: treating a right single quote as
# a closing delimiter truncated titles at the first apostrophe ("can't").
_OPEN_QUOTES = "\u201c\u2018\u00ab\""
_CLOSE_QUOTES = "\u201d\u00bb\""
_QUOTE_CHARS = _OPEN_QUOTES + _CLOSE_QUOTES + "\u2019'"
_MIN_TITLE_CHARS = 4
_HEADING_ONLY_RE = BIBLIOGRAPHY_HEADING_RE


_URLISH_END_RE = re.compile(r"(?:https?://\S+|\b10\.\d{4,}/\S+|\.|\)|\]|”|\")\s*$")
_DITTO_RE = re.compile(r"^\s*(?:[—–]|-{2,}|_{3,})\s*[.,]?")




_MARKER_ONLY_RE = re.compile(r"^\s*(?:\[\d{1,3}\]|\(\d{1,3}\)|\d{1,3}[.)])\s*$")
_NUMBER_START_RE = re.compile(r"^\s*(?:\[\d{1,3}\]|\(\d{1,3}\)|\d{1,3}[.)](?:\s|$))")


_CONTINUATION_START = re.compile(r"^(?:accessed|retrieved|available|https?://|www\.|doi\b|10\.\d{4,}/)", re.I)


def _begins_entry(line: str, previous: str, after_marker: bool = False, numbered: bool = False,
                  at_margin: Optional[bool] = None, previous_at_margin: Optional[bool] = None) -> bool:
    """Entry starts are name patterns, never just a capital: "Columbia
    University Press, 2006." and "Frontiers: A Journal …" continue an entry.

    ``at_margin`` is the hanging-indent signal when the source has geometry:
    False — the line is indented, so it continues an entry; True — it sits at
    the list's left edge, so any opening (a quoted title, "Donald J. Trump:")
    starts an entry after an indented line, or after an unindented line that
    ended an entry (an entry set without its hanging indent)."""
    stripped = line.strip()
    if not previous:
        return True
    if after_marker:
        return False            # "1." on its own line; this line is its text
    if _NUMBER_START_RE.match(stripped) and not _continued_range(previous, stripped):
        return True             # a reference number is decisive
    if numbered:
        return False            # numbered list: "Oxford University Press, Oxford (2014)" continues
    if re.match(r"^\(?(?:1[5-9]\d{2}|20\d{2})\)?[.]?\s", stripped):
        return False            # publication year wrapped after an author list
    if previous.rstrip().endswith((",", " and")):
        return False            # a continued author list, even if it contains the year
    if (_DITTO_RE.match(stripped) and _YEAR_RE.search(stripped[:25])) or (
        _AUTHOR_START_RE.match(stripped) and _plausible_author_start(stripped)
        and _YEAR_RE.search(stripped[:100])
    ):
        return True
    if at_margin is False:
        return False
    if at_margin is True:
        if _CONTINUATION_START.match(stripped):
            return False
        if previous_at_margin is False:
            return True
        return _ends_entry(previous) and not previous.rstrip().endswith("-")
    starts = (_APA_ENTRY_START_RE.match(stripped) or _DITTO_RE.match(stripped)
              or (_AUTHOR_START_RE.match(stripped) and _plausible_author_start(stripped)))
    return bool(starts) and _ends_entry(previous)


_MONTH_RE = re.compile(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d", re.I)


def _plausible_author_start(line: str) -> bool:
    """"Allyn, Bobby." / "Kim, Jean J." — not "NPR, October 30.", "Santa
    Monica, CA: RAND." or "Opinion, Centre for International …"."""
    after = line.split(",", 1)[1].strip() if "," in line else ""
    words = after.split()
    if not words or _MONTH_RE.match(after) or re.match(r"^[A-Z]{2}\b[:,]?", after):
        return False
    return not (len(words) > 1 and words[1][:1].islower()
                and words[1].lower().rstrip(".,") not in {"and", "de", "van", "von", "da", "ed", "eds", "editor", "editors"})



def _numbered_list(lines: List[str]) -> bool:
    """Most references start with their number ("12.", "[12]")."""
    numbers = [int(m.group(1)) for line in lines for m in [re.match(r"^\s*\[?(\d{1,3})[.)\]]", line)] if m]
    return len(numbers) >= 5 and sum(1 for a, b in zip(numbers, numbers[1:]) if b == a + 1) >= 0.6 * len(numbers)


def _continued_range(previous: str, line: str) -> bool:
    """"453. https://…" after "…73(1):439-" is a wrapped page range."""
    return previous.rstrip().endswith(("-", "–", ":", ","))


def _ends_entry(line: str) -> bool:
    stripped = line.strip()
    # the wrapped tail of a URL ("founding-of-new-parties") also ends an entry
    url_tail = len(stripped) >= 6 and " " not in stripped and re.search(r"[/\-]", stripped) \
        and not stripped.endswith("-")
    return bool(_URLISH_END_RE.search(stripped) or url_tail)


def _inherit_ditto_authors(entries: List[BibliographyEntry]) -> None:
    """"———. 2024. Title" repeats the previous entry's authors."""
    for previous, entry in zip(entries, entries[1:]):
        if _DITTO_RE.match(entry.raw_text):
            entry.authors = list(previous.authors)
            entry.first_author_surname = previous.first_author_surname
            year = entry.year or ""
            surname = (previous.first_author_surname or "").lower()
            if surname:
                entry.normalized_keys["surname_year"] = f"{surname}_{year}" if year else surname


def _strip_ref_marker(raw: str) -> str:
    """Drop a leading "[12] " / "12. " marker so it cannot leak into a name."""
    return _REF_MARKER_RE.sub("", raw, count=1)


def has_reference_start(text: str) -> bool:
    """A reference-list entry starts with a number or a recognizable author run."""
    clean = text.lstrip()
    return bool(_REF_MARKER_RE.match(clean) or _AUTHOR_START_RE.match(clean)
                or _APA_ENTRY_START_RE.match(clean))


def _parse_authors(raw: str) -> Tuple[List[str], str]:
    """Return (author_list, first_author_surname). Best-effort."""
    # Remove the reference marker first: leaving it in produced surnames like
    # "1. IEA (). Energy", which then matched nothing.
    body = _strip_ref_marker(raw)
    year = _YEAR_RE.search(body)
    if year:
        author_segment = body[:year.start()].strip(" .,()")
    else:
        author_segment = re.split(r"\.\s+(?=[A-Z][a-z]{2,})|[“\"]", body, maxsplit=1)[0]
    # A year may follow an author run and title. Keep the author clause, while
    # initials (A., J. R.) never terminate that clause.
    author_segment = re.split(r"(?<=[a-z])\.\s+", author_segment, maxsplit=1)[0]
    authors: List[str] = []
    for part in _AND_SEP_RE.split(author_segment):
        part = part.strip(" .,()")
        if len(part) < 2:
            continue
        if "," in part:
            surname = part.split(",", 1)[0].strip()
        elif re.search(r"\b[A-Z]\.", part):
            surname = part.split()[-1].strip(" .")
        else:
            surname = part
        if surname:
            authors.append(surname)

    first_surname = authors[0] if authors else ""
    return authors, first_surname


def _clean_title(title: str) -> Optional[str]:
    """Trim a candidate title, or drop it when it is not a title at all.

    Anything left over from a stripped URL or DOI ("doi/abs/", "id=IW1PR7vEBf")
    is discarded so the citation card falls back to the full reference text.
    """
    cleaned = title.strip(_QUOTE_CHARS + " .,:;/")
    if len(cleaned) < _MIN_TITLE_CHARS:
        return None
    if len(re.findall(r"[^\W\d_]{2,}", cleaned)) < 2:
        return None
    first = cleaned.split()[0]
    if "/" in first or "=" in first:
        return None
    return cleaned


_AUTHOR_DATE_RE = re.compile(r"^[^.]{1,200}?\.\s*(?:1[5-9]|20)\d{2}[a-z]?\.")


def _guess_style(raw: str, ref_num: Optional[str]) -> str:
    """numbered | author_year (APA/Chicago author-date: "Authors. 2021.") |
    legal | author_title (MLA/Chicago notes: year after the title) | unknown."""
    if ref_num:
        return "numbered"
    if _YEAR_RE.search(raw[:80]) or _AUTHOR_DATE_RE.match(raw):
        return "author_year"
    if re.search(r"\bv\.\s*\d+|§\s*\d+|\bF\.\d+[a-z]?\b", raw):
        return "legal"
    if _AUTHOR_START_RE.match(raw) and _YEAR_RE.search(raw):
        return "author_title"
    return "unknown"


def _entry_title(raw, year):
    title: Optional[str] = None
    m_title = re.search(rf"[{_OPEN_QUOTES}](.{{10,150}}?)[{_CLOSE_QUOTES}]", raw)
    if m_title:
        title = m_title.group(1).strip()
    elif year and year in raw:
        after = raw[raw.index(year) + len(year):]
        after = _URL_RE.sub("", _DOI_RE.sub("", after)).strip(" .,)")
        # Stop at the end of the title clause; the old code kept the
        # journal, volume and page run, and that 200-char blob produced
        # spurious "title phrase overlap" matches for unrelated citations.
        end = _TITLE_END_RE.search(after)
        title = (after[:end.start()] if end else after)[:_MAX_TITLE_CHARS].strip()

    return _clean_title(title) if title else None


def _entry_fields(raw):
    # Reference number
    ref_num: Optional[str] = None
    m_num = _REF_MARKER_RE.match(raw)
    if m_num:
        ref_num = next(value for value in m_num.groups() if value is not None)

    # DOI / URL
    doi = _DOI_RE.search(raw)
    url = _URL_RE.search(raw)

    # Year
    year_match = _YEAR_RE.search(raw)
    year = year_match.group(1) if year_match else None

    authors, first_surname = _parse_authors(raw)

    title = _entry_title(raw, year)

    citation_style = _guess_style(raw, ref_num)

    norm_keys: Dict[str, str] = {}
    if first_surname and year:
        norm_keys["surname_year"] = f"{first_surname.lower()}_{year}"
    if doi:
        norm_keys["doi"] = doi.group(0).lower()
    if ref_num:
        norm_keys["numeric"] = ref_num

    return {"authors": authors, "first_author_surname": first_surname,
            "year": year, "title": title, "doi": doi.group(0) if doi else None,
            "url": url.group(0) if url else None, "reference_number": ref_num,
            "citation_style": citation_style, "normalized_keys": norm_keys}


class BibliographyExtractionService:
    def __init__(self, pattern_registry=None) -> None:
        self._registry = pattern_registry

    # ------------------------------------------------------------------

    def detect_bibliography_blocks(
        self, blocks: List[DocumentBlock]
    ) -> List[DocumentBlock]:
        """Return blocks already classified as 'bibliography' by SectionStructureExtractor."""
        return [b for b in blocks if b.block_type == "bibliography"]

    def segment_entries(
        self, bib_blocks: List[DocumentBlock], doc_id: str
    ) -> List[BibliographyEntry]:
        """
        Split bibliography blocks into individual BibliographyEntry objects.

        Uses six segmentation signals in priority order:
          1. Numbered markers [N] or N.
          2. Author-name line starts
          3. Year patterns in hanging-indent structure
          4. DOI/URL boundaries
          5. Alphabetical ordering clues
          6. Paragraph/line boundaries
        """
        if not bib_blocks:
            return []

        # Paragraph reconstruction makes a block a reference (hanging indent),
        # so blocks are the primary boundary; a block may still hold several
        # numbered/APA entries, and an entry may continue across a column or
        # page break. Every entry keeps its own page and exact character span.
        units = self._block_units(bib_blocks)
        entries: List[BibliographyEntry] = []
        for line_index, (raw, block, start, end, spans) in enumerate(units):
            raw = raw.strip()
            if len(raw) < _MIN_ENTRY_CHARS or _HEADING_ONLY_RE.match(raw):
                continue
            entry = self.normalize_entry(raw, doc_id, block.page_num, block.block_id, line_index=line_index)
            entry.location.char_start, entry.location.char_end = start, end
            entry.source_spans = spans
            entries.append(entry)
        _inherit_ditto_authors(entries)
        return entries

    def _block_units(self, bib_blocks: List[DocumentBlock]) -> list:
        """Entry text, first location and all contributing source spans.

        A reference number ("12.", "[12]") always starts an entry. An author
        or "Author. YEAR." line starts one only after a line that ended the
        previous entry; anything else continues the current entry, including
        a block that resumes an entry after a column or page break.
        """
        units: List[List[Any]] = []
        numbered = _numbered_list([line for block in bib_blocks for _s, _e, line in _lines(content_text(block))])
        margins = _hanging_margins(bib_blocks, lambda line: _AUTHOR_START_RE.match(line) and _plausible_author_start(line))
        previous_margin: Optional[bool] = None
        for block in bib_blocks:
            lines = [(s, e, line, x0) for (s, e, line), x0 in _with_x0(block)
                     if line.strip(" \t.;:") and not _HEADING_ONLY_RE.match(line.strip(" \t.;:"))]
            for k, (start, end, line, x0) in enumerate(lines):
                span = {"block_id": block.block_id, "page": block.page_num,
                        "char_start": start, "char_end": end}
                previous = lines[k - 1][2] if k else (units[-1][0].splitlines()[-1] if units else "")
                marker = bool(units) and bool(_MARKER_ONLY_RE.match(previous))
                at_margin = _at_margin(margins, block, x0)
                if _begins_entry(line, previous, after_marker=marker, numbered=numbered,
                                 at_margin=at_margin, previous_at_margin=previous_margin):
                    units.append([line, block, start, end, [span]])
                else:
                    units[-1][0] += "\n" + line
                    if units[-1][1] is block:
                        units[-1][3] = end
                    previous_span = units[-1][4][-1]
                    # Merge only adjacent lines. Furniture removed between
                    # them must never be highlighted as reference text.
                    if previous_span["block_id"] == block.block_id and start <= previous_span["char_end"] + 1:
                        previous_span["char_end"] = end
                    else:
                        units[-1][4].append(span)
                previous_margin = at_margin
        return [tuple(unit) for unit in units]

    def normalize_entry(
        self,
        raw_text: str,
        doc_id: str,
        page_num: int = 0,
        block_id: str = "",
        line_index: int = 0,
    ) -> BibliographyEntry:
        """Parse a raw bibliography entry string into a structured BibliographyEntry."""
        raw = raw_text.strip()

        fields = _entry_fields(raw)

        entry_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{doc_id}|{block_id}|{line_index}|{raw}",
        ))
        return BibliographyEntry(
            entry_id=entry_id,
            doc_id=doc_id,
            raw_text=raw,
            **fields,
            location=SourceLocation(
                doc_id=doc_id, page_num=page_num, block_id=block_id
            ),
            provenance=ExtractionProvenance(
                extractor_name="bibliography_extraction_service",
                extractor_version=_EXTRACTOR_VERSION,
                parser_provider="pymupdf_layout",
                method="regex",
                confidence=0.8,
            ),
        )

    # ------------------------------------------------------------------

    def _split_entries(self, lines: List[str]) -> List[Tuple[str, int]]:
        # Retained for line-only callers (plain-text bibliographies).
        """Split bibliography lines into (entry_text, first_line_index) pairs."""
        # Strategy 1: numbered references [1], 1. etc.
        numbered: List[Tuple[int, int]] = []   # (line_idx, ref_num)
        for i, line in enumerate(lines):
            m = _REF_MARKER_RE.match(line)
            if m:
                numbered.append((i, int(next(value for value in m.groups() if value is not None))))

        # Strategy 2: "Authors. YEAR." entry starts. Precise enough to keep a
        # wrapped continuation line from starting a new reference.
        apa_lines = [i for i, line in enumerate(lines) if _APA_ENTRY_START_RE.match(line)]

        # Strategy 3: author-name line starts (capitalised word, comma, initial)
        author_lines: List[int] = []
        for i, line in enumerate(lines):
            if _AUTHOR_START_RE.match(line.strip()):
                author_lines.append(i)
        starts = sorted(set([i for i, _num in numbered] + apa_lines + author_lines))
        if starts:
            return self._split_by_line_indices(lines, starts)

        # Strategy 4: year-anchored splitting, but only at lines that plausibly
        # start an entry. Splitting at every line containing a year cut entries
        # mid-identifier and produced fragments like "00434" from an arXiv id.
        year_lines: List[int] = [
            i for i, line in enumerate(lines)
            if i > 0 and _YEAR_RE.search(line) and _ENTRY_START_RE.match(line.strip())
        ]
        if len(year_lines) >= 2:
            return self._split_by_line_indices(lines, year_lines)

        # Fallback: treat each non-empty line as an entry
        return [(ln, i) for i, ln in enumerate(lines) if ln.strip()]

    @staticmethod
    def _split_by_line_indices(
        lines: List[str], start_indices: List[int]
    ) -> List[Tuple[str, int]]:
        entries: List[Tuple[str, int]] = []
        for k, start in enumerate(start_indices):
            end = start_indices[k + 1] if k + 1 < len(start_indices) else len(lines)
            chunk = "\n".join(lines[start:end]).strip()
            if chunk:
                entries.append((chunk, start))
        return entries
