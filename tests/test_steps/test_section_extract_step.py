"""Tests for SectionExtractStep (SECTION_EXTRACT) — real section-boundary
chunking with fallback to the legacy page-based chunker.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.engine.steps.section_extract_step import SectionExtractStep


def _block(block_id, section_path, text, page=1):
    return SimpleNamespace(block_id=block_id, section_path=section_path, exact_text=text, page_num=page)


def _make_context(document_extraction_service=None, state=None):
    ctx = SimpleNamespace()
    ctx.state = state or {}
    ctx.document_extraction_service = document_extraction_service
    ctx.project_manager = None
    ctx.prompt_manager = None
    ctx.ontology_registry = None
    return ctx


class TestSectionExtractStepRealSections(unittest.TestCase):
    def test_small_sections_merge_into_one_chunk_up_to_char_budget(self):
        """Regression test: grouping by the FULL nested section_path (instead of
        top-level only) used to emit one chunk per subsection, wildly
        over-fragmenting analysis (e.g. 13 chunks for a 10-page paper). Small
        sections must now merge together into shared chunks up to max_chars.
        """
        blocks = [
            _block("b1", ["Introduction"], "Intro text one.", page=1),
            _block("b2", ["Introduction"], "Intro text two.", page=1),
            _block("b3", ["Methods"], "Methods text.", page=2),
            _block("b4", ["Discussion"], "Discussion text.", page=3),
        ]
        doc_ext = MagicMock()
        doc_ext.get_blocks.return_value = blocks
        doc_ext.get_sections.return_value = [
            SimpleNamespace(section_id="s1"), SimpleNamespace(section_id="s2"), SimpleNamespace(section_id="s3"),
        ]
        ctx = _make_context(document_extraction_service=doc_ext)

        result = SectionExtractStep().execute(ctx, {
            "doc_path": "/doc.pdf",
            "template": {"instructions": "Find claims"},
            "contract": {"limits": {"max_chunk_chars": 14000}, "prompt_contract": "SCHEMA"},
            "template_id": "argument_map",
        })

        chunks = result.raw_value
        # all 3 tiny sections comfortably fit in one 14000-char budget chunk
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["total_sections"], 3)
        self.assertIn("Intro text one.", chunks[0]["text"])
        self.assertIn("Methods text.", chunks[0]["text"])
        self.assertIn("Discussion text.", chunks[0]["text"])

    def test_sections_split_into_separate_chunks_when_budget_is_tight(self):
        blocks = [
            _block("b1", ["Introduction"], "x" * 100, page=1),
            _block("b2", ["Methods"], "y" * 100, page=2),
            _block("b3", ["Discussion"], "z" * 100, page=3),
        ]
        doc_ext = MagicMock()
        doc_ext.get_blocks.return_value = blocks
        doc_ext.get_sections.return_value = [
            SimpleNamespace(section_id="s1"), SimpleNamespace(section_id="s2"), SimpleNamespace(section_id="s3"),
        ]
        ctx = _make_context(document_extraction_service=doc_ext)

        # budget fits exactly one 100-char section per chunk
        result = SectionExtractStep().execute(ctx, {
            "doc_path": "/doc.pdf",
            "template": {"instructions": ""},
            "contract": {"limits": {"max_chunk_chars": 100}, "prompt_contract": "SCHEMA"},
            "template_id": "t1",
        })
        chunks = result.raw_value
        self.assertEqual(len(chunks), 3)
        self.assertEqual([c["section_number"] for c in chunks], [1, 2, 3])
        self.assertEqual(chunks[0]["total_sections"], 3)

    def test_ignores_nested_subheadings_and_groups_by_top_level_only(self):
        """A document with many subsections under few top-level headings must
        NOT produce one chunk per subsection."""
        blocks = [
            _block("b1", ["1 Introduction"], "Intro text.", page=1),
            _block("b2", ["2 Methods", "2.1 Data"], "Data text.", page=2),
            _block("b3", ["2 Methods", "2.2 Model"], "Model text.", page=2),
            _block("b4", ["2 Methods", "2.3 Evaluation"], "Eval text.", page=3),
            _block("b5", ["3 Results"], "Results text.", page=4),
        ]
        doc_ext = MagicMock()
        doc_ext.get_blocks.return_value = blocks
        doc_ext.get_sections.return_value = [SimpleNamespace(section_id=f"s{i}") for i in range(5)]
        ctx = _make_context(document_extraction_service=doc_ext)

        result = SectionExtractStep().execute(ctx, {
            "doc_path": "/doc.pdf",
            "template": {"instructions": ""},
            "contract": {"limits": {"max_chunk_chars": 14000}, "prompt_contract": "SCHEMA"},
            "template_id": "t1",
        })
        chunks = result.raw_value
        # 3 top-level sections (Introduction, Methods, Results), not 5 subsections —
        # small enough overall to merge into a single chunk given the large budget
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["total_sections"], 3)
        for text in ("Intro text.", "Data text.", "Model text.", "Eval text.", "Results text."):
            self.assertIn(text, chunks[0]["text"])

    def test_sub_splits_large_sections_by_char_budget(self):
        blocks = [
            _block("b1", ["Intro"], "x" * 100, page=1),
            _block("b2", ["Intro"], "y" * 100, page=1),
        ]
        doc_ext = MagicMock()
        doc_ext.get_blocks.return_value = blocks
        doc_ext.get_sections.return_value = [SimpleNamespace(section_id="s1"), SimpleNamespace(section_id="s2")]
        ctx = _make_context(document_extraction_service=doc_ext)

        result = SectionExtractStep().execute(ctx, {
            "doc_path": "/doc.pdf",
            "template": {"instructions": ""},
            "contract": {"limits": {"max_chunk_chars": 100}, "prompt_contract": "SCHEMA"},
            "template_id": "t1",
        })
        chunks = result.raw_value
        # 100-char budget forces the two 100-char blocks into separate sub-chunks
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["section_number"], 1)
        self.assertEqual(chunks[1]["section_number"], 1)  # same section, second sub-chunk


class TestSectionExtractStepFallback(unittest.TestCase):
    def test_falls_back_when_no_extraction_service(self):
        ctx = _make_context(document_extraction_service=None)
        legacy_chunks = [
            {"doc_path": "/doc.pdf", "chunk_index": 0, "text": "page 1-4 text", "page_range": "1-4"},
        ]
        with patch("core.engine.analysis_runtime.AnalysisRuntime.chunk_document", return_value=legacy_chunks):
            result = SectionExtractStep().execute(ctx, {
                "doc_path": "/doc.pdf", "template": {}, "contract": {}, "template_id": "t1",
            })
        chunks = result.raw_value
        self.assertEqual(len(chunks), 1)
        self.assertIsNone(chunks[0]["section_id"])
        self.assertEqual(chunks[0]["section_number"], 1)
        self.assertEqual(chunks[0]["total_sections"], 1)
        self.assertEqual(result.state_updates["analysis_total_sections"], 1)

    def test_falls_back_when_only_one_section_detected(self):
        blocks = [_block("b1", [], "flat text", page=1)]
        doc_ext = MagicMock()
        doc_ext.get_blocks.return_value = blocks
        doc_ext.get_sections.return_value = [SimpleNamespace(section_id="s1")]  # only 1 section — no real structure
        ctx = _make_context(document_extraction_service=doc_ext)

        legacy_chunks = [{"doc_path": "/doc.pdf", "chunk_index": 0, "text": "fallback text", "page_range": "1-1"}]
        with patch("core.engine.analysis_runtime.AnalysisRuntime.chunk_document", return_value=legacy_chunks):
            result = SectionExtractStep().execute(ctx, {
                "doc_path": "/doc.pdf", "template": {}, "contract": {}, "template_id": "t1",
            })
        self.assertEqual(len(result.raw_value), 1)
        self.assertIsNone(result.raw_value[0]["section_id"])


if __name__ == "__main__":
    unittest.main()
