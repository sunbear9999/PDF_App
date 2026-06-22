import json
from types import SimpleNamespace

import fitz

from core.engine.steps.source_statistics_step import SourceStatisticsStep
from core.engine.steps.ontology_catalog_step import OntologyCatalogStep
from core.engine.default_blueprints import DefaultBlueprints
from core.engine.action_model import AIActionBlueprint
from core.engine.master_runner import MasterActionRunner
from core.events.event_bus import EventBus
from core.events.domains.workspace_events import WorkspaceIntent
from core.ontology.registry import OntologyRegistry
from core.plugins.plugin_step_protocol import StepContext
from core.registries.workflow_registry import build_default_blueprint_node_type_registry


class _Collection:
    def get(self, **_kwargs):
        return {"ids": ["chunk-1", "chunk-2", "chunk-3"]}


def test_source_statistics_collects_pages_and_is_registry_available(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    document = fitz.open()
    for _ in range(3):
        document.new_page()
    document.save(pdf_path)
    document.close()

    pm = SimpleNamespace(list_sources=lambda: [{
        "id": "source-1", "path": str(pdf_path), "source_type": "pdf", "metadata": {},
    }])
    lm = SimpleNamespace(collection=_Collection())
    result = SourceStatisticsStep().execute(
        StepContext(project_manager=pm, llm_manager=lm),
        {"allowed_docs": ["paper.pdf"], "metrics": ["page_count", "indexed_chunk_count"]},
    )
    stats = json.loads(result.raw_value)

    assert stats["source_count"] == 1
    assert stats["totals"]["page_count"] == 3
    assert stats["sources"][0]["indexed_chunk_count"] == 3
    registered = build_default_blueprint_node_type_registry().get_by_step_type("SOURCE_STATISTICS")
    assert registered.step_cls is SourceStatisticsStep


def test_source_statistics_supports_pluggable_metrics():
    SourceStatisticsStep.register_metric("research_weight", lambda source, _ctx: source["weight"])
    pm = SimpleNamespace(list_sources=lambda: [{
        "id": "s", "path": "/missing/doc.pdf", "source_type": "pdf",
        "metadata": {}, "weight": 7,
    }])

    result = SourceStatisticsStep().execute(
        StepContext(project_manager=pm),
        {"metrics": ["research_weight"]},
    )
    stats = json.loads(result.raw_value)

    assert stats["sources"][0]["research_weight"] == 7
    assert stats["totals"]["research_weight"] == 7


def test_deep_research_executes_each_planned_search_and_reuses_all_context():
    class _AdaptiveCollection:
        def __init__(self):
            self.search_count = 0

        def query(self, **_kwargs):
            self.search_count += 1
            n = self.search_count
            return {
                "ids": [[f"chunk-{n}"]],
                "documents": [[f"evidence from search {n}"]],
                "metadatas": [[{"doc_name": "paper.pdf", "page": n - 1}]],
                "distances": [[0.1]],
            }

        def get(self, **_kwargs):
            return {"ids": ["chunk-1", "chunk-2", "chunk-3", "chunk-4"]}

    class _LLM:
        ai_enabled = True

        def __init__(self):
            self.collection = _AdaptiveCollection()
            self.questions = []

        def get_embedding(self, _query):
            return [0.1]

        def query(self, question, callback=None, **_kwargs):
            self.questions.append(question)
            if "Decide what additional evidence" in question:
                return json.dumps({"searches": [
                    {"topic": "mechanism", "query": "mechanism query", "reason": "gap one"},
                    {"topic": "limitations", "query": "limitations query", "reason": "gap two"},
                    {"topic": "outcomes", "query": "outcomes query", "reason": "gap three"},
                ]})
            if "List every independently sourced factual claim" in question:
                return json.dumps({"claims": [
                    {"claim": "claim one", "evidence_need": "support one"},
                    {"claim": "claim two", "evidence_need": "support two"},
                ]})
            if "Find every distinct passage" in question:
                return json.dumps({"citations": [{
                    "doc_name": "paper.pdf", "quote": question.split("TARGET CLAIM:\n", 1)[1].split("\n", 1)[0],
                    "note": "supports claim",
                }]})
            answer = "complete synthesized answer"
            if callback:
                callback(answer)
            return answer

    class _Prompts:
        def get_prompt(self, key):
            prompts = {
                "General Assistant": "Answer carefully.",
                "Context Inject - Manifest": "",
                "JSON Schema Enforcer": "Return JSON matching: {schema_str}",
                "Format Enforcer - Chat Widgets": "Return exact citations.",
            }
            return prompts.get(key, "")

    pm = SimpleNamespace(
        list_sources=lambda: [{
            "id": "source-1", "path": "/missing/paper.pdf", "source_type": "pdf",
            "metadata": {"page_count": 42},
        }]
    )
    lm = _LLM()
    runner = MasterActionRunner(
        DefaultBlueprints.get_universal_chat_blueprint(None),
        {
            "user_query": "Explain the issue fully",
            "chat_history": "",
            "chat_persona": "General Assistant",
            "use_advanced_rag": True,
            "selected_model": "test-model",
            "active_rag_docs": ["paper.pdf"],
            "active_rag_tags": [],
            "active_rag_tag_logic": "OR",
            "allow_manifest_updates": False,
            "output_workspace": False,
            "project_manifest": "{}",
        },
        llm_manager=lm,
        prompt_manager=_Prompts(),
        project_manager=pm,
        node_type_registry=build_default_blueprint_node_type_registry(),
    )

    runner.run()

    assert lm.collection.search_count == 4  # initial search + three planned searches
    assert "ontology_catalog" not in runner.state
    assert all(f"evidence from search {n}" in runner.state["rag_context"] for n in range(1, 5))
    citation_questions = [q for q in lm.questions if "Find every distinct passage" in q]
    assert len(citation_questions) == 2
    assert all("complete synthesized answer" in q for q in citation_questions)
    assert all(all(f"evidence from search {n}" in q for n in range(1, 5)) for q in citation_questions)
    rendered = json.loads(runner.state["citations"])
    assert len(rendered) == 2


def test_typed_workspace_graph_uses_registry_catalog_and_import_contract():
    ontology = OntologyRegistry()

    class _GraphLLM:
        def query(self, callback=None, **_kwargs):
            result = json.dumps({
                "entities": [
                    {"id": "q1", "type": "entity.question", "text": "What evidence is missing?"},
                    {"id": "c1", "type": "entity.concept", "text": "Evidence collection"},
                ],
                "relations": [
                    {"id": "r1", "type": "relation.basic", "source": "c1", "target": "q1", "label": "frames"},
                ],
            })
            if callback:
                callback(result)
            return result

    class _Prompts:
        def get_prompt(self, key):
            return {
                "General Assistant": "Build a typed graph.",
                "Context Inject - Workspace": "",
                "JSON Schema Enforcer": "Return JSON matching {schema_str}",
            }.get(key, "")

    captured = []
    bus = EventBus.get_instance()
    slot = lambda intent, payload: captured.append((intent, payload))
    bus.workspace_action_requested.connect(slot)
    try:
        runner = MasterActionRunner(
            AIActionBlueprint(
                name="Typed graph",
                description="",
                steps=DefaultBlueprints.get_auto_build_graph_steps("final_answer"),
            ),
            {
                "final_answer": "Organize this plan into questions and concepts.",
                "workspace_data": "{}",
                "selected_model": "test-model",
            },
            llm_manager=_GraphLLM(),
            prompt_manager=_Prompts(),
            ontology_registry=ontology,
            node_type_registry=build_default_blueprint_node_type_registry(),
        )
        runner.run()
    finally:
        bus.workspace_action_requested.disconnect(slot)

    assert captured
    intent, payload = captured[-1]
    assert intent == WorkspaceIntent.IMPORT_GRAPH
    graph = payload.extra["graph"]
    catalog = json.loads(runner.state["ontology_catalog"])
    assert any(item["type"] == "entity.question" for item in catalog["entity_types"])
    assert {entity["type"] for entity in graph["entities"]} == {"entity.question", "entity.concept"}
    assert graph["relations"][0]["type"] == "relation.basic"


def test_ontology_catalog_is_a_reusable_registered_step():
    registry = OntologyRegistry()
    result = OntologyCatalogStep().execute(
        StepContext(ontology_registry=registry),
        {"entity_types": ["entity.question", "entity.concept"], "include_descriptions": False},
    )
    catalog = json.loads(result.raw_value)

    assert {item["type"] for item in catalog["entity_types"]} == {
        "entity.question", "entity.concept"
    }
    assert all("description" not in item for item in catalog["entity_types"])
    registered = build_default_blueprint_node_type_registry().get_by_step_type("ONTOLOGY_CATALOG")
    assert registered.step_cls is OntologyCatalogStep
