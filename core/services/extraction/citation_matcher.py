"""
CitationMatcher — weighted feature scoring for bibliography matching.

Replaces the simple author+year dict lookup in CitationExtractor with a
multi-signal scoring system that produces confidence values and correctly
identifies ambiguous and unmatched citations.

No LLM calls.  All matching is deterministic.
"""
from __future__ import annotations

import re
import uuid
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from core.models.extraction_models import (
    BibliographyEntry,
    CitationMatch,
    InTextCitation,
)
from core.services.extraction.legal_citation_patterns import authority_key
from core.services.extraction.author_year_citations import normalized_name

# Try to use rapidfuzz for faster/better fuzzy matching; fall back to difflib.
try:
    from rapidfuzz import fuzz as _rf_fuzz  # type: ignore
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False

_EXTRACTOR_VERSION = "1.1"

# ---- Scoring weights -------------------------------------------------------
# Weights sum to roughly 1.0 for an exact match on every applicable signal.
# Ambiguity penalty is subtracted when two entries are within 0.1 of each other.

MATCH_WEIGHTS: Dict[str, float] = {
    "exact_reference_number":  1.00,  # [1] matches ref 1 exactly
    "author_year_combined":    0.80,  # surname + year both match
    "doi":                     1.00,  # DOI is globally unique
    "url":                     0.90,  # URL is usually unique
    "legal_reporter":          0.85,  # legal reporter match
    "author_surname":          0.40,  # surname-only match
    "year":                    0.20,  # year alone stays under the threshold
    "title_phrase":            0.50,  # partial title phrase match
    "fuzzy_surname":           0.25,  # fuzzy surname (difflib/rapidfuzz)
    "numbering_compatible":    0.35,  # numeric key in plausible range
}
_AMBIGUITY_PENALTY = 0.30
_AMBIGUITY_DELTA = 0.10     # two entries within this → ambiguous
MIN_MATCH_CONFIDENCE = 0.30  # below this → unmatched
_MIN_MATCH_THRESHOLD = MIN_MATCH_CONFIDENCE


def _fuzzy_ratio(a: str, b: str) -> float:
    """Return similarity 0–1 between two strings."""
    if _HAS_RAPIDFUZZ:
        return _rf_fuzz.ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()


_FUZZY_SURNAME_THRESHOLD = 0.88
# Keys that identify exactly one entry; a tie elsewhere does not make them
# ambiguous, so the ambiguity penalty must not erode them.
_DECISIVE_SIGNALS = ("exact_reference_number", "doi", "url", "legal_reporter")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _tokens(s: str) -> set:
    """Word tokens of *s*, so "li" never matches inside "williams"."""
    return set(re.findall(r"[\w']+", s or ""))


# Citation types that name a legal authority rather than a reference-list entry.
AUTHORITY_CITATION_TYPES = frozenset({"legal", "statute"})

class CitationMatcher:
    """
    Score every bibliography entry against an in-text citation and return
    the best match with confidence and score breakdown.
    """

    def match(
        self,
        citation: InTextCitation,
        entries: List[BibliographyEntry],
    ) -> Optional[CitationMatch]:
        """
        Match one in-text citation to the best bibliography entry.

        Returns None only if entries list is empty.
        Returns a CitationMatch with confidence=0 if no entry clears
        the _MIN_MATCH_THRESHOLD.
        """
        if not entries:
            return None

        scored: List[Tuple[float, Dict[str, float], BibliographyEntry]] = []

        for entry in entries:
            score, breakdown = self._score(citation, entry)
            scored.append((score, breakdown, entry))

        scored.sort(key=lambda t: t[0], reverse=True)
        best_score, best_breakdown, best_entry = scored[0]

        best_score, is_ambiguous, alternates = _ambiguity(scored, best_score, best_breakdown)

        confidence = max(0.0, min(1.0, best_score))
        if confidence < _MIN_MATCH_THRESHOLD:
            confidence = 0.0

        reason = _make_reason(best_breakdown)

        return CitationMatch(
            match_id=str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{citation.citation_id}|{best_entry.entry_id}",
            )),
            in_text_citation_id=citation.citation_id,
            bibliography_entry_id=best_entry.entry_id,
            confidence=confidence,
            score_breakdown=best_breakdown,
            is_ambiguous=is_ambiguous,
            alternate_entry_ids=alternates,
            reason=reason,
        )

    def match_all(
        self,
        citations: List[InTextCitation],
        entries: List[BibliographyEntry],
    ) -> List[CitationMatch]:
        """Match occurrences to every referenced entry, including grouped styles.

        Only matches that clear ``MIN_MATCH_CONFIDENCE`` are returned. A
        below-threshold match still names the highest-scoring entry, so keeping
        it would silently attach an unmatched citation to an arbitrary
        reference; the caller records those citations as unmatched instead.
        """
        matches: List[CitationMatch] = []
        for citation in citations:
            if len(citation.numeric_keys) > 1:
                seen_entries = set()
                for key in citation.numeric_keys:
                    split_citation = InTextCitation(
                        citation_id=citation.citation_id,
                        doc_id=citation.doc_id,
                        raw_text=citation.raw_text,
                        citation_type=citation.citation_type,
                        numeric_keys=[key],
                        author_keys=list(citation.author_keys),
                        year=citation.year,
                        location=citation.location,
                        context_text=citation.context_text,
                        provenance=citation.provenance,
                    )
                    match = self.match(split_citation, entries)
                    if (
                        match and match.confidence > 0
                        and match.bibliography_entry_id not in seen_entries
                    ):
                        seen_entries.add(match.bibliography_entry_id)
                        matches.append(match)
                continue
            match = self.match(citation, entries)
            if match is not None and match.confidence > 0:
                matches.append(match)
        return matches

    # ------------------------------------------------------------------

    def _score(self, citation, entry) -> Tuple[float, Dict[str, float]]:
        if citation.numeric_keys:
            return self._score_number(citation, entry)
        if citation.citation_type in AUTHORITY_CITATION_TYPES | {"legal_short"}:
            reporter = citation.resolved_authority_key or authority_key(citation.raw_text)
            if reporter and reporter == authority_key(entry.raw_text):
                weight = MATCH_WEIGHTS["legal_reporter"]
                return weight, {"legal_reporter": weight}
            return 0.0, {}
        if citation.year and entry.year and _conflicting_year(citation.year, entry.year) \
                and not _entry_mentions_year(entry, citation.year):
            return 0.0, {"year_conflict": -1.0}
        breakdown = self._score_authors(citation, entry)
        breakdown.update(self._score_identifiers(citation, entry))
        breakdown.update(self._score_title(citation, entry))
        return round(sum(breakdown.values()), 4), breakdown

    @staticmethod
    def _score_identifiers(citation, entry):
        breakdown = {}
        blob = _clean(f"{citation.raw_text} {citation.context_text}").rstrip("/.,;")
        if entry.doi and _clean(entry.doi) in blob:
            breakdown["doi"] = MATCH_WEIGHTS["doi"]
        if entry.url and citation.author_keys and _clean(entry.url).rstrip("/.,;") in blob:
            breakdown["url"] = MATCH_WEIGHTS["url"]
        return breakdown

    @staticmethod
    def _score_authors(citation, entry):
        breakdown = {}
        surname = normalized_name(entry.first_author_surname)
        authors = [normalized_name(author) for author in citation.author_keys]
        exact = bool(surname) and any(_tokens(surname) <= _tokens(author) for author in authors)
        fuzzy = bool(surname) and any(_fuzzy_ratio(surname, author) >= _FUZZY_SURNAME_THRESHOLD for author in authors)
        year_matches = bool(citation.year and entry.year) and (
            citation.year[:4] == entry.year[:4] or _entry_mentions_year(entry, citation.year))
        if (exact or fuzzy) and year_matches:
            breakdown["author_year_combined"] = MATCH_WEIGHTS["author_year_combined"]
        else:
            if exact or fuzzy:
                breakdown["author_surname"] = MATCH_WEIGHTS["author_surname"]
                if not exact:
                    breakdown["fuzzy_surname"] = MATCH_WEIGHTS["fuzzy_surname"]
            if year_matches:
                breakdown["year"] = MATCH_WEIGHTS["year"]
        key = entry.normalized_keys.get("surname_year")
        if key and any(_clean(key) in _tokens(_clean(author)) for author in citation.author_keys):
            if "author_year_combined" not in breakdown:
                breakdown["normalized_key_match"] = MATCH_WEIGHTS["author_year_combined"] * 0.9
        return breakdown

    @staticmethod
    def _score_title(citation, entry):
        if not entry.title or not citation.context_text or not citation.author_keys:
            return {}
        title_words = set(_clean(entry.title).split())
        overlap = title_words & set(_clean(citation.context_text).split())
        if len(overlap) < 3:
            return {}
        weight = MATCH_WEIGHTS["title_phrase"] * len(overlap) / len(title_words)
        return {"title_phrase": round(weight, 3)} if weight > 0.1 else {}

    def _score_number(self, citation, entry):
        breakdown = {}
        total = 0.0
        ref = entry.reference_number
        if ref and ref in citation.numeric_keys:
            w = MATCH_WEIGHTS["exact_reference_number"]
            breakdown["exact_reference_number"] = w
            total += w
        elif ref:
            # Compatible numbering: ref is plausibly in range of citations
            try:
                n = int(ref)
                if any(self._numeric_range_includes(nk, n) for nk in citation.numeric_keys):
                    w = MATCH_WEIGHTS["numbering_compatible"]
                    breakdown["numbering_compatible"] = w
                    total += w
            except ValueError:
                pass

        return round(total, 4), breakdown

    @staticmethod
    def _numeric_range_includes(key: str, ref_num: int) -> bool:
        """Check if a numeric key like "4-6" or "2,3" includes ref_num."""
        # Range: "4-6"
        m = re.match(r"(\d+)-(\d+)", key)
        if m:
            return int(m.group(1)) <= ref_num <= int(m.group(2))
        # Single
        if key.isdigit():
            return int(key) == ref_num
        # List: "2,3,5"
        parts = re.split(r"[,;]", key)
        return any(p.strip().isdigit() and int(p.strip()) == ref_num for p in parts)


def _ambiguity(scored, best_score, breakdown):
    decisive = any(signal in breakdown for signal in _DECISIVE_SIGNALS) and (
        len(scored) == 1 or best_score - scored[1][0] > _AMBIGUITY_DELTA)
    candidates = [row[2].entry_id for row in scored[1:]
                  if best_score - row[0] <= _AMBIGUITY_DELTA]
    ambiguous = bool(candidates and not decisive and best_score >= _MIN_MATCH_THRESHOLD)
    if ambiguous:
        breakdown["ambiguity_penalty"] = -_AMBIGUITY_PENALTY
        best_score -= _AMBIGUITY_PENALTY
    return best_score, ambiguous, candidates if ambiguous else []


def _make_reason(breakdown: Dict[str, float]) -> str:
    """Produce a human-readable reason string from the score breakdown."""
    parts: List[str] = []
    if "exact_reference_number" in breakdown:
        parts.append("reference number matched")
    if "doi" in breakdown:
        parts.append("DOI matched")
    if "legal_reporter" in breakdown:
        parts.append("legal authority matched")
    if "url" in breakdown:
        parts.append("URL matched")
    if "author_year_combined" in breakdown:
        parts.append("author+year matched")
    elif "normalized_key_match" in breakdown:
        parts.append("normalized key matched")
    else:
        if "author_surname" in breakdown:
            parts.append("author surname matched")
        if "year" in breakdown:
            parts.append("year matched")
    if "fuzzy_surname" in breakdown:
        parts.append("fuzzy surname")
    if "title_phrase" in breakdown:
        parts.append("title phrase overlap")
    if "ambiguity_penalty" in breakdown:
        parts.append("ambiguous (penalty applied)")
    return "; ".join(parts) if parts else "no clear signal"


_ENTRY_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})[a-z]?\b")


def _entry_mentions_year(entry, year: str) -> bool:
    """MLA/Chicago entries carry several years ("Sugar Production in Hawaii,
    1946-1960 … vol. 30, 1983"); the parsed ``entry.year`` may be the title's.
    An exact suffix still has to agree."""
    if len(year) > 4:
        return False
    return year[:4] in set(_ENTRY_YEAR_RE.findall(entry.raw_text or ""))


def _conflicting_year(left: str, right: str) -> bool:
    """Suffixes distinguish same-author/year works; an omitted suffix is weak."""
    return left[:4] != right[:4] or (
        len(left) > 4 and len(right) > 4 and left[4:] != right[4:]
    )
