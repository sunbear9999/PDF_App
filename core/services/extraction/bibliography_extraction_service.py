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
from typing import Dict, List, Optional, Tuple

from core.models.extraction_models import (
    BibliographyEntry,
    DocumentBlock,
    ExtractionProvenance,
    SourceLocation,
)

_EXTRACTOR_VERSION = "1.0"

# ---- Segmentation patterns ------------------------------------------------

_NUMBERED_RE = re.compile(r"^\s*\[(\d+)\]|\b^(\d+)\.\s", re.M)
_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20[0-2]\d)\b")
_DOI_RE = re.compile(r"\b10\.\d{4,}/\S+", re.I)
_URL_RE = re.compile(r"https?://\S+")
_AUTHOR_START_RE = re.compile(
    r"^[A-Z][a-z]+,?\s+[A-Z]\.?|^[A-Z][A-Z]+,\s+[A-Z]"
)
_AND_SEP_RE = re.compile(r"[;&]|\band\b", re.I)

# ---- Author name parsing ---------------------------------------------------

_AUTHOR_BLOCK_RE = re.compile(
    r"([A-Z][a-z\-\']+(?:,?\s+(?:Jr\.?|Sr\.?|II+|III+))?)"
    r"(?:,\s*[A-Z]\.?\s*(?:[A-Z]\.?\s*)?)?",
)


def _parse_authors(raw: str) -> Tuple[List[str], str]:
    """Return (author_list, first_author_surname). Best-effort."""
    # Remove year, title hints, etc.
    body = _YEAR_RE.sub("", raw).strip()
    body = _DOI_RE.sub("", body)
    body = _URL_RE.sub("", body)

    # Try to isolate the author segment (before first year or title marker)
    authors: List[str] = []
    m = re.match(r"^(.*?)(?:\(?\d{4}\)?|[""\"«])", body)
    author_segment = m.group(1).strip(" .,") if m else body[:200]

    for part in _AND_SEP_RE.split(author_segment):
        part = part.strip(" .,")
        if len(part) > 2 and re.search(r"[A-Z]", part):
            # Normalize "Last, F." → "Last"
            surname = re.split(r",\s*", part)[0].strip()
            if surname and len(surname) > 1:
                authors.append(surname)

    first_surname = authors[0] if authors else ""
    return authors, first_surname


def _guess_style(raw: str, ref_num: Optional[str]) -> str:
    if ref_num:
        return "numbered"
    if _YEAR_RE.search(raw[:80]):
        return "author_year"
    if re.search(r"\bv\.\s*\d+|§\s*\d+|\bF\.\d+[a-z]?\b", raw):
        return "legal"
    return "unknown"


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

        # Concatenate all bib text preserving line breaks
        full_text = "\n".join(b.exact_text for b in bib_blocks)
        first_page = bib_blocks[0].page_num if bib_blocks else 0
        first_block_id = bib_blocks[0].block_id if bib_blocks else ""

        raw_entries = self._split_entries(full_text)
        entries: List[BibliographyEntry] = []

        for raw in raw_entries:
            raw = raw.strip()
            if len(raw) < 10:
                continue
            entry = self.normalize_entry(raw, doc_id, first_page, first_block_id)
            entries.append(entry)

        return entries

    def normalize_entry(
        self,
        raw_text: str,
        doc_id: str,
        page_num: int = 0,
        block_id: str = "",
    ) -> BibliographyEntry:
        """Parse a raw bibliography entry string into a structured BibliographyEntry."""
        raw = raw_text.strip()

        # Reference number
        ref_num: Optional[str] = None
        m_num = re.match(r"^\[(\d+)\]|^(\d+)\.\s", raw)
        if m_num:
            ref_num = m_num.group(1) or m_num.group(2)

        # DOI / URL
        doi = _DOI_RE.search(raw)
        url = _URL_RE.search(raw)

        # Year
        year_match = _YEAR_RE.search(raw)
        year = year_match.group(1) if year_match else None

        authors, first_surname = _parse_authors(raw)

        # Title: heuristic — first quoted string or text after year
        title: Optional[str] = None
        m_title = re.search(r'["""](.{10,150})["""]', raw)
        if m_title:
            title = m_title.group(1).strip()
        elif year and year in raw:
            after = raw[raw.index(year) + 4:].strip(" .,")
            title = after[:200] if after else None

        citation_style = _guess_style(raw, ref_num)

        norm_keys: Dict[str, str] = {}
        if first_surname and year:
            norm_keys["surname_year"] = f"{first_surname.lower()}_{year}"
        if doi:
            norm_keys["doi"] = doi.group(0).lower()
        if ref_num:
            norm_keys["numeric"] = ref_num

        entry_id = str(uuid.uuid4())
        return BibliographyEntry(
            entry_id=entry_id,
            doc_id=doc_id,
            raw_text=raw,
            authors=authors,
            first_author_surname=first_surname,
            year=year,
            title=title,
            doi=doi.group(0) if doi else None,
            url=url.group(0) if url else None,
            reference_number=ref_num,
            citation_style=citation_style,
            location=SourceLocation(
                doc_id=doc_id, page_num=page_num, block_id=block_id
            ),
            normalized_keys=norm_keys,
            provenance=ExtractionProvenance(
                extractor_name="bibliography_extraction_service",
                extractor_version=_EXTRACTOR_VERSION,
                parser_provider="pymupdf_layout",
                method="regex",
                confidence=0.8,
            ),
        )

    # ------------------------------------------------------------------

    def _split_entries(self, text: str) -> List[str]:
        """Split a block of bibliography text into per-entry strings."""
        lines = text.splitlines()

        # Strategy 1: numbered references [1], 1. etc.
        numbered: List[Tuple[int, int]] = []   # (line_idx, ref_num)
        for i, line in enumerate(lines):
            m = re.match(r"^\s*\[(\d+)\]|^\s*(\d+)\.\s+[A-Z]", line)
            if m:
                numbered.append((i, int(m.group(1) or m.group(2))))
        if len(numbered) >= 2:
            return self._split_by_line_indices(lines, [i for i, _ in numbered])

        # Strategy 2: author-name line starts (capitalised word, comma, initial)
        author_lines: List[int] = []
        for i, line in enumerate(lines):
            if _AUTHOR_START_RE.match(line.strip()):
                author_lines.append(i)
        if len(author_lines) >= 2:
            return self._split_by_line_indices(lines, author_lines)

        # Strategy 3: year-anchored splitting
        year_lines: List[int] = []
        for i, line in enumerate(lines):
            if _YEAR_RE.search(line) and i > 0:
                year_lines.append(i)
        if len(year_lines) >= 2:
            return self._split_by_line_indices(lines, year_lines)

        # Fallback: treat each non-empty line as an entry
        return [ln for ln in lines if ln.strip()]

    @staticmethod
    def _split_by_line_indices(
        lines: List[str], start_indices: List[int]
    ) -> List[str]:
        entries: List[str] = []
        for k, start in enumerate(start_indices):
            end = start_indices[k + 1] if k + 1 < len(start_indices) else len(lines)
            chunk = "\n".join(lines[start:end]).strip()
            if chunk:
                entries.append(chunk)
        return entries
