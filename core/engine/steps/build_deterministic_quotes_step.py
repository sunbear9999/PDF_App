"""
core/engine/steps/build_deterministic_quotes_step.py

Deterministic, no-LLM step: converts RANK_BLOCKS's already-scored candidate
blocks directly into quote records shaped exactly like the evidence-extraction
LLM call used to produce, so EVIDENCE_STORE can consume them without any LLM
round-trip. A lightweight sentence-level reranker (reusing the same causal/
argument/statistical marker regexes RankingFeatureService already scores
blocks with) picks the single best-fitting sentence per candidate block and
trims it to the template's target quote length.

This exists because RANK_BLOCKS already identifies the most relevant text
deterministically — asking an LLM to re-derive quotes from that same
pre-filtered text is redundant load and an extra point of failure (timeouts,
dropped connections) for a per-chunk stage that runs many times per analysis.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext
from core.services.extraction.ranking_feature_service import (
    _ARGUMENT_RE,
    _CAUSAL_RE,
    _STATISTICAL_RE,
)

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"“‘\'])')
_MIN_SENTENCE_WORDS = 4

DEFAULT_QUOTE_WORDS = 10
DEFAULT_MAX_QUOTE_WORDS = 18
DEFAULT_MAX_QUOTES = 5


class BuildDeterministicQuotesStep(BaseStep):
    step_type = "BUILD_DETERMINISTIC_QUOTES"
    label = "Build Deterministic Quotes"
    category = "Analysis"
    description = (
        "Convert RANK_BLOCKS's ranked candidate blocks directly into quote "
        "records via a lightweight sentence-level reranker — no LLM call."
    )
    input_schema = {
        "candidates": {"type": "json", "label": "Ranked candidates from RANK_BLOCKS: [{block_id, exact_text, score, page_num}]"},
        "fallback_text": {"type": "textarea", "label": "Raw/compact chunk text used when no ranked candidates are available"},
        "quote_words": {"type": "integer", "label": "Target quote length in words", "default": DEFAULT_QUOTE_WORDS},
        "max_quote_words": {"type": "integer", "label": "Maximum quote length in words", "default": DEFAULT_MAX_QUOTE_WORDS},
        "max_quotes": {"type": "integer", "label": "Maximum quotes to produce", "default": DEFAULT_MAX_QUOTES},
    }
    output_schema = {
        "quotes": {"type": "json", "label": "Quote records, same shape EVIDENCE_STORE expects"},
    }

    def execute(self, context: StepContext, inputs: dict):
        candidates = inputs.get("candidates") or []
        if not isinstance(candidates, list):
            candidates = []
        quote_words = _safe_int(inputs.get("quote_words")) or DEFAULT_QUOTE_WORDS
        max_quote_words = _safe_int(inputs.get("max_quote_words")) or DEFAULT_MAX_QUOTE_WORDS
        max_quotes = _safe_int(inputs.get("max_quotes")) or DEFAULT_MAX_QUOTES

        if not candidates:
            # No ranked candidates (sparse chunk, or template hasn't configured
            # deterministic_passes) — fall back to paragraph-level scoring over
            # the raw/compact chunk text so a chunk never silently yields zero
            # quotes just because ranking had nothing to offer.
            fallback_text = str(inputs.get("fallback_text") or "")
            candidates = [
                {"exact_text": para, "score": 0.5, "page_num": None}
                for para in re.split(r"\n\s*\n", fallback_text) if para.strip()
            ]

        quotes: List[Dict[str, Any]] = []
        seen_text: set = set()

        for candidate in candidates:
            if len(quotes) >= max_quotes:
                break
            if not isinstance(candidate, dict):
                continue
            text = str(candidate.get("exact_text") or "")
            sentence, causal_hits, argument_hits, stat_hits = _best_sentence(text, quote_words, max_quote_words)
            if not sentence:
                continue
            dedupe_key = re.sub(r"\W+", "", sentence.lower())
            if dedupe_key in seen_text:
                continue
            seen_text.add(dedupe_key)

            quotes.append({
                "exact_text": sentence,
                "note": _deterministic_note(causal_hits, argument_hits, stat_hits),
                "role": "data" if stat_hits else "supports",
                "importance": _clamp01(candidate.get("score")),
                "page_num": candidate.get("page_num"),
            })

        return self.build_result({"quotes": quotes})


def _best_sentence(text: str, quote_words: int, max_quote_words: int):
    """Pick the single best-scoring sentence in a block for use as a quote.

    Falls back to the first substantial sentence (trimmed to max_quote_words)
    if no discourse markers matched anything, so a block never contributes
    zero quotes purely because it lacks a causal/argument/statistical cue.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    best = None
    best_score = -1.0
    best_hits = (0, 0, 0)
    for sentence in sentences:
        word_count = len(sentence.split())
        if word_count < _MIN_SENTENCE_WORDS:
            continue
        causal_hits = len(_CAUSAL_RE.findall(sentence))
        argument_hits = len(_ARGUMENT_RE.findall(sentence))
        stat_hits = len(_STATISTICAL_RE.findall(sentence))
        length_penalty = abs(word_count - quote_words) * 0.02
        score = causal_hits * 2.0 + argument_hits * 1.5 + stat_hits * 1.5 - length_penalty
        if score > best_score:
            best_score = score
            best = sentence
            best_hits = (causal_hits, argument_hits, stat_hits)

    if best is None and sentences:
        # No sentence cleared the minimum word count as a standalone match —
        # fall back to the longest sentence available rather than skip the block.
        best = max(sentences, key=lambda s: len(s.split()), default="")
        best_hits = (0, 0, 0)

    if not best:
        return "", 0, 0, 0

    words = best.split()
    if len(words) > max_quote_words:
        best = " ".join(words[:max_quote_words])
    return best, best_hits[0], best_hits[1], best_hits[2]


def _deterministic_note(causal_hits: int, argument_hits: int, stat_hits: int) -> str:
    if stat_hits:
        return "Statistical/data evidence"
    if causal_hits:
        return "Causal reasoning"
    if argument_hits:
        return "Contrasting or qualifying point"
    return "Relevant supporting text"


def _clamp01(val) -> float:
    try:
        return max(0.0, min(1.0, float(val)))
    except (TypeError, ValueError):
        return 0.5


def _safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
