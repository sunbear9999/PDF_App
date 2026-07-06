"""Tests for the atomic deterministic extraction step types.

Covers EXTRACT_DOCUMENT_BLOCKS, EXTRACT_ENTITIES, EXTRACT_DATES,
EXTRACT_CITATIONS, EXTRACT_TABLES, DETECT_PATTERNS — all pure delegation
to (mocked) DocumentExtractionService / PatternDetectorRegistry, no LLM.
"""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_context(**overrides):
    ctx = SimpleNamespace()
    ctx.state = overrides.pop("state", {})
    ctx.document_extraction_service = overrides.pop("document_extraction_service", None)
    ctx.pattern_detector_registry = overrides.pop("pattern_detector_registry", None)
    ctx.ranking_feature_service = overrides.pop("ranking_feature_service", None)
    ctx.deterministic_pass_registry = overrides.pop("deterministic_pass_registry", None)
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


def _block(block_id="b1", text="Some paragraph text.", block_type="paragraph", page=1):
    return SimpleNamespace(
        block_id=block_id, doc_id="doc1", page_num=page, block_type=block_type,
        exact_text=text, normalized_text=text.lower(), heading_level=None,
        section_path=["Intro"], has_superscript=False,
    )


class TestExtractDocumentBlocksStep(unittest.TestCase):
    def test_returns_serialized_blocks(self):
        from core.engine.steps.extract_document_blocks_step import ExtractDocumentBlocksStep
        svc = MagicMock()
        svc.get_blocks.return_value = [_block("b1"), _block("b2")]
        ctx = _make_context(document_extraction_service=svc)
        result = ExtractDocumentBlocksStep().execute(ctx, {"doc_path": "/doc.pdf"})
        self.assertEqual(len(result.raw_value), 2)
        self.assertEqual(result.raw_value[0]["block_id"], "b1")
        svc.get_blocks.assert_called_once_with("/doc.pdf")

    def test_empty_when_no_doc_path(self):
        from core.engine.steps.extract_document_blocks_step import ExtractDocumentBlocksStep
        ctx = _make_context(document_extraction_service=MagicMock())
        result = ExtractDocumentBlocksStep().execute(ctx, {})
        self.assertEqual(result.raw_value, [])

    def test_empty_when_no_service(self):
        from core.engine.steps.extract_document_blocks_step import ExtractDocumentBlocksStep
        ctx = _make_context()
        result = ExtractDocumentBlocksStep().execute(ctx, {"doc_path": "/doc.pdf"})
        self.assertEqual(result.raw_value, [])


class TestExtractEntitiesStep(unittest.TestCase):
    def _entity(self, entity_type="entity.person"):
        return SimpleNamespace(
            entity_id="e1", entity_type=entity_type, canonical_text="Jane Doe",
            aliases=[], properties={},
        )

    def test_returns_entities(self):
        from core.engine.steps.extract_entities_step import ExtractEntitiesStep
        svc = MagicMock()
        svc.get_entities.return_value = [self._entity(), self._entity("entity.organization")]
        ctx = _make_context(document_extraction_service=svc)
        result = ExtractEntitiesStep().execute(ctx, {"doc_path": "/doc.pdf"})
        self.assertEqual(len(result.raw_value), 2)

    def test_filters_by_entity_type(self):
        from core.engine.steps.extract_entities_step import ExtractEntitiesStep
        svc = MagicMock()
        svc.get_entities.return_value = [self._entity("entity.person"), self._entity("entity.organization")]
        ctx = _make_context(document_extraction_service=svc)
        result = ExtractEntitiesStep().execute(ctx, {"doc_path": "/doc.pdf", "entity_type": "entity.organization"})
        self.assertEqual(len(result.raw_value), 1)
        self.assertEqual(result.raw_value[0]["entity_type"], "entity.organization")


class TestExtractDatesStep(unittest.TestCase):
    def test_returns_dates(self):
        from core.engine.steps.extract_dates_step import ExtractDatesStep
        svc = MagicMock()
        svc.get_dates.return_value = [
            SimpleNamespace(date_id="d1", exact_text="2024", normalized_date="2024",
                             certainty="confirmed", context_text="in 2024")
        ]
        ctx = _make_context(document_extraction_service=svc)
        result = ExtractDatesStep().execute(ctx, {"doc_path": "/doc.pdf"})
        self.assertEqual(len(result.raw_value), 1)
        self.assertEqual(result.raw_value[0]["normalized_date"], "2024")


class TestExtractCitationsStep(unittest.TestCase):
    def test_returns_empty_dict_when_no_index(self):
        from core.engine.steps.extract_citations_step import ExtractCitationsStep
        svc = MagicMock()
        svc.get_citation_index.return_value = None
        ctx = _make_context(document_extraction_service=svc)
        result = ExtractCitationsStep().execute(ctx, {"doc_path": "/doc.pdf"})
        self.assertEqual(result.raw_value, {})

    def test_returns_serialized_index(self):
        from core.engine.steps.extract_citations_step import ExtractCitationsStep
        from core.models.extraction_models import CitationIndex
        svc = MagicMock()
        svc.get_citation_index.return_value = CitationIndex(doc_id="doc1")
        ctx = _make_context(document_extraction_service=svc)
        result = ExtractCitationsStep().execute(ctx, {"doc_path": "/doc.pdf"})
        self.assertEqual(result.raw_value["doc_id"], "doc1")


class TestExtractTablesStep(unittest.TestCase):
    def test_returns_tables(self):
        from core.engine.steps.extract_tables_step import ExtractTablesStep
        svc = MagicMock()
        svc.get_tables.return_value = [
            SimpleNamespace(table_id="t1", page_num=3, caption="Table 1", headers=["A", "B"],
                             extraction_strategy="pymupdf", confidence=0.9, dataset_id=None)
        ]
        ctx = _make_context(document_extraction_service=svc)
        result = ExtractTablesStep().execute(ctx, {"doc_path": "/doc.pdf"})
        self.assertEqual(len(result.raw_value), 1)
        self.assertEqual(result.raw_value[0]["table_id"], "t1")


class TestDetectPatternsStep(unittest.TestCase):
    def _pack(self, pack_id="causal_marker", patterns=None):
        return SimpleNamespace(
            pack_id=pack_id,
            compiled=[re.compile(p, re.I) for p in (patterns or [r"\btherefore\b"])],
        )

    def test_scans_raw_text(self):
        from core.engine.steps.detect_patterns_step import DetectPatternsStep
        registry = MagicMock()
        registry.get_packs_for_type.return_value = [self._pack()]
        ctx = _make_context(pattern_detector_registry=registry)
        result = DetectPatternsStep().execute(ctx, {
            "entity_type": "causal_marker",
            "text": "The results were significant, therefore the hypothesis holds.",
        })
        self.assertEqual(len(result.raw_value), 1)
        self.assertEqual(result.raw_value[0]["match_text"].lower(), "therefore")

    def test_scans_document_blocks_when_no_text(self):
        from core.engine.steps.detect_patterns_step import DetectPatternsStep
        registry = MagicMock()
        registry.get_packs_for_type.return_value = [self._pack()]
        svc = MagicMock()
        svc.get_blocks.return_value = [_block("b1", text="Therefore this is relevant.")]
        ctx = _make_context(pattern_detector_registry=registry, document_extraction_service=svc)
        result = DetectPatternsStep().execute(ctx, {"entity_type": "causal_marker", "doc_path": "/doc.pdf"})
        self.assertEqual(len(result.raw_value), 1)
        self.assertEqual(result.raw_value[0]["block_id"], "b1")

    def test_empty_when_no_packs_registered(self):
        from core.engine.steps.detect_patterns_step import DetectPatternsStep
        registry = MagicMock()
        registry.get_packs_for_type.return_value = []
        ctx = _make_context(pattern_detector_registry=registry)
        result = DetectPatternsStep().execute(ctx, {"entity_type": "nonexistent", "text": "anything"})
        self.assertEqual(result.raw_value, [])


if __name__ == "__main__":
    unittest.main()
