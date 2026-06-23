"""Tests for the hierarchical evidence-to-graph analysis pipeline.

Covers:
  EvidenceStoreStep  — stable IDs, event emission, DB save, empty-skip, dict return
  SectionGroupStep   — 5-chunk grouping, compact-refs format (no full text)
  QuoteHydrationStep — attaches exact text, flags missing IDs
  EvidenceDB         — round-trip save/get via temp SQLite
  AnalysisContractStep — state_updates includes all new hierarchical prompt keys
"""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock, patch


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_context(state: Dict[str, Any] | None = None, pm=None):
    ctx = SimpleNamespace()
    ctx.state = state or {}
    ctx.project_manager = pm
    ctx.prompt_manager = None
    ctx.ontology_registry = None
    ctx.llm_manager = None
    return ctx


def _stable_id(run_id: str, exact_text: str) -> str:
    return hashlib.sha1(f"{run_id}:{exact_text}".encode()).hexdigest()[:16]


def _chunk_evidence_input(exact_text: str, run_id: str = "run1", chunk_id: int = 0):
    """Build minimal inputs dict for EvidenceStoreStep."""
    return {
        "chunk_evidence": {"quotes": [{"exact_text": exact_text, "note": "test note",
                                        "role": "supports", "importance": 0.8}]},
        "chunk_id": chunk_id,
    }


# ── EvidenceStoreStep ──────────────────────────────────────────────────────────

class TestEvidenceStoreStep(unittest.TestCase):

    def _run(self, state=None, pm=None, inputs=None):
        from core.engine.steps.evidence_store_step import EvidenceStoreStep
        step = EvidenceStoreStep()
        ctx = _make_context(state or {"analysis_run_id": "run1", "analysis_doc_path": "/doc.pdf"}, pm)
        return step.execute(ctx, inputs or _chunk_evidence_input("exact quote text here"))

    def test_stable_ids_same_text_produces_same_id(self):
        pm = MagicMock()
        result1 = self._run(pm=pm, inputs=_chunk_evidence_input("hello world"))
        result2 = self._run(pm=pm, inputs=_chunk_evidence_input("hello world"))
        id1 = result1.raw_value["refs"][0]["quote_id"]
        id2 = result2.raw_value["refs"][0]["quote_id"]
        self.assertEqual(id1, id2)

    def test_stable_id_matches_sha1(self):
        pm = MagicMock()
        text = "the quick brown fox"
        result = self._run(pm=pm, inputs=_chunk_evidence_input(text))
        expected = _stable_id("run1", text)
        self.assertEqual(result.raw_value["refs"][0]["quote_id"], expected)

    def test_emits_chunk_evidence_ready_system_event(self):
        from core.events.domains.analysis_events import AnalysisEvent
        result = self._run(pm=MagicMock())
        self.assertTrue(len(result.system_events) >= 1)
        evt = result.system_events[0]
        self.assertEqual(evt.event_type, "bus_emit")
        self.assertEqual(evt.payload["intent"], AnalysisEvent.CHUNK_EVIDENCE_READY)

    def test_saves_to_db_via_project_manager(self):
        pm = MagicMock()
        self._run(pm=pm, inputs=_chunk_evidence_input("save this quote"))
        pm.save_evidence_quote.assert_called_once()

    def test_skips_empty_exact_text(self):
        pm = MagicMock()
        inputs = {"chunk_evidence": {"quotes": [{"exact_text": "", "note": "nothing"}]}, "chunk_id": 0}
        result = self._run(pm=pm, inputs=inputs)
        self.assertEqual(result.raw_value["refs"], [])
        pm.save_evidence_quote.assert_not_called()

    def test_returns_dict_not_list(self):
        pm = MagicMock()
        result = self._run(pm=pm)
        self.assertIsInstance(result.raw_value, dict)
        self.assertIn("chunk_id", result.raw_value)
        self.assertIn("refs", result.raw_value)

    def test_snippet_is_at_most_100_chars(self):
        pm = MagicMock()
        long_text = "x" * 300
        result = self._run(pm=pm, inputs=_chunk_evidence_input(long_text))
        snippet = result.raw_value["refs"][0]["snippet"]
        self.assertLessEqual(len(snippet), 100)


# ── SectionGroupStep ───────────────────────────────────────────────────────────

class TestSectionGroupStep(unittest.TestCase):

    def _make_evidence(self, n_chunks: int):
        return [
            {"chunk_id": i, "refs": [{"quote_id": f"q{i}", "snippet": f"snippet {i}",
                                       "note": "n", "role": "supports", "importance": 0.5}]}
            for i in range(n_chunks)
        ]

    def _run(self, all_chunk_evidence, chunks_per_section=5):
        from core.engine.steps.section_synthesis_step import SectionGroupStep
        step = SectionGroupStep()
        ctx = _make_context()
        return step.execute(ctx, {"all_chunk_evidence": all_chunk_evidence,
                                   "chunks_per_section": chunks_per_section})

    def test_groups_11_chunks_into_5_5_1(self):
        evidence = self._make_evidence(11)
        result = self._run(evidence)
        groups = result.raw_value
        self.assertEqual(len(groups), 3)

    def test_section_ids_are_sequential(self):
        evidence = self._make_evidence(7)
        result = self._run(evidence)
        ids = [g["section_id"] for g in result.raw_value]
        self.assertEqual(ids, ["section_0", "section_1"])

    def test_compact_refs_text_contains_no_exact_text_key(self):
        evidence = [{"chunk_id": 0, "refs": [{"quote_id": "abc", "snippet": "short snip",
                                               "note": "n", "role": "supports", "importance": 0.9,
                                               "exact_text": "SHOULD NOT APPEAR"}]}]
        result = self._run(evidence)
        compact = result.raw_value[0]["compact_refs_text"]
        self.assertNotIn("exact_text", compact)
        self.assertNotIn("SHOULD NOT APPEAR", compact)

    def test_compact_refs_text_contains_quote_id(self):
        evidence = [{"chunk_id": 0, "refs": [{"quote_id": "myqid123", "snippet": "snip",
                                               "note": "n", "role": "supports", "importance": 0.7}]}]
        result = self._run(evidence)
        self.assertIn("myqid123", result.raw_value[0]["compact_refs_text"])

    def test_top_quote_ids_compact_in_state_updates(self):
        evidence = self._make_evidence(3)
        result = self._run(evidence)
        self.assertIn("top_quote_ids_compact", result.state_updates)

    def test_empty_input_returns_empty_groups(self):
        result = self._run([])
        self.assertEqual(result.raw_value, [])

    def test_total_sections_set_correctly(self):
        evidence = self._make_evidence(6)
        result = self._run(evidence, chunks_per_section=5)
        for g in result.raw_value:
            self.assertEqual(g["total_sections"], 2)


# ── QuoteHydrationStep ─────────────────────────────────────────────────────────

class TestQuoteHydrationStep(unittest.TestCase):

    def _make_eq(self, quote_id, exact_text, chunk_id=0):
        from core.models.analysis_models import EvidenceQuote
        return EvidenceQuote(
            quote_id=quote_id, run_id="run1", doc_path="/doc.pdf",
            chunk_id=chunk_id, exact_text=exact_text, snippet=exact_text[:100],
            page_num=3, note_text="", importance_score=0.8, confidence_score=0.8,
            suggested_node_type="CLAIM", quote_role="supports",
            tags_json="[]", created_at="2026-01-01",
        )

    def _run(self, graph, quotes):
        from core.engine.steps.quote_hydration_step import QuoteHydrationStep
        pm = MagicMock()
        pm.get_evidence_quotes_for_run.return_value = quotes
        step = QuoteHydrationStep()
        ctx = _make_context({"analysis_run_id": "run1"}, pm)
        return step.execute(ctx, {"assembled_graph": graph})

    def test_attaches_exact_text_to_entity(self):
        eq = self._make_eq("qid001", "The population grew by 15%.")
        graph = {"entities": [{"id": "e1", "properties": {"source_quote_id": "qid001"}}]}
        result = self._run(graph, [eq])
        entity = result.raw_value["entities"][0]
        self.assertEqual(entity["exact_text"], "The population grew by 15%.")
        self.assertTrue(entity["properties"]["quote_verified"])

    def test_flags_missing_ids(self):
        graph = {"entities": [{"id": "e1", "properties": {"source_quote_id": "missing_id"}}]}
        result = self._run(graph, [])
        self.assertIn("missing_id", result.raw_value["hydration_missing_ids"])

    def test_no_extra_text_invented_for_missing(self):
        graph = {"entities": [{"id": "e1", "properties": {"source_quote_id": "ghost"}}]}
        result = self._run(graph, [])
        entity = result.raw_value["entities"][0]
        self.assertNotIn("exact_text", entity)

    def test_entities_without_quote_id_untouched(self):
        graph = {"entities": [{"id": "e1", "label": "Concept", "properties": {}}]}
        result = self._run(graph, [])
        self.assertNotIn("exact_text", result.raw_value["entities"][0])
        self.assertEqual(result.raw_value["hydration_missing_ids"], [])

    def test_emits_graph_hydrated_event(self):
        from core.events.domains.analysis_events import AnalysisEvent
        graph = {"entities": []}
        result = self._run(graph, [])
        self.assertTrue(len(result.system_events) >= 1)
        evt = result.system_events[0]
        self.assertEqual(evt.payload["intent"], AnalysisEvent.GRAPH_HYDRATED)


# ── EvidenceDB round-trip ──────────────────────────────────────────────────────

class TestEvidenceDBRoundTrip(unittest.TestCase):

    def _init_db(self, path: str):
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_evidence_quotes (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, doc_path TEXT NOT NULL,
                chunk_id INTEGER NOT NULL, exact_text TEXT NOT NULL, snippet TEXT NOT NULL,
                page_num INTEGER, note_text TEXT NOT NULL DEFAULT '',
                importance_score REAL NOT NULL DEFAULT 0.5,
                confidence_score REAL NOT NULL DEFAULT 0.5,
                suggested_node_type TEXT NOT NULL DEFAULT '',
                quote_role TEXT NOT NULL DEFAULT 'supports',
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        return conn

    def _make_db(self, conn, path):
        from core.db.evidence_db import EvidenceDB
        manager = SimpleNamespace(project_filepath=path, _conn=conn)
        db = EvidenceDB(manager)
        return db

    def _make_eq(self, quote_id="abc123"):
        from core.models.analysis_models import EvidenceQuote
        return EvidenceQuote(
            quote_id=quote_id, run_id="run99", doc_path="/p.pdf",
            chunk_id=2, exact_text="Original verbatim text.", snippet="Original verbatim text.",
            page_num=7, note_text="important", importance_score=0.9, confidence_score=0.85,
            suggested_node_type="FACT", quote_role="supports",
            tags_json='["key"]', created_at="2026-06-01",
        )

    def test_save_and_get_round_trip(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            conn = self._init_db(f.name)
            db = self._make_db(conn, f.name)
            eq = self._make_eq("roundtrip1")
            db.save_quote(eq)
            retrieved = db.get_quote("roundtrip1")
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.exact_text, "Original verbatim text.")
            self.assertEqual(retrieved.page_num, 7)
            self.assertAlmostEqual(retrieved.importance_score, 0.9, places=4)

    def test_get_quotes_for_run(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            conn = self._init_db(f.name)
            db = self._make_db(conn, f.name)
            db.save_quote(self._make_eq("q1"))
            db.save_quote(self._make_eq("q2"))
            results = db.get_quotes_for_run("run99")
            self.assertEqual(len(results), 2)

    def test_get_missing_quote_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            conn = self._init_db(f.name)
            db = self._make_db(conn, f.name)
            self.assertIsNone(db.get_quote("nonexistent"))

    def test_get_quotes_for_chunk(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            conn = self._init_db(f.name)
            db = self._make_db(conn, f.name)
            db.save_quote(self._make_eq("c0"))  # chunk_id=2
            results = db.get_quotes_for_chunk("run99", 2)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].quote_id, "c0")


# ── AnalysisContractStep new keys ──────────────────────────────────────────────

class TestAnalysisContractStepNewKeys(unittest.TestCase):

    EXPECTED_KEYS = [
        "analysis_evidence_system_prompt",
        "analysis_evidence_query_prompt",
        "analysis_section_synthesis_system_prompt",
        "analysis_section_synthesis_query_prompt",
        "analysis_graph_plan_system_prompt",
        "analysis_graph_plan_query_prompt",
        "analysis_graph_assembly_system_prompt",
        "analysis_graph_assembly_query_prompt",
    ]

    def test_state_updates_includes_all_hierarchical_prompt_keys(self):
        from core.engine.steps.analysis_contract_step import AnalysisContractStep
        pm_mock = MagicMock()
        pm_mock.get_prompt.return_value = "mock prompt"
        pm_mock.get_ontology.return_value = {}

        ctx = _make_context(pm=MagicMock())
        ctx.prompt_manager = pm_mock
        ctx.ontology_registry = MagicMock()
        ctx.ontology_registry.get_all.return_value = []

        step = AnalysisContractStep()
        with patch("core.engine.analysis_runtime.AnalysisRuntime.build_contract",
                   return_value={"limits": {}, "entities": [], "relations": [],
                                  "prompt_contract": "schema"}), \
             patch("core.engine.analysis_runtime.AnalysisRuntime.build_prompt_contract",
                   return_value="schema"), \
             patch.object(type(step).__mro__[0], "execute",  # call through
                          wraps=step.execute):
            result = step.execute(ctx, {"template": {}})

        all_keys = {**result.state_updates}
        for key in self.EXPECTED_KEYS:
            self.assertIn(key, all_keys, f"Missing expected key: {key}")


if __name__ == "__main__":
    unittest.main()
