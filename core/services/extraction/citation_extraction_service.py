"""
CitationExtractionService — in-text citation extraction + citation index assembly.

Stages:
  1. InTextCitationExtractor.extract(body_blocks) → List[InTextCitation]
     Detects numeric [1], [2,3], [4-6], superscript digits, author-year (Smith, 2020),
     and footnote/endnote markers.
  2. CitationExtractionService.build_citation_index(doc_id, blocks) → CitationIndex
     Orchestrates bibliography extraction, in-text extraction, and CitationMatcher.

No LLM calls.  All extraction is deterministic.
"""
from __future__ import annotations

import re
import uuid
from typing import Dict, List, Optional, Set, Tuple

from core.models.extraction_models import (
    BibliographyEntry,
    CitationIndex,
    CitationMatch,
    DocumentBlock,
    ExtractionProvenance,
    InTextCitation,
    SourceLocation,
)

_EXTRACTOR_VERSION = "1.0"
_CONTEXT_RADIUS = 200


def _context(text: str, start: int, end: int) -> str:
    lo = max(0, start - _CONTEXT_RADIUS)
    hi = min(len(text), end + _CONTEXT_RADIUS)
    return text[lo:hi].strip()


# ── In-text citation patterns ───────────────────────────────────────────────

# [1], [2,3], [4-6], [1,3,5-7]
_NUM_BRACKET_RE = re.compile(
    r"\[(\d+(?:[-–]\d+)?(?:,\s*\d+(?:[-–]\d+)?)*)\]"
)
# Superscript Unicode digits inserted by PDFLayoutParser (¹²³⁴⁵⁶⁷⁸⁹⁰)
_SUPERSCRIPT_DIGIT_RE = re.compile(
    r"[⁰¹²³⁴⁵⁶⁷⁸⁹](?:[,\s]?[⁰¹²³⁴⁵⁶⁷⁸⁹])*"
)
_SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

# (Smith, 2020), Smith (2020), (Smith et al., 2020), (Smith & Jones, 2020)
_AUTHOR_YEAR_PAREN_RE = re.compile(
    r"\(([A-Z][a-z\-\']+(?:\s+(?:et\s+al\.?|&|and)\s+[A-Z][a-z\-\']+)*"
    r"(?:,?\s+(?:1[5-9]\d{2}|20[0-2]\d)(?:[a-z])?)?)\)",
    re.I,
)
# Smith (2020) — surname followed by bracketed year
_AUTHOR_YEAR_INLINE_RE = re.compile(
    r"\b([A-Z][a-z\-\']+(?:\s+et\s+al\.?)?)\s+\((1[5-9]\d{2}|20[0-2]\d)([a-z]?)\)"
)
# Footnote/endnote markers: *, †, ‡, §, or small superscript n (not digits)
_FOOTNOTE_MARKER_RE = re.compile(r"[*†‡§]")

_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20[0-2]\d)\b")

# Words that indicate a number is NOT a citation (unit/figure/equation context)
_NON_CITATION_CONTEXT = re.compile(
    r"\b(?:figure|fig\.|table|eq\.|equation|section|§|chapter|p\.|pp\.|"
    r"patient|subject|sample|group|item|step|grade|score)\b",
    re.I,
)


class InTextCitationExtractor:
    def __init__(self, pattern_registry=None) -> None:
        self._registry = pattern_registry

    def extract(
        self, body_blocks: List[DocumentBlock], doc_id: str
    ) -> List[InTextCitation]:
        """Extract all in-text citations from body (non-bibliography) blocks."""
        citations: List[InTextCitation] = []
        seen_spans: Set[Tuple[int, int, int]] = set()  # (page, start, end)

        for block in body_blocks:
            text = block.exact_text
            if not text.strip():
                continue

            citations.extend(self._extract_numeric(text, block, doc_id, seen_spans))
            citations.extend(self._extract_superscript(text, block, doc_id, seen_spans))
            citations.extend(self._extract_author_year(text, block, doc_id, seen_spans))
            citations.extend(self._extract_footnote_markers(text, block, doc_id, seen_spans))

        return citations

    # ------------------------------------------------------------------

    def _extract_numeric(
        self,
        text: str,
        block: DocumentBlock,
        doc_id: str,
        seen_spans: Set,
    ) -> List[InTextCitation]:
        results = []
        for m in _NUM_BRACKET_RE.finditer(text):
            start, end = m.start(), m.end()
            span_key = (block.page_num, start, end)
            if span_key in seen_spans:
                continue

            # Reject if surrounding context looks like a figure/table reference
            ctx = text[max(0, start - 30): end + 10]
            if _NON_CITATION_CONTEXT.search(ctx):
                continue

            inner = m.group(1)
            keys = _expand_numeric_keys(inner)
            seen_spans.add(span_key)
            results.append(InTextCitation(
                citation_id=str(uuid.uuid4()),
                doc_id=doc_id,
                raw_text=m.group(0),
                citation_type="numeric",
                numeric_keys=keys,
                author_keys=[],
                year=None,
                location=SourceLocation(
                    doc_id=doc_id,
                    page_num=block.page_num,
                    block_id=block.block_id,
                    char_start=start,
                    char_end=end,
                ),
                context_text=_context(text, start, end),
                provenance=_provenance("regex", 0.85),
            ))
        return results

    def _extract_superscript(
        self,
        text: str,
        block: DocumentBlock,
        doc_id: str,
        seen_spans: Set,
    ) -> List[InTextCitation]:
        results = []
        for m in _SUPERSCRIPT_DIGIT_RE.finditer(text):
            start, end = m.start(), m.end()
            span_key = (block.page_num, start, end)
            if span_key in seen_spans:
                continue
            raw = m.group(0)
            digits = raw.translate(_SUPERSCRIPT_MAP).replace(" ", "").replace(",", ",")
            keys = _expand_numeric_keys(digits)
            seen_spans.add(span_key)
            results.append(InTextCitation(
                citation_id=str(uuid.uuid4()),
                doc_id=doc_id,
                raw_text=raw,
                citation_type="superscript",
                numeric_keys=keys,
                author_keys=[],
                year=None,
                location=SourceLocation(
                    doc_id=doc_id,
                    page_num=block.page_num,
                    block_id=block.block_id,
                    char_start=start,
                    char_end=end,
                ),
                context_text=_context(text, start, end),
                provenance=_provenance("regex", 0.80),
            ))
        return results

    def _extract_author_year(
        self,
        text: str,
        block: DocumentBlock,
        doc_id: str,
        seen_spans: Set,
    ) -> List[InTextCitation]:
        results = []
        for pattern, style in [
            (_AUTHOR_YEAR_PAREN_RE, "author_year"),
            (_AUTHOR_YEAR_INLINE_RE, "author_year"),
        ]:
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()
                span_key = (block.page_num, start, end)
                if span_key in seen_spans:
                    continue
                raw = m.group(0)
                year_m = _YEAR_RE.search(raw)
                year = year_m.group(1) if year_m else None

                # Build author keys: strip year + punctuation
                inner = re.sub(r",?\s*(1[5-9]\d{2}|20[0-2]\d)[a-z]?", "", raw)
                inner = re.sub(r"[\(\)\[\]]", "", inner).strip()
                author_keys = [p.strip() for p in re.split(r"\s*(?:&|and|;)\s*", inner) if p.strip()]

                seen_spans.add(span_key)
                results.append(InTextCitation(
                    citation_id=str(uuid.uuid4()),
                    doc_id=doc_id,
                    raw_text=raw,
                    citation_type=style,
                    numeric_keys=[],
                    author_keys=author_keys,
                    year=year,
                    location=SourceLocation(
                        doc_id=doc_id,
                        page_num=block.page_num,
                        block_id=block.block_id,
                        char_start=start,
                        char_end=end,
                    ),
                    context_text=_context(text, start, end),
                    provenance=_provenance("regex", 0.80),
                ))
        return results

    def _extract_footnote_markers(
        self,
        text: str,
        block: DocumentBlock,
        doc_id: str,
        seen_spans: Set,
    ) -> List[InTextCitation]:
        results = []
        for m in _FOOTNOTE_MARKER_RE.finditer(text):
            start, end = m.start(), m.end()
            span_key = (block.page_num, start, end)
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            results.append(InTextCitation(
                citation_id=str(uuid.uuid4()),
                doc_id=doc_id,
                raw_text=m.group(0),
                citation_type="footnote",
                numeric_keys=[],
                author_keys=[],
                year=None,
                location=SourceLocation(
                    doc_id=doc_id,
                    page_num=block.page_num,
                    block_id=block.block_id,
                    char_start=start,
                    char_end=end,
                ),
                context_text=_context(text, start, end),
                provenance=_provenance("regex", 0.60),
            ))
        return results


def _expand_numeric_keys(inner: str) -> List[str]:
    """Expand '1,3,5-7' → ['1','3','5','6','7']."""
    keys: List[str] = []
    for part in re.split(r",\s*", inner):
        part = part.strip()
        m = re.match(r"(\d+)[-–](\d+)", part)
        if m:
            keys.extend(str(i) for i in range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            keys.append(part)
    return keys


def _provenance(method: str, confidence: float) -> ExtractionProvenance:
    return ExtractionProvenance(
        extractor_name="citation_extraction_service",
        extractor_version=_EXTRACTOR_VERSION,
        parser_provider="pymupdf_layout",
        method=method,
        confidence=confidence,
    )


# ── Service ─────────────────────────────────────────────────────────────────

class CitationExtractionService:
    """
    Orchestrates bibliography extraction, in-text citation detection, and
    citation matching to produce a complete CitationIndex per document.
    """

    def __init__(
        self,
        bibliography_service,
        citation_matcher,
        pattern_registry=None,
    ) -> None:
        self._bib_svc = bibliography_service
        self._matcher = citation_matcher
        self._in_text = InTextCitationExtractor(pattern_registry)

    # ------------------------------------------------------------------

    def build_citation_index(
        self, doc_id: str, blocks: List[DocumentBlock]
    ) -> CitationIndex:
        """
        Build a complete CitationIndex for a document.

        Steps:
          1. Detect + segment bibliography entries from bibliography blocks
          2. Extract in-text citations from body blocks
          3. Match in-text to bibliography entries
          4. Build entry_page_index and classify unmatched / ambiguous
        """
        bib_blocks = self._bib_svc.detect_bibliography_blocks(blocks)
        body_blocks = [b for b in blocks if b.block_type != "bibliography"]

        entries: List[BibliographyEntry] = self._bib_svc.segment_entries(bib_blocks, doc_id)
        in_text: List[InTextCitation] = self._in_text.extract(body_blocks, doc_id)

        matches: List[CitationMatch] = []
        matched_bib_ids: Set[str] = set()
        matched_in_text_ids: Set[str] = set()
        ambiguous_match_ids: List[str] = []

        for cit in in_text:
            m = self._matcher.match(cit, entries)
            if m is None:
                continue
            matches.append(m)
            if m.confidence >= 0.3:
                matched_bib_ids.add(m.bibliography_entry_id)
                matched_in_text_ids.add(cit.citation_id)
            if m.is_ambiguous:
                ambiguous_match_ids.append(m.match_id)

        unmatched_bib_entry_ids = [e.entry_id for e in entries if e.entry_id not in matched_bib_ids]
        unmatched_in_text_ids = [c.citation_id for c in in_text if c.citation_id not in matched_in_text_ids]

        entry_page_index = self._build_page_index(matches, in_text)
        citation_contexts = {c.citation_id: c.context_text for c in in_text}

        return CitationIndex(
            doc_id=doc_id,
            bibliography_entries=entries,
            in_text_citations=in_text,
            citation_matches=matches,
            unmatched_bib_entry_ids=unmatched_bib_entry_ids,
            unmatched_in_text_ids=unmatched_in_text_ids,
            ambiguous_match_ids=ambiguous_match_ids,
            entry_page_index=entry_page_index,
            citation_contexts=citation_contexts,
        )

    # ------------------------------------------------------------------

    def _build_page_index(
        self,
        matches: List[CitationMatch],
        in_text: List[InTextCitation],
    ) -> Dict[str, List[int]]:
        """Map bibliography entry_id → sorted list of pages where it's cited."""
        cit_page: Dict[str, int] = {
            c.citation_id: (c.location.page_num if c.location else 0)
            for c in in_text
        }
        index: Dict[str, List[int]] = {}
        for m in matches:
            if m.confidence < 0.3:
                continue
            page = cit_page.get(m.in_text_citation_id, 0)
            index.setdefault(m.bibliography_entry_id, [])
            if page not in index[m.bibliography_entry_id]:
                index[m.bibliography_entry_id].append(page)
        for pages in index.values():
            pages.sort()
        return index
