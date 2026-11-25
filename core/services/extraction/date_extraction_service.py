"""
DateExtractionService — shared date/timeline extraction.

Replaces the duplicated DateEventExtractor logic in deterministic.py and
DocumentIngestionService. Applies the same 7-tier priority pattern system
with span-level conflict resolution to avoid double-counting years already
matched by more specific patterns.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from typing import List, Optional, Tuple

from core.services.extraction.block_roles import content_text
from core.models.extraction_models import (
    DocumentBlock,
    ExtractedDate,
    ExtractionProvenance,
    SourceLocation,
)
from core.services.extraction.citation_extraction_service import InTextCitationExtractor
from core.services.extraction.date_context import classify_date_context
from core.services.extraction.numeric_dates import resolve as resolve_numeric_dates

# 1.6: numeric dates ("4-2-1647") normalised with the document's day/month convention.
_EXTRACTOR_VERSION = "1.6"

# How many characters either side of a match to include as context
_CONTEXT_RADIUS = 180

# Minimum year to accept as a historical date
_MIN_YEAR = 1000


def _context_around(text: str, start: int, end: int) -> str:
    lo = max(0, start - _CONTEXT_RADIUS)
    hi = min(len(text), end + _CONTEXT_RADIUS)
    return text[lo:hi].strip()


# ---------------------------------------------------------------------------
# Pattern tiers (same order as existing DateEventExtractor)
# ---------------------------------------------------------------------------

_MONTH_NAMES = (
    "January|February|March|April|May|June|July|August|"
    "September|October|November|December"
)

_TIERS: List[Tuple[str, re.Pattern, str]] = [
    # 1 — Full named-month date (DD Month YYYY or Month DD, YYYY)
    (
        "full_named",
        re.compile(
            rf"\b(?:{_MONTH_NAMES})\s+\d{{1,2}},?\s+\d{{4}}\b"
            rf"|\b\d{{1,2}}\s+(?:{_MONTH_NAMES})\s+\d{{4}}\b",
            re.I,
        ),
        "date",
    ),
    # 2 — ISO
    ("iso", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "date"),
    # 3 — Numeric (10/4/2004; hyphen/dot forms need one separator and a 4-digit year)
    ("slash", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}([-.])\d{1,2}\1(?:1[0-9]|20)\d{2}\b"), "date"),
    # 4 — Month-year
    (
        "month_year",
        re.compile(rf"\b(?:{_MONTH_NAMES})\s+\d{{4}}\b", re.I),
        "date",
    ),
    # 5 — Year with preposition
    (
        "prep_year",
        re.compile(
            r"\b(?:in|since|by|after|before|until|from|circa|c\.)\s+(\d{4})\b",
            re.I,
        ),
        "year",
    ),
    # 6 — Decades
    (
        "decade",
        re.compile(
            r"\b(?:the\s+)?(?:early\s+|late\s+|mid-?)?(\d{4})[’']?s\b",
            re.I,
        ),
        "decade",
    ),
    # 7 — Bare 4-digit year (fallback — deduped against earlier matches)
    (
        "bare_year",
        re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\b"),
        "year",
    ),
]

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def _normalize_date(raw: str, tier: str) -> str:
    """Best-effort ISO normalisation; returns raw text if parsing unclear."""
    raw = raw.strip()
    if tier == "iso":
        return raw if _valid_calendar_date(raw) else ""
    if tier in ("prep_year", "bare_year", "decade"):
        m = re.search(r"\d{4}", raw)
        return m.group(0) if m else raw
    if tier == "month_year":
        m = re.match(rf"({_MONTH_NAMES})\s+(\d{{4}})", raw, re.I)
        if m:
            return f"{m.group(2)}-{_MONTH_MAP.get(m.group(1).lower(), '??')}"
    if tier == "full_named":
        # Try "Month DD, YYYY"
        m = re.match(rf"({_MONTH_NAMES})\s+(\d{{1,2}}),?\s+(\d{{4}})", raw, re.I)
        if m:
            mo = _MONTH_MAP.get(m.group(1).lower(), "??")
            normalized = f"{m.group(3)}-{mo}-{m.group(2).zfill(2)}"
            return normalized if _valid_calendar_date(normalized) else ""
        # Try "DD Month YYYY"
        m = re.match(rf"(\d{{1,2}})\s+({_MONTH_NAMES})\s+(\d{{4}})", raw, re.I)
        if m:
            mo = _MONTH_MAP.get(m.group(2).lower(), "??")
            normalized = f"{m.group(3)}-{mo}-{m.group(1).zfill(2)}"
            return normalized if _valid_calendar_date(normalized) else ""
    return raw


def _valid_calendar_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


class DateExtractionService:
    def __init__(self, pattern_registry=None, citation_extractor=None) -> None:
        self._registry = pattern_registry
        self._citations = citation_extractor or InTextCitationExtractor(pattern_registry)

    # ------------------------------------------------------------------

    def extract_dates(
        self, blocks: List[DocumentBlock], doc_id: str
    ) -> List[ExtractedDate]:
        """Extract dates from all blocks, deduplicating by span overlap."""
        results: List[ExtractedDate] = []
        seen_by_block = {}

        citations_by_block = {}
        for citation in self._citations.extract(blocks, doc_id):
            citations_by_block.setdefault(citation.location.block_id, []).append(citation)
        for block in blocks:
            text = content_text(block)   # furniture blanked; offsets stay raw
            if not text.strip():
                continue
            block_dates = self._extract_from_text(
                text, block, doc_id, seen_by_block.setdefault(block.block_id, []),
                citations_by_block.get(block.block_id, []))
            results.extend(block_dates)
        resolve_numeric_dates(results)   # day/month order from the document's own unambiguous dates
        return results

    # ------------------------------------------------------------------

    def _extract_from_text(
        self,
        text: str,
        block: DocumentBlock,
        doc_id: str,
        seen_spans: List[Tuple[str, int, int]],
        citations=(),
    ) -> List[ExtractedDate]:
        dates: List[ExtractedDate] = []

        for tier_name, pattern, kind in _TIERS:
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()
                # Check year is plausible
                yr_match = re.search(r"\b\d{4}\b", m.group(0))
                if yr_match:
                    yr = int(yr_match.group(0))
                    if yr < _MIN_YEAR:
                        continue

                if self._overlaps(block.block_id, start, end, seen_spans):
                    continue
                # Even an invalid specific date owns its span: a fallback year
                # must not turn February 31, 2020 into a confirmed observation.
                seen_spans.append((block.block_id, start, end))
                raw = m.group(0)
                normalized = _normalize_date(raw, tier_name)
                if not normalized and tier_name in {"full_named", "iso"}:
                    continue
                assessment = classify_date_context(
                    text, start, end, tier=tier_name, block_type=block.block_type,
                    section_path=block.section_path, citations=citations)
                if assessment.non_date:
                    continue
                dates.append(_date_observation(
                    doc_id, block, start, end, raw, normalized, tier_name, assessment))
        return dates

    @staticmethod
    def _overlaps(
        block_id: str, start: int, end: int, seen: List[Tuple[str, int, int]]
    ) -> bool:
        for sp, ss, se in seen:
            if sp == block_id and not (end <= ss or start >= se):
                return True
        return False


def _date_observation(doc_id, block, start, end, raw, normalized, tier_name, assessment):
    return ExtractedDate(
        date_id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                    f"{doc_id}|{block.block_id}|{start}|{end}|{raw}")),
        doc_id=doc_id, exact_text=raw, normalized_date=normalized,
        certainty="ambiguous" if tier_name == "slash" else "confirmed",
        location=SourceLocation(doc_id=doc_id, page_num=block.page_num,
                                block_id=block.block_id, char_start=start, char_end=end),
        context_text=_context_around(block.exact_text, start, end),
        provenance=ExtractionProvenance(
            extractor_name="date_extraction_service", extractor_version=_EXTRACTOR_VERSION,
            parser_provider="pymupdf_layout", method="regex",
            confidence=_tier_confidence(tier_name),
        ),
        metadata={**assessment.metadata(), "precision": _precision(normalized, tier_name)},
    )


def _tier_confidence(tier: str) -> float:
    return {
        "full_named": 0.95,
        "iso": 0.98,
        "slash": 0.75,
        "month_year": 0.9,
        "prep_year": 0.8,
        "decade": 0.7,
        "bare_year": 0.55,
    }.get(tier, 0.7)


def _precision(normalized: str, tier: str) -> str:
    if tier == "decade":
        return "decade"
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", normalized):
        return "unresolved"
    return {4: "year", 7: "month", 10: "day"}.get(len(normalized), "unresolved")
