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

# Try to use rapidfuzz for faster/better fuzzy matching; fall back to difflib.
try:
    from rapidfuzz import fuzz as _rf_fuzz  # type: ignore
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False

_EXTRACTOR_VERSION = "1.0"

# ---- Scoring weights -------------------------------------------------------
# Weights sum to roughly 1.0 for an exact match on every applicable signal.
# Ambiguity penalty is subtracted when two entries are within 0.1 of each other.

MATCH_WEIGHTS: Dict[str, float] = {
    "exact_reference_number":  1.00,  # [1] matches ref 1 exactly
    "author_year_combined":    0.80,  # surname + year both match
    "doi":                     1.00,  # DOI is globally unique
    "url":                     0.90,  # URL is usually unique
    "footnote_endnote_id":     0.90,  # footnote number match
    "legal_reporter":          0.85,  # legal reporter match
    "author_surname":          0.40,  # surname-only match
    "year":                    0.30,  # year-only match
    "title_phrase":            0.50,  # partial title phrase match
    "fuzzy_surname":           0.25,  # fuzzy surname (difflib/rapidfuzz)
    "numbering_compatible":    0.35,  # numeric key in plausible range
}
_AMBIGUITY_PENALTY = 0.30
_AMBIGUITY_DELTA = 0.10     # two entries within this → ambiguous
_MIN_MATCH_THRESHOLD = 0.30 # below this → unmatched


def _fuzzy_ratio(a: str, b: str) -> float:
    """Return similarity 0–1 between two strings."""
    if _HAS_RAPIDFUZZ:
        return _rf_fuzz.ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b).ratio()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


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

        # Ambiguity: second candidate within delta
        is_ambiguous = False
        alternates: List[str] = []
        if len(scored) > 1:
            second_score = scored[1][0]
            if best_score >= _MIN_MATCH_THRESHOLD and (best_score - second_score) <= _AMBIGUITY_DELTA:
                is_ambiguous = True
                best_score -= _AMBIGUITY_PENALTY
                best_breakdown["ambiguity_penalty"] = -_AMBIGUITY_PENALTY
                alternates = [scored[1][2].entry_id]

        confidence = max(0.0, min(1.0, best_score))
        if confidence < _MIN_MATCH_THRESHOLD:
            confidence = 0.0

        reason = _make_reason(best_breakdown)

        return CitationMatch(
            match_id=str(uuid.uuid4()),
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
        """Match a list of in-text citations against all bibliography entries."""
        return [m for c in citations if (m := self.match(c, entries)) is not None]

    # ------------------------------------------------------------------

    def _score(
        self,
        citation: InTextCitation,
        entry: BibliographyEntry,
    ) -> Tuple[float, Dict[str, float]]:
        breakdown: Dict[str, float] = {}
        total = 0.0

        # ── Numeric key match ─────────────────────────────────────────
        if citation.citation_type in ("numeric", "superscript") and citation.numeric_keys:
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

        # ── DOI match ─────────────────────────────────────────────────
        if citation.author_keys and entry.doi:
            pass  # DOI rarely appears in-text; handled via bib side
        # If the in-text citation somehow contains a DOI segment and matches
        if entry.doi:
            doi_clean = _clean(entry.doi)
            if any(doi_clean in _clean(ak) for ak in citation.author_keys):
                w = MATCH_WEIGHTS["doi"]
                breakdown["doi"] = w
                total += w

        # ── Author-year combined ───────────────────────────────────────
        surname = _clean(entry.first_author_surname)
        year = (entry.year or "").strip()

        author_matched = False
        year_matched = citation.year and year and citation.year == year

        if surname:
            for ak in citation.author_keys:
                ak_clean = _clean(ak)
                if surname and surname in ak_clean:
                    author_matched = True
                    break
                ratio = _fuzzy_ratio(surname, ak_clean)
                if ratio >= 0.88:
                    author_matched = True
                    break

        if author_matched and year_matched:
            w = MATCH_WEIGHTS["author_year_combined"]
            breakdown["author_year_combined"] = w
            total += w
        else:
            if author_matched:
                w = MATCH_WEIGHTS["author_surname"]
                breakdown["author_surname"] = w
                total += w
                # Fuzzy bonus if it was fuzzy
                if not any(surname in _clean(ak) for ak in citation.author_keys):
                    breakdown["fuzzy_surname"] = MATCH_WEIGHTS["fuzzy_surname"]
                    total += MATCH_WEIGHTS["fuzzy_surname"]
            if year_matched:
                w = MATCH_WEIGHTS["year"]
                breakdown["year"] = w
                total += w

        # ── Normalized key lookup ──────────────────────────────────────
        for key_type, key_val in entry.normalized_keys.items():
            if key_type == "surname_year" and citation.author_keys:
                for ak in citation.author_keys:
                    if _clean(key_val) in _clean(ak):
                        if "author_year_combined" not in breakdown:
                            w = MATCH_WEIGHTS["author_year_combined"] * 0.9
                            breakdown["normalized_key_match"] = w
                            total += w
                        break

        # ── Title phrase match ─────────────────────────────────────────
        if entry.title and citation.context_text:
            title_words = set(_clean(entry.title).split())
            context_words = set(_clean(citation.context_text).split())
            overlap = title_words & context_words
            if len(overlap) >= 3 and len(title_words) > 0:
                coverage = len(overlap) / len(title_words)
                w = MATCH_WEIGHTS["title_phrase"] * min(coverage, 1.0)
                if w > 0.1:
                    breakdown["title_phrase"] = round(w, 3)
                    total += w

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


def _make_reason(breakdown: Dict[str, float]) -> str:
    """Produce a human-readable reason string from the score breakdown."""
    parts: List[str] = []
    if "exact_reference_number" in breakdown:
        parts.append("reference number matched")
    if "doi" in breakdown:
        parts.append("DOI matched")
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
