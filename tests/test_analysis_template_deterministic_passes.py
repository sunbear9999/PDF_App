"""Tests for the deterministic_passes field on the default Argument Map
analysis template and its backward-compatible backfill into saved templates.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.db.document_db import DocumentDB


def _make_db():
    return DocumentDB(SimpleNamespace(templates_path=None))


class TestDefaultArgumentMapDeterministicPasses(unittest.TestCase):
    def test_default_template_declares_quote_candidate_rank(self):
        db = _make_db()
        defaults = db._default_analysis_templates()
        argument_map = next(t for t in defaults if t["id"] == "default_argument_map")
        passes = argument_map["deterministic_passes"]
        self.assertEqual(len(passes), 1)
        self.assertEqual(passes[0]["pass_id"], "quote_candidate_rank")
        self.assertIn("top_n", passes[0])


class TestMergeBackfillsMissingKey(unittest.TestCase):
    def test_old_saved_template_without_key_gets_backfilled(self):
        db = _make_db()
        old_saved = {
            "id": "default_argument_map",
            "title": "Argument Map",
            "instructions": "custom instructions the user edited",
            "analysis_template_version": 12,
            "limits": {"chunk_pages": 4, "max_chunk_chars": 14000},
            # deliberately no "deterministic_passes" key — simulates a template
            # saved before this feature existed
        }
        merged = db._merge_default_analysis_templates([old_saved])
        result = next(t for t in merged if t["id"] == "default_argument_map")
        self.assertIn("deterministic_passes", result)
        self.assertEqual(result["deterministic_passes"][0]["pass_id"], "quote_candidate_rank")
        # user's customized instructions must survive the backfill untouched
        self.assertEqual(result["instructions"], "custom instructions the user edited")

    def test_explicit_empty_list_opt_out_is_preserved(self):
        db = _make_db()
        opted_out = {
            "id": "default_argument_map",
            "title": "Argument Map",
            "analysis_template_version": 12,
            "limits": {"chunk_pages": 4, "max_chunk_chars": 14000},
            "deterministic_passes": [],  # user explicitly disabled filtering
        }
        merged = db._merge_default_analysis_templates([opted_out])
        result = next(t for t in merged if t["id"] == "default_argument_map")
        self.assertEqual(result["deterministic_passes"], [])

    def test_custom_template_without_argument_map_id_is_untouched(self):
        db = _make_db()
        custom = {
            "id": "tmpl_my_custom_template",
            "title": "My Custom Mode",
            "analysis_template_version": 1,
            "limits": {"chunk_pages": 2, "max_chunk_chars": 8000},
        }
        merged = db._merge_default_analysis_templates([custom])
        result = next(t for t in merged if t["id"] == "tmpl_my_custom_template")
        # custom templates aren't forced to adopt deterministic_passes
        self.assertNotIn("deterministic_passes", result)


if __name__ == "__main__":
    unittest.main()
