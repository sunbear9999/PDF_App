"""Bounded, deterministic OCR-aware historical query expansion."""
from __future__ import annotations

import re
from typing import Dict, List

from ..models import ArchiveQuery, QueryVariant
from .query_language import interpret_query, normalize_boolean_expression


DEFAULT_MAX_VARIANTS = 8
SUGGESTIONS: Dict[str, List[str]] = {
    "derailment": ["wreck", "train wreck", "cars left the track", "cars left the rails"],
    "landslide": ["slide", "rock slide", "washout", "track blocked"],
    "railroad": ["rail road", "railway", "rail-road"],
    "embankment": ["fill", "grade", "right-of-way"],
    "flood": ["washout", "high water", "flood protection"],
}
OCR_SWAPS = (("m", "rn"), ("rn", "m"), ("d", "cl"), ("i", "l"))


class QueryPlanner:
    def __init__(self, max_variants: int = DEFAULT_MAX_VARIANTS) -> None:
        self._max = max(1, min(int(max_variants), 20))

    def expand(self, query: ArchiveQuery) -> List[QueryVariant]:
        raw = re.sub(r"\s+", " ", query.original_query).strip()
        explicit_boolean = bool(re.search(r"\b(?:AND|OR|NOT)\b|[()]", raw, re.I))
        original = normalize_boolean_expression(raw) if explicit_boolean else raw
        if not original:
            return []
        variants = [QueryVariant(original, "Original query")]
        if original == "*" or explicit_boolean:
            return variants
        self._add(variants, original.replace("-", " "), "Hyphenation variant")
        self._add(variants, re.sub(r"[^\w\s'-]", " ", original), "Punctuation variant")
        for token in self._tokens(original):
            for suggestion in SUGGESTIONS.get(token.lower(), []):
                self._add(
                    variants,
                    re.sub(rf"\b{re.escape(token)}\b", suggestion, original, flags=re.I),
                    f"Historical term for “{token}”",
                )
            if len(token) >= 5:
                for old, new in OCR_SWAPS:
                    if old in token.lower():
                        changed = re.sub(old, new, token, count=1, flags=re.I)
                        self._add(
                            variants,
                            original.replace(token, changed),
                            f"Likely OCR substitution in “{token}”",
                        )
                        break
            if len(variants) >= self._max:
                break
        return variants[: self._max]

    def suggestions(self, text: str) -> List[str]:
        found = []
        for token in self._tokens(text):
            for suggestion in SUGGESTIONS.get(token.lower(), []):
                if suggestion not in found:
                    found.append(suggestion)
        return found[:8]

    def interpret(self, query: ArchiveQuery):
        return interpret_query(query)

    @staticmethod
    def _tokens(value: str) -> List[str]:
        return re.findall(r"[A-Za-z][A-Za-z'-]+", value)

    def _add(self, variants, text, reason) -> None:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if cleaned and cleaned.lower() not in {v.text.lower() for v in variants}:
            variants.append(QueryVariant(cleaned, reason))
