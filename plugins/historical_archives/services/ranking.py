"""Deterministic local relevance ranking and cross-provider deduplication."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, List

from ..models import ArchiveQuery, ArchiveResult


class LocalRanker:
    def rank(self, results: Iterable[ArchiveResult], query: ArchiveQuery) -> List[ArchiveResult]:
        term_source = " ".join((
            query.original_query,
            query.all_words,
            query.any_words,
            query.title,
            query.subject,
        ))
        terms = self._terms(term_source)
        phrase = self._ranking_phrase(query)
        ranked = []
        for result in results:
            score, reasons = self._score(result, terms, phrase, query)
            result.local_score = score
            result.match_reasons = reasons
            ranked.append(result)
        return sorted(ranked, key=lambda item: (-item.local_score, item.date, item.title))

    def _score(self, result, terms, phrase, query):
        title = result.title.lower()
        ocr = result.ocr_snippet.lower()
        description = result.description.lower()
        searchable = f"{title} {ocr} {description}"
        score = 0.0
        reasons = []
        if phrase and phrase in searchable:
            score += 40
            reasons.append("Exact phrase found")
        matched = [term for term in terms if term in searchable]
        if matched:
            score += 8 * len(matched) / max(1, len(terms))
            reasons.append(f"{len(matched)}/{len(terms)} query terms found")
        if any(term in title for term in terms):
            score += 12
            reasons.append("Title match")
        if query.location and query.location.lower() in " ".join(result.places).lower():
            score += 10
            reasons.append("Place match")
        if query.publication and query.publication.lower() in result.publication.lower():
            score += 8
            reasons.append("Publication match")
        if query.date_from and result.date and result.date >= query.date_from:
            score += 3
            reasons.append("Date range fit")
        fuzzy = max(
            (SequenceMatcher(None, term, word).ratio() for term in terms for word in self._terms(ocr)),
            default=0.0,
        )
        if fuzzy >= 0.82 and not matched:
            score += fuzzy * 7
            reasons.append("Fuzzy OCR match")
        score += min(result.provider_score, 100.0) / 100.0
        return round(score, 4), reasons or ["Metadata-only match"]

    @staticmethod
    def _terms(value: str) -> List[str]:
        ignored = {"and", "or", "not"}
        return [
            term for term in re.findall(r"[a-z0-9]+", value.lower())
            if len(term) > 1 and term not in ignored
        ]

    @staticmethod
    def _ranking_phrase(query: ArchiveQuery) -> str:
        if query.exact_phrase:
            return query.exact_phrase.lower().strip()
        quoted = re.findall(r'"([^"]+)"', query.original_query)
        if quoted:
            return quoted[0].lower().strip()
        if not re.search(r"\b(?:AND|OR|NOT)\b|[()*]", query.original_query, re.I):
            return query.original_query.lower().strip()
        return ""


class ResultDeduplicator:
    def mark(self, results: Iterable[ArchiveResult]) -> List[ArchiveResult]:
        seen = {}
        output = []
        for result in results:
            key = result.fingerprint
            if key in seen:
                result.duplicate_of = seen[key].provider_key
            else:
                seen[key] = result
            output.append(result)
        return output
