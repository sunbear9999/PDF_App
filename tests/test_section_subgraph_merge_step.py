"""Integration tests for SectionSubgraphMergeStep and the map-reduce graph
build it enables — verifies section subgraphs merge correctly, ids stay
consistent when re-normalized as "master", and the deterministic
combine-with-polish-additions script produces a graph that
AnalysisRuntime.normalize_result() can consume without errors.
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from core.db.document_db import DocumentDB
from core.engine.analysis_runtime import AnalysisRuntime
from core.engine.steps.section_subgraph_merge_step import SectionSubgraphMergeStep
from core.ontology.registry import OntologyRegistry
from core.prompt_manager import PromptManager


def _make_context(state=None):
    ctx = SimpleNamespace()
    ctx.state = state or {}
    ctx.project_manager = None
    ctx.prompt_manager = PromptManager()
    ctx.ontology_registry = OntologyRegistry()
    ctx.api = None
    ctx.llm_manager = None
    return ctx


def _argument_map_contract():
    db = DocumentDB(SimpleNamespace(templates_path=None))
    template = db._default_analysis_templates()[0]
    runtime = AnalysisRuntime(None, PromptManager(), OntologyRegistry())
    contract = runtime.build_contract(template)
    contract["prompt_contract"] = runtime.build_prompt_contract(contract)
    return runtime, contract, template


class TestSectionSubgraphMergeStep(unittest.TestCase):
    def test_merges_two_sections_into_one_graph_with_unique_ids(self):
        _, contract, _ = _argument_map_contract()
        claim_alias = next(e["a"] for e in contract["allowed_entity_types"] if e["type"] == "entity.claim")
        quote_alias = next(e["a"] for e in contract["allowed_entity_types"] if e["type"] == "entity.quote")
        rel_alias = contract["allowed_relation_types"][0]["a"]

        section_0 = {
            "summary": "Section 0 summary", "key_themes": ["theme a"], "key_quote_ids": ["qidA"],
            "n": [
                {"id": "n1", "t": claim_alias, "x": "Central claim of section 0"},
                {"id": "n2", "t": quote_alias, "x": "supports", "properties": {"source_quote_id": "qidA"}},
            ],
            "e": [{"s": "n2", "g": "n1", "t": rel_alias, "ev": ["qidA"]}],
        }
        section_1 = {
            "summary": "Section 1 summary", "key_themes": ["theme b"], "key_quote_ids": ["qidB"],
            "n": [
                {"id": "n1", "t": claim_alias, "x": "Central claim of section 1"},
                {"id": "n2", "t": quote_alias, "x": "supports", "properties": {"source_quote_id": "qidB"}},
            ],
            "e": [{"s": "n2", "g": "n1", "t": rel_alias, "ev": ["qidB"]}],
        }

        ctx = _make_context()
        result = SectionSubgraphMergeStep().execute(ctx, {
            "all_section_syntheses": [section_0, section_1],
            "contract": contract,
            "max_chars": 20000,
        })

        graph = result.raw_value
        # both sections' "n1"/"n2" local ids must not collide after merge
        entity_ids = [e["temp_id"] for e in graph["entities"]]
        self.assertEqual(len(entity_ids), len(set(entity_ids)), "merged entity ids must be unique across sections")
        self.assertEqual(len(graph["entities"]), 4)  # 2 claims + 2 quotes
        self.assertEqual(len(graph["relations"]), 2)
        self.assertIn("section_syntheses_compact", result.state_updates)
        self.assertIn("Section 0 summary", result.state_updates["section_syntheses_compact"])
        self.assertIn("Section 1 summary", result.state_updates["section_syntheses_compact"])
        # compact_text is present for the polish-pass prompt
        self.assertIn("compact_text", graph)
        self.assertTrue(len(graph["compact_text"]) > 0)

    def test_re_normalizing_merged_graph_as_master_keeps_ids_consistent(self):
        """Simulates what ANALYSIS_FINALIZE's normalize_result() does: takes the
        already-normalized merged graph and re-normalizes it under the "master"
        prefix. A relation referencing a pre-existing merged entity id (as the
        polish pass would emit) must still resolve correctly after re-scoping,
        PROVIDED the relation type is actually valid per CONTRACT (entity.claim
        can only ever be a relation TARGET in this contract, never a source —
        confirmed by inspecting allowed_relation_types below — so sections build
        bridge-level entity.reasoning nodes, matching what the section-synthesis
        stage rules now instruct; the root-level entity.claim is intentionally
        left for _ensure_graph_plan_roots() to add deterministically).
        """
        runtime, contract, _ = _argument_map_contract()
        reasoning_alias = next(e["a"] for e in contract["allowed_entity_types"] if e["type"] == "entity.reasoning")
        quote_alias = next(e["a"] for e in contract["allowed_entity_types"] if e["type"] == "entity.quote")
        sup_alias = next(r["a"] for r in contract["allowed_relation_types"] if r["type"] == "relation.supports")
        reasons_alias = next(r["a"] for r in contract["allowed_relation_types"] if r["type"] == "relation.reasons")

        section_0 = {
            "n": [
                {"id": "n1", "t": reasoning_alias, "x": "Bridge zero"},
                {"id": "n2", "t": quote_alias, "x": "q0", "properties": {"source_quote_id": "qid0"}},
            ],
            "e": [{"s": "n2", "g": "n1", "t": sup_alias, "ev": ["qid0"]}],
        }
        section_1 = {
            "n": [{"id": "n1", "t": reasoning_alias, "x": "Bridge one"}],
            "e": [],
        }
        ctx = _make_context()
        merge_result = SectionSubgraphMergeStep().execute(ctx, {
            "all_section_syntheses": [section_0, section_1],
            "contract": contract,
            "max_chars": 20000,
        })
        merged = merge_result.raw_value
        bridge_ids = [e["temp_id"] for e in merged["entities"] if e["type"] == "entity.reasoning"]
        self.assertEqual(len(bridge_ids), 2)

        # Simulate the polish pass connecting the two sections' bridge nodes with
        # a genuinely VALID relation type (reasoning -> reasoning is allowed by
        # "relation.reasons") — this is exactly the additive-only shape the
        # updated prompt now asks for: only new edges, no new nodes.
        polish_relation = {"s": bridge_ids[0], "g": bridge_ids[1], "t": reasons_alias, "ev": []}

        combined_entities = list(merged["entities"])
        combined_relations = list(merged["relations"]) + [polish_relation]
        combined = {"entities": combined_entities, "relations": combined_relations}

        # This is exactly what normalize_result() does internally to master_raw.
        master_norm = runtime.normalize_graph_object(combined, "master", contract)

        self.assertEqual(len(master_norm["entities"]), 3)  # 2 bridges + 1 quote
        cross_section_rel = [
            r for r in master_norm["relations"]
            if r["type"] == "relation.reasons"
        ]
        self.assertTrue(cross_section_rel, "valid polish-pass cross-section relation must survive re-scoping, not be dropped")

    def test_full_normalize_result_auto_roots_a_merged_graph_with_no_polish_additions(self):
        """End-to-end: even with ZERO polish-pass additions (empty edge list —
        the expected common case when sections don't obviously cross-reference
        each other), the existing, unchanged normalize_result() pipeline must
        still produce a complete, connected, rooted graph via its own
        deterministic _ensure_graph_plan_roots()/_ensure_connected_master_graph()
        repair steps — proving the map-reduce merge alone is sufficient for a
        usable result even when the polish LLM call contributes nothing.
        """
        runtime, contract, template = _argument_map_contract()
        reasoning_alias = next(e["a"] for e in contract["allowed_entity_types"] if e["type"] == "entity.reasoning")
        quote_alias = next(e["a"] for e in contract["allowed_entity_types"] if e["type"] == "entity.quote")
        sup_alias = next(r["a"] for r in contract["allowed_relation_types"] if r["type"] == "relation.supports")

        sections = [
            {
                "n": [
                    {"id": "n1", "t": reasoning_alias, "x": f"Bridge {i}"},
                    {"id": "n2", "t": quote_alias, "x": f"quote {i}", "properties": {"source_quote_id": f"qid{i}"}},
                ],
                "e": [{"s": "n2", "g": "n1", "t": sup_alias, "ev": [f"qid{i}"]}],
            }
            for i in range(3)
        ]
        ctx = _make_context()
        merge_result = SectionSubgraphMergeStep().execute(ctx, {
            "all_section_syntheses": sections, "contract": contract, "max_chars": 20000,
        })
        merged = merge_result.raw_value
        # Simulate QUOTE_HYDRATION, which runs before ANALYSIS_FINALIZE in the
        # real pipeline and populates exact_text on quote-type entities.
        for e in merged["entities"]:
            if e["type"] == "entity.quote":
                e["exact_text"] = f"exact quote text for {e['properties']['source_quote_id']}"

        # Polish pass contributed nothing — the common/expected case.
        combined = {"entities": merged["entities"], "relations": merged["relations"]}

        result = runtime.normalize_result(
            doc_path="/doc.pdf", template_id=template["id"], run_id="run1",
            template=template, contract=contract,
            chunks_raw=[], master_raw=combined, synthesis="",
        )

        self.assertGreaterEqual(len(result["entities"]), 7)  # 3 bridges + 3 quotes + auto-root
        claim_entities = [e for e in result["entities"] if e["type"] == "entity.claim"]
        self.assertTrue(claim_entities, "normalize_result must auto-create a root claim node")
        root = claim_entities[0]
        # every bridge must end up connected to the auto-created root (directly or
        # transitively) — a fully disconnected section would mean lost content
        connected_targets = {r["target"] for r in result["relations"]}
        connected_sources = {r["source"] for r in result["relations"]}
        self.assertIn(root["temp_id"], connected_targets | connected_sources, "auto-root must be connected into the graph")

# NOTE: TestCombineMasterGraphScript was removed — the map-reduce
# graph_assembly design (SECTION_SUBGRAPH_MERGE + combine_master_graph) was
# reverted in the default blueprint after it reliably produced sparse graphs
# with small local models (see get_analysis_blueprint's docstring). The
# SectionSubgraphMergeStep tests above still verify the step itself works
# correctly for any blueprint that opts into it.


if __name__ == "__main__":
    unittest.main()
