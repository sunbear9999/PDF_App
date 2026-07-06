"""Tests for BuildDeterministicQuotesStep (BUILD_DETERMINISTIC_QUOTES) — fully
deterministic, no-LLM conversion of RANK_BLOCKS's ranked candidates into
quote records shaped exactly like the (now-removed) per-chunk LLM call used
to produce, so EVIDENCE_STORE's verbatim-quote validation always passes by
construction.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.engine.steps.build_deterministic_quotes_step import BuildDeterministicQuotesStep


def _make_context():
    return SimpleNamespace(state={})


class TestBuildDeterministicQuotesStep(unittest.TestCase):
    def test_picks_best_sentence_per_candidate_and_produces_verbatim_quotes(self):
        candidates = [
            {
                "exact_text": (
                    "The methodology used standard equipment. Therefore, the results "
                    "confirm that AI training causes a significant rise in emissions."
                ),
                "score": 0.9,
                "page_num": 3,
            },
        ]
        result = BuildDeterministicQuotesStep().execute(_make_context(), {
            "candidates": candidates, "quote_words": 10, "max_quote_words": 20, "max_quotes": 5,
        })
        quotes = result.raw_value["quotes"]
        self.assertEqual(len(quotes), 1)
        # the causal-marker sentence must be chosen over the plain methodology sentence
        self.assertIn("Therefore", quotes[0]["exact_text"])
        # quote text must be a verbatim substring of the source block (EVIDENCE_STORE
        # validates this) — construction guarantees it, but assert explicitly anyway
        self.assertIn(quotes[0]["exact_text"].rstrip("."), candidates[0]["exact_text"])
        self.assertEqual(quotes[0]["note"], "Causal reasoning")
        self.assertEqual(quotes[0]["page_num"], 3)
        self.assertAlmostEqual(quotes[0]["importance"], 0.9)

    def test_statistical_sentence_gets_data_role(self):
        candidates = [{"exact_text": "The results were mixed. The study found that p<0.05 for the primary outcome measure.", "score": 0.7, "page_num": 1}]
        result = BuildDeterministicQuotesStep().execute(_make_context(), {"candidates": candidates})
        quotes = result.raw_value["quotes"]
        self.assertEqual(quotes[0]["role"], "data")
        self.assertEqual(quotes[0]["note"], "Statistical/data evidence")

    def test_respects_max_quotes_limit(self):
        candidates = [
            {"exact_text": f"Therefore this is important reasoning number {i} in the argument.", "score": 1.0 - i * 0.1, "page_num": i}
            for i in range(10)
        ]
        result = BuildDeterministicQuotesStep().execute(_make_context(), {"candidates": candidates, "max_quotes": 3})
        self.assertEqual(len(result.raw_value["quotes"]), 3)

    def test_trims_to_max_quote_words(self):
        long_sentence = "Therefore " + " ".join(f"word{i}" for i in range(40)) + "."
        candidates = [{"exact_text": long_sentence, "score": 0.8, "page_num": 1}]
        result = BuildDeterministicQuotesStep().execute(_make_context(), {"candidates": candidates, "max_quote_words": 12})
        quote_text = result.raw_value["quotes"][0]["exact_text"]
        self.assertLessEqual(len(quote_text.split()), 12)

    def test_falls_back_to_paragraph_split_when_no_candidates(self):
        fallback_text = (
            "First paragraph with no markers at all here.\n\n"
            "Second paragraph. Therefore this one has a causal marker in it clearly."
        )
        result = BuildDeterministicQuotesStep().execute(_make_context(), {
            "candidates": [], "fallback_text": fallback_text,
        })
        quotes = result.raw_value["quotes"]
        self.assertGreater(len(quotes), 0)

    def test_deduplicates_identical_sentences_across_candidates(self):
        candidates = [
            {"exact_text": "Therefore the same claim repeats verbatim here.", "score": 0.9, "page_num": 1},
            {"exact_text": "Therefore the same claim repeats verbatim here.", "score": 0.5, "page_num": 2},
        ]
        result = BuildDeterministicQuotesStep().execute(_make_context(), {"candidates": candidates})
        self.assertEqual(len(result.raw_value["quotes"]), 1)

    def test_empty_candidates_and_empty_fallback_produces_no_quotes(self):
        result = BuildDeterministicQuotesStep().execute(_make_context(), {"candidates": [], "fallback_text": ""})
        self.assertEqual(result.raw_value["quotes"], [])


if __name__ == "__main__":
    unittest.main()
