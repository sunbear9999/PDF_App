"""
plugins/locallaws/court_case_extractor.py

Regex-based court case / precedent extractor for use with
DeterministicExtractorRegistry.  Detects:
  - Full reporter citations: "Brown v. Board of Education, 347 U.S. 483 (1954)"
  - Neutral citations:       "2021 SCC 45", "[2021] UKSC 12", "2019 ONCA 734"
  - Bare case names:         "Marbury v. Madison", "Roe v. Wade"
    (only extracted when not already captured by the fuller patterns)

Returns List[ExtractedEntityGroup] with entity_type="law.court_case".
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Tuple

from core.extraction.deterministic import (
    ExtractedEntityGroup,
    ExtractedMention,
    _build_full_text,
    _page_for_offset,
    _sentence_for_span,
)

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Full reporter citation:
#   "Smith v. Jones, 42 F.3d 100 (9th Cir. 1994)"
#   "Brown v. Board of Education, 347 U.S. 483 (1954)"
# Captures: group(1)=case name, group(2)=volume+reporter+page, group(3)=court/year clause
_REPORTER_CITATION = re.compile(
    r"\b"
    r"((?:[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,5})"   # party 1 (1-6 cap words)
    r"\s+v\.?\s+"                                              # "v." separator
    r"(?:[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,5}))"    # party 2
    r",?\s+"
    r"(\d{1,4}\s+"                                             # volume
    r"(?:[A-Z][A-Za-z.]{1,10}(?:\s*[0-9]?[a-z]{1,2})?)"     # reporter (e.g. U.S., F.3d, S.Ct.)
    r"\s+\d{1,6})"                                             # page
    r"(?:\s*\(([^)]{2,60})\))?"                               # optional (court year)
)

# Neutral citation styles:
#   "2021 SCC 45"   "[2021] UKSC 12"   "2019 ONCA 734"   "[2020] AC 100"
_NEUTRAL_CITATION = re.compile(
    r"""(?:\[)?(1[89]\d{2}|20\d{2})(?:\])?  # year
    \s+
    ([A-Z]{2,8})                            # tribunal abbrev
    \s+
    (\d{1,5})                               # decision number
    """,
    re.VERBOSE,
)

# Bare case name only: "Marbury v. Madison", "R v. Smith"
# (used as fallback when no reporter/neutral citation was found)
_BARE_CASE = re.compile(
    r"\b"
    r"((?:[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,4})"
    r"\s+v\.?\s+"
    r"(?:[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,4}))"
    r"\b"
)

# Words that appear capitalised but are never party names
_FALSE_POSITIVE_NAMES = frozenset([
    "The Court", "The State", "United States", "Supreme Court",
    "Court Of", "Act Of", "Section Of",
])


def _clean_name(raw: str) -> str:
    """Strip trailing punctuation and normalise whitespace."""
    return re.sub(r"[,;.\s]+$", "", raw.strip())


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def run_court_case_extractor(
    full_text: str, page_texts: List[Tuple[int, str]]
) -> List[ExtractedEntityGroup]:
    """Extract court case citations and bare case names from legal text."""
    _, page_offsets = _build_full_text(page_texts)

    # groups maps normalised case-name key → {name, citation, court_year, mentions}
    groups: Dict[str, dict] = {}

    # --- Step 1: Full reporter citations ---
    for m in _REPORTER_CITATION.finditer(full_text):
        name = _clean_name(m.group(1))
        if not name or any(fp in name for fp in _FALSE_POSITIVE_NAMES):
            continue
        citation = m.group(2).strip() if m.group(2) else ""
        court_year = m.group(3).strip() if m.group(3) else ""
        key = name.lower()
        ctx = _sentence_for_span(full_text, m.start(), m.end())
        page = _page_for_offset(m.start(), page_offsets)
        mention = ExtractedMention(text=m.group().strip(), context=ctx, page_num=page)

        # Merge with existing group that has very similar name
        merged_key = next(
            (k for k in groups if _similar(key, k) > 0.88), None
        )
        if merged_key:
            groups[merged_key]["mentions"].append(mention)
            if not groups[merged_key]["citation"] and citation:
                groups[merged_key]["citation"] = citation
            if not groups[merged_key]["court_year"] and court_year:
                groups[merged_key]["court_year"] = court_year
        else:
            groups[key] = {
                "name": name,
                "citation": citation,
                "court_year": court_year,
                "mentions": [mention],
            }

    # --- Step 2: Neutral citations ---
    for m in _NEUTRAL_CITATION.finditer(full_text):
        year = m.group(1)
        tribunal = m.group(2)
        number = m.group(3)
        citation_str = f"{year} {tribunal} {number}"
        key = citation_str.lower()
        ctx = _sentence_for_span(full_text, m.start(), m.end())
        page = _page_for_offset(m.start(), page_offsets)
        mention = ExtractedMention(text=citation_str, context=ctx, page_num=page)
        if key not in groups:
            groups[key] = {
                "name": citation_str,
                "citation": citation_str,
                "court_year": year,
                "mentions": [mention],
            }
        else:
            groups[key]["mentions"].append(mention)

    # --- Step 3: Bare case names not already captured ---
    for m in _BARE_CASE.finditer(full_text):
        name = _clean_name(m.group(1))
        if not name or any(fp in name for fp in _FALSE_POSITIVE_NAMES):
            continue
        key = name.lower()
        # Skip if already captured (exact or fuzzy match)
        already = next((k for k in groups if _similar(key, k) > 0.85), None)
        if already:
            continue
        ctx = _sentence_for_span(full_text, m.start(), m.end())
        page = _page_for_offset(m.start(), page_offsets)
        mention = ExtractedMention(text=m.group().strip(), context=ctx, page_num=page)
        groups[key] = {
            "name": name,
            "citation": "",
            "court_year": "",
            "mentions": [mention],
        }

    # --- Deduplicate mentions by context sentence ---
    result = []
    for data in groups.values():
        seen_ctx: set = set()
        deduped = []
        for mention in data["mentions"]:
            if mention.context not in seen_ctx:
                seen_ctx.add(mention.context)
                deduped.append(mention)
        if deduped:
            result.append(
                ExtractedEntityGroup(
                    canonical_text=data["name"],
                    entity_type="law.court_case",
                    mentions=deduped,
                    extra_properties={
                        "citation": data["citation"],
                        "court_year": data["court_year"],
                        "description": "",
                    },
                )
            )

    return sorted(result, key=lambda g: -len(g.mentions))
