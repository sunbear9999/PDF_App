"""Tests for RankBlocksStep (RANK_BLOCKS) — deterministic pre-filter/ranking
primitive used ahead of LLM evidence-extraction calls.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.engine.steps.rank_blocks_step import (
    DEFAULT_BASE_NUM_PREDICT,
    MIN_CANDIDATES,
    RankBlocksStep,
)
from core.models.analysis_models import DeterministicPassOutput
from core.models.extraction_models import RankedBlock


def _make_context(document_extraction_service=None, deterministic_pass_registry=None, state=None):
    ctx = SimpleNamespace()
    ctx.state = state or {}
    ctx.document_extraction_service = document_extraction_service
    ctx.deterministic_pass_registry = deterministic_pass_registry
    return ctx


def _block(block_id, text, page_num=1):
    return SimpleNamespace(block_id=block_id, doc_id="doc1", exact_text=text, page_num=page_num)


def _ranked(block, score):
    return RankedBlock(block_id=block.block_id, doc_id=block.doc_id, score=score, block=block)


class TestRankBlocksStepPassthrough(unittest.TestCase):
    def test_no_passes_configured_passes_through(self):
        ctx = _make_context()
        result = RankBlocksStep().execute(ctx, {
            "passes": [], "fallback_text": "raw text here", "base_num_predict": 900,
        })
        self.assertEqual(result.raw_value["compact_text"], "raw text here")
        self.assertEqual(result.raw_value["suggested_evidence_num_predict"], 900)
        self.assertEqual(result.raw_value["candidate_count"], 0)

    def test_missing_services_passes_through(self):
        ctx = _make_context()
        result = RankBlocksStep().execute(ctx, {
            "passes": [{"pass_id": "quote_candidate_rank"}],
            "fallback_text": "raw text",
        })
        self.assertEqual(result.raw_value["compact_text"], "raw text")
        self.assertEqual(result.raw_value["suggested_evidence_num_predict"], DEFAULT_BASE_NUM_PREDICT)

    def test_sparse_candidates_falls_back_to_raw_text(self):
        blocks = [_block("b1", "one"), _block("b2", "two")]
        doc_ext = MagicMock()
        doc_ext.get_blocks.return_value = blocks
        doc_ext.get_citation_index.return_value = None
        doc_ext.get_entities.return_value = []
        doc_ext.get_dates.return_value = []
        doc_ext.get_tables.return_value = []

        spec = SimpleNamespace(
            pass_id="quote_candidate_rank",
            run_fn=lambda pass_input: DeterministicPassOutput(
                ranked_blocks=[_ranked(blocks[0], 0.5)]  # only 1 candidate — below MIN_CANDIDATES
            ),
        )
        registry = MagicMock()
        registry.get.return_value = spec

        ctx = _make_context(document_extraction_service=doc_ext, deterministic_pass_registry=registry)
        result = RankBlocksStep().execute(ctx, {
            "doc_path": "/doc.pdf",
            "passes": [{"pass_id": "quote_candidate_rank"}],
            "fallback_text": "raw fallback text",
        })
        self.assertEqual(result.raw_value["compact_text"], "raw fallback text")
        self.assertLess(1, MIN_CANDIDATES)  # sanity: the test setup is actually sparse


class TestRankBlocksStepRanking(unittest.TestCase):
    def test_joins_candidates_in_document_order_and_scales_budget(self):
        blocks = [_block(f"b{i}", f"text {i}") for i in range(5)]
        doc_ext = MagicMock()
        doc_ext.get_blocks.return_value = blocks
        doc_ext.get_citation_index.return_value = None
        doc_ext.get_entities.return_value = []
        doc_ext.get_dates.return_value = []
        doc_ext.get_tables.return_value = []

        # Ranked out of document order — step must re-sort into document order.
        ranked_out_of_order = [
            _ranked(blocks[3], 0.9),
            _ranked(blocks[0], 0.7),
            _ranked(blocks[4], 0.6),
        ]
        spec = SimpleNamespace(
            pass_id="quote_candidate_rank",
            run_fn=lambda pass_input: DeterministicPassOutput(ranked_blocks=ranked_out_of_order),
        )
        registry = MagicMock()
        registry.get.return_value = spec

        ctx = _make_context(document_extraction_service=doc_ext, deterministic_pass_registry=registry)
        result = RankBlocksStep().execute(ctx, {
            "doc_path": "/doc.pdf",
            "passes": [{"pass_id": "quote_candidate_rank", "top_n": 10}],
            "fallback_text": "unused",
            "base_num_predict": 800,
        })
        self.assertEqual(result.raw_value["candidate_count"], 3)
        # document order: b0, b3, b4 (not ranked order b3, b0, b4)
        self.assertEqual(result.raw_value["compact_text"], "text 0\n\ntext 3\n\ntext 4")
        self.assertGreater(result.raw_value["suggested_evidence_num_predict"], 800)

    def test_dedupes_across_multiple_passes_keeping_max_score(self):
        blocks = [_block(f"b{i}", f"text {i}") for i in range(4)]
        doc_ext = MagicMock()
        doc_ext.get_blocks.return_value = blocks
        doc_ext.get_citation_index.return_value = None
        doc_ext.get_entities.return_value = []
        doc_ext.get_dates.return_value = []
        doc_ext.get_tables.return_value = []

        spec_a = SimpleNamespace(
            pass_id="pass_a",
            run_fn=lambda pass_input: DeterministicPassOutput(ranked_blocks=[
                _ranked(blocks[0], 0.4), _ranked(blocks[1], 0.3), _ranked(blocks[2], 0.2),
            ]),
        )
        spec_b = SimpleNamespace(
            pass_id="pass_b",
            run_fn=lambda pass_input: DeterministicPassOutput(ranked_blocks=[
                _ranked(blocks[0], 0.9), _ranked(blocks[3], 0.5),
            ]),
        )
        registry = MagicMock()
        registry.get.side_effect = lambda pid: {"pass_a": spec_a, "pass_b": spec_b}.get(pid)

        ctx = _make_context(document_extraction_service=doc_ext, deterministic_pass_registry=registry)
        result = RankBlocksStep().execute(ctx, {
            "doc_path": "/doc.pdf",
            "passes": [{"pass_id": "pass_a"}, {"pass_id": "pass_b"}],
            "fallback_text": "unused",
        })
        # 4 unique blocks survive (b0 deduped, kept once) across both passes
        self.assertEqual(result.raw_value["candidate_count"], 4)


if __name__ == "__main__":
    unittest.main()
