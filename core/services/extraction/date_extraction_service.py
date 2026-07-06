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
from typing import List, Optional, Tuple

from core.models.extraction_models import (
    DocumentBlock,
    ExtractedDate,
    ExtractionProvenance,
    SourceLocation,
)

_EXTRACTOR_VERSION = "1.0"

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
    # 3 — Numeric slash
    ("slash", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "date"),
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
            r"\b(?:the\s+)?(?:early\s+|late\s+|mid-?)?(\d{4})s\b",
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
        return raw
    if tier in ("year", "decade"):
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
            return f"{m.group(3)}-{mo}-{m.group(2).zfill(2)}"
        # Try "DD Month YYYY"
        m = re.match(rf"(\d{{1,2}})\s+({_MONTH_NAMES})\s+(\d{{4}})", raw, re.I)
        if m:
            mo = _MONTH_MAP.get(m.group(2).lower(), "??")
            return f"{m.group(3)}-{mo}-{m.group(1).zfill(2)}"
    return raw


class DateExtractionService:
    def __init__(self, pattern_registry=None) -> None:
        self._registry = pattern_registry

    # ------------------------------------------------------------------

    def extract_dates(
        self, blocks: List[DocumentBlock], doc_id: str
    ) -> List[ExtractedDate]:
        """Extract dates from all blocks, deduplicating by span overlap."""
        results: List[ExtractedDate] = []
        seen_spans: List[Tuple[int, int, int]] = []   # (page, start, end)

        # Exclude bibliography blocks (reference years would produce noise)
        target_blocks = [b for b in blocks if b.block_type != "bibliography"]

        for block in target_blocks:
            text = block.exact_text
            if not text.strip():
                continue
            block_dates = self._extract_from_text(text, block, doc_id, seen_spans)
            results.extend(block_dates)

        return results

    # ------------------------------------------------------------------

    def _extract_from_text(
        self,
        text: str,
        block: DocumentBlock,
        doc_id: str,
        seen_spans: List[Tuple[int, int, int]],
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

                # For bare_year tier, skip if already captured by higher tier
                if tier_name == "bare_year":
                    if self._overlaps(block.page_num, start, end, seen_spans):
                        continue

                seen_spans.append((block.page_num, start, end))
                raw = m.group(0)
                context = _context_around(text, start, end)

                dates.append(ExtractedDate(
                    date_id=str(uuid.uuid4()),
                    doc_id=doc_id,
                    exact_text=raw,
                    normalized_date=_normalize_date(raw, tier_name),
                    certainty="confirmed",
                    location=SourceLocation(
                        doc_id=doc_id,
                        page_num=block.page_num,
                        block_id=block.block_id,
                        char_start=start,
                        char_end=end,
                    ),
                    context_text=context,
                    provenance=ExtractionProvenance(
                        extractor_name="date_extraction_service",
                        extractor_version=_EXTRACTOR_VERSION,
                        parser_provider="pymupdf_layout",
                        method="regex",
                        confidence=_tier_confidence(tier_name),
                    ),
                ))
        return dates

    @staticmethod
    def _overlaps(
        page: int, start: int, end: int, seen: List[Tuple[int, int, int]]
    ) -> bool:
        for sp, ss, se in seen:
            if sp == page and not (end <= ss or start >= se):
                return True
        return False


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
