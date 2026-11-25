"""Detect inline citation spans while retaining exact source coordinates."""
from __future__ import annotations

import re
import uuid
from typing import List, Set, Tuple

from core.models.extraction_models import DocumentBlock, ExtractionProvenance, InTextCitation, SourceLocation
from core.services.extraction.author_year_citations import author_year_mentions
from core.services.extraction.block_roles import content_text
from core.services.extraction.legal_citation_resolution import SHORT_REFERENCE_RE
from core.services.extraction.legal_citation_patterns import (
    LEGAL_REPORTER_RE as _LEGAL_REPORTER_RE, STATUTE_RE as _STATUTE_RE,
    LEGAL_SHORT_REPORTER_RE, STATE_STATUTE_RE, LEGISLATIVE_RE, BRIEF_RE, SECONDARY_RE,
)
from core.services.extraction.markers import superscript_search_text

_EXTRACTOR_VERSION = "2.0"
_CONTEXT_RADIUS = 200
_MAX_NUMERIC_RANGE = 200


def _context(text: str, start: int, end: int) -> str:
    lo = max(0, start - _CONTEXT_RADIUS)
    hi = min(len(text), end + _CONTEXT_RADIUS)
    return text[lo:hi].strip()


def _stable_citation_id(doc_id, block_id, start, end, style, raw):
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{doc_id}|{block_id}|{start}|{end}|{style}|{raw}",
    ))


# ── In-text citation patterns ───────────────────────────────────────────────

# [1], [2,3], [4-6], [1,3,5-7]
# Also "[49, p. 21]", "[72, p. viii]" and mixed "[Tramer et al. 2016, 53, 54]".
_NUM_BRACKET_RE = re.compile(
    r"\[(?:[^\[\]\d]{1,60}?(?:1[5-9]|20)\d{2}[a-z]?,\s*)?"
    r"(\d+(?:[-–]\d+)?(?:,\s*\d+(?:[-–]\d+)?)*)"
    r"(?:,\s*pp?\.\s*[\dxivlc]+(?:[-–][\dxivlc]+)?)?\]"
)
# Superscript Unicode digits inserted by PDFLayoutParser (¹²³⁴⁵⁶⁷⁸⁹⁰)
# Ranges arrive as "¹⁻³" (the parser maps a small "1-3" span to superscript).
_SUPERSCRIPT_DIGIT_RE = re.compile(
    r"[⁰¹²³⁴⁵⁶⁷⁸⁹](?:[,⁻\s]?[⁰¹²³⁴⁵⁶⁷⁸⁹])*"
)
_SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")

_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20[0-2]\d)\b")

# Unit symbols that take a superscript exponent. The PDF parser cannot tell an
# exponent from a reference marker — it converts every small digit span to the
# same Unicode superscript — so the preceding token is the only signal. A
# superscript after a digit ("10³") or one of these units ("m²", "gCO²") is an
# exponent; after a word ("work¹") or an acronym ("AI¹") it is a reference.
_EXPONENT_UNITS = (
    "m", "km", "cm", "mm", "nm", "um", "µm", "ft", "yd", "mi",
    "g", "kg", "mg", "l", "w", "wh", "kwh", "j", "n", "pa", "hz",
    "co", "gco", "tco", "mtco",
)
_EXPONENT_PREFIX_RE = re.compile(
    r"(?:\d\s?|(?:^|[^\w])(?:"
    + "|".join(sorted(_EXPONENT_UNITS, key=len, reverse=True))
    + r"))$",
    re.I,
)

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
        seen_spans: Set[Tuple[str, int, int]] = set()  # (block_id, start, end)

        for block in body_blocks:
            first_citation = len(citations)
            text = content_text(block)   # furniture blanked; offsets stay raw
            if block.structure.get("region") in {"contents", "index", "glossary"}:
                continue
            if not text.strip():
                continue

            citations.extend(self._extract_numeric(text, block, doc_id, seen_spans))
            # A superscript in a first-page heading marks an author's
            # affiliation ("Lorenzo Belenguer¹"), not a citation.
            if not (block.page_num == 0 and block.block_type == "heading"):
                citations.extend(self._extract_superscript(text, block, doc_id, seen_spans))
            citations.extend(self._extract_legal(text, block, doc_id, seen_spans))
            citations.extend(self._extract_author_year(text, block, doc_id, seen_spans))
            for citation in citations[first_citation:]:
                citation.provenance.parser_provider = (
                    block.provenance.parser_provider if block.provenance else "document_blocks"
                )

        return _without_overlapping_secondary(citations)

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
            span_key = (block.block_id, start, end)
            if span_key in seen_spans:
                continue

            # Reject a number introduced as a figure/table/section reference.
            ctx = text[max(0, start - 30): start]
            if _NON_CITATION_CONTEXT.search(ctx):
                continue

            inner = m.group(1)
            if _YEAR_RE.fullmatch(inner):
                continue   # a bracketed year, e.g. "[2012] UKSC 1"
            keys = _expand_numeric_keys(inner)
            if not keys:
                continue
            seen_spans.add(span_key)
            results.append(InTextCitation(
                citation_id=_stable_citation_id(
                    doc_id, block.block_id, start, end, "numeric", m.group(0)
                ),
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
        for m in _SUPERSCRIPT_DIGIT_RE.finditer(superscript_search_text(text, block)):
            start, end = m.start(), m.end()
            span_key = (block.block_id, start, end)
            if span_key in seen_spans:
                continue
            if _EXPONENT_PREFIX_RE.search(text[max(0, start - 5):start]):
                continue
            line_start = text.rfind("\n", 0, start) + 1
            if not text[line_start:start].strip() and end < len(text) and text[end] in " \t\u2002\n":
                continue   # a footnote's own label ("¹ For a complementary…"), not a citation
            # "Table 3", "Fig. 2", "p. 14" disqualify the number they *precede*;
            # a following "Table A summarizes…" says nothing about this mark.
            if _NON_CITATION_CONTEXT.search(text[max(0, start - 30):start]):
                continue
            raw = text[start:end]
            digits = m.group(0).translate(_SUPERSCRIPT_MAP).replace(" ", "")
            if _YEAR_RE.fullmatch(digits):
                continue   # superscript year, e.g. "²⁰²⁵", not a reference
            keys = _expand_numeric_keys(digits)
            if not keys:
                continue
            seen_spans.add(span_key)
            results.append(InTextCitation(
                citation_id=_stable_citation_id(
                    doc_id, block.block_id, start, end, "superscript", raw
                ),
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
        for start, end, authors, year in author_year_mentions(text):
            if any(len(span) == 3 and span[0] == block.block_id
                   and span[1] <= start and end <= span[2] for span in seen_spans):
                continue
            raw = text[start:end]
            key = (block.block_id, start, end, year)
            if key in seen_spans:
                continue
            seen_spans.add(key)
            results.append(InTextCitation(
                citation_id=_stable_citation_id(
                    doc_id, block.block_id, start, end, f"author_year:{year}", raw,
                ), doc_id=doc_id, raw_text=raw, citation_type="author_year",
                author_keys=authors, year=year,
                location=SourceLocation(
                    doc_id=doc_id, page_num=block.page_num, block_id=block.block_id,
                    char_start=start, char_end=end,
                ), context_text=_context(text, start, end),
                provenance=_provenance("regex_author_year", 0.85),
            ))
        return results

    def _extract_legal(
        self, text: str, block: DocumentBlock, doc_id: str, seen_spans: Set
    ) -> List[InTextCitation]:
        """Reporter and statutory citations — the citation form legal sources use."""
        results = []
        for pattern, style, method in (
            (_STATUTE_RE, "statute", "regex_statute"),
            (_LEGAL_REPORTER_RE, "legal", "regex_legal_reporter"),
            (LEGAL_SHORT_REPORTER_RE, "legal_short", "regex_short_reporter"),
            (SHORT_REFERENCE_RE, "legal_short", "regex_id_reference"),
            (STATE_STATUTE_RE, "statute", "regex_state_statute"),
            (LEGISLATIVE_RE, "legal", "regex_legislative_report"),
            (BRIEF_RE, "secondary", "regex_brief"),
            (SECONDARY_RE, "secondary", "regex_secondary_title"),
        ):
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()
                span_key = (block.block_id, start, end)
                if span_key in seen_spans:
                    continue
                seen_spans.add(span_key)
                raw = m.group(0)
                year = m.groupdict().get("year")
                results.append(InTextCitation(
                    citation_id=_stable_citation_id(
                        doc_id, block.block_id, start, end, style, raw
                    ),
                    doc_id=doc_id,
                    raw_text=raw,
                    citation_type=style,
                    author_keys=[raw],
                    year=year,
                    location=SourceLocation(
                        doc_id=doc_id, page_num=block.page_num,
                        block_id=block.block_id, char_start=start, char_end=end,
                    ),
                    context_text=_context(text, start, end),
                    provenance=_provenance(method, 0.85),
                ))
        return results

def _without_overlapping_secondary(citations: List[InTextCitation]) -> List[InTextCitation]:
    """A title-pattern match that overlaps a reporter/statute/author-year mark
    is that mark's surroundings, not a separate treatise citation."""
    def span(c):
        loc = c.location
        return (loc.block_id, loc.char_start or 0, loc.char_end or 0)
    firm = [span(c) for c in citations if c.citation_type != "secondary"]
    return [c for c in citations if c.citation_type != "secondary" or not any(
        b == span(c)[0] and s < span(c)[2] and span(c)[1] < e for b, s, e in firm)]


def _expand_numeric_keys(inner: str) -> List[str]:
    """Expand '1,3,5-7' → ['1','3','5','6','7']."""
    keys: List[str] = []
    for part in re.split(r",\s*", inner):
        part = part.strip()
        m = re.match(r"(\d+)[-–](\d+)", part)
        if m:
            if int(m[2]) - int(m[1]) > _MAX_NUMERIC_RANGE:
                continue  # malformed/OCR range, not an unbounded allocation
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


