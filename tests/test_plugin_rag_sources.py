import json
from types import SimpleNamespace

from core.engine.steps.rag_search_step import RagSearchStep
from core.engine.steps.llm_query_step import LLMQueryStep
from core.llm_manager import LocalLLMManager
from core.plugins.extension_registry import PluginExtensionRegistry
from core.plugins.plugin_step_protocol import StepContext
from plugins.locallaws.plugin import Plugin as LocalLawsPlugin
from plugins.locallaws.law_manager import LocalLawManager
import pandas as pd


def _results(prefix, distances):
    return {
        "ids": [[f"{prefix}-{i}" for i in range(len(distances))]],
        "documents": [[f"{prefix} evidence {i}" for i in range(len(distances))]],
        "metadatas": [[
            {"doc_name": f"{prefix}.pdf", "page": i, "source_type": prefix}
            for i in range(len(distances))
        ]],
        "distances": [distances],
    }


def test_registered_rag_sources_merge_with_project_results_by_relevance():
    manager = LocalLLMManager.__new__(LocalLLMManager)
    manager.collection = SimpleNamespace(
        count=lambda: 1,
        query=lambda **_kwargs: _results("project", [0.4, 0.8]),
    )
    manager._rag_sources = {}
    manager._query_interceptors = []
    manager.register_rag_source(
        "test:external",
        lambda **_kwargs: _results("external", [0.1, 0.6]),
    )

    results = manager.query_by_raw_embedding([0.25], n_results=3)

    assert results["ids"][0] == ["external-0", "project-0", "external-1"]


def test_rag_step_uses_external_sources_without_a_project_collection():
    class ExternalOnlyManager:
        ai_enabled = True
        collection = None

        def has_rag_sources(self):
            return True

        def get_embedding(self, _query):
            return [0.25]

        def query_by_raw_embedding(self, _embedding, **_kwargs):
            return _results("municipal-law", [0.1])

    result = RagSearchStep().execute(
        StepContext(llm_manager=ExternalOnlyManager()),
        {"queries": ["parking restrictions"], "n_results": 5},
    )

    assert "municipal-law evidence 0" in result.raw_value
    assert "DOCUMENT: municipal-law.pdf" in result.raw_value
    assert "CITATION_SOURCE_TYPE: municipal-law" in result.raw_value


def test_locallaws_dock_is_an_other_tools_contribution_not_a_toolbar_button():
    registry = PluginExtensionRegistry()
    plugin = LocalLawsPlugin()

    plugin.register_gui_extensions(registry)

    assert [dock.id for dock in registry.get_extra_docks()] == ["locallaws_dock"]
    assert registry.get_extra_docks()[0].menu_name == "⚖️ Local Laws Directory"
    assert registry.get_main_toolbar_buttons() == []


def test_locallaws_source_queries_the_jurisdictions_selected_in_settings():
    plugin = LocalLawsPlugin()
    plugin._api = SimpleNamespace(
        config=SimpleNamespace(get=lambda key, default: ["Denver_CO"]),
    )
    calls = []
    plugin.law_manager = SimpleNamespace(
        is_available=lambda: True,
        query_laws=lambda embedding, count, active: calls.append(
            (embedding, count, active)
        ) or _results("law", [0.2]),
    )

    result = plugin._query_law_source(
        embedding_vector=[0.25], n_results=4, tag_logic="OR",
    )

    assert calls == [([0.25], 4, ["Denver_CO"])]
    assert result["ids"][0] == ["law-0"]


def test_locallaws_normalizes_header_content_schema_without_na_labels():
    row = pd.Series({
        "header": "### 3-2-6: SALES TO MINORS:",
        "content": "A licensee shall not sell to a minor.",
    })

    record = LocalLawManager.row_to_record(row)

    assert record["label"] == "3-2-6: SALES TO MINORS:"
    assert record["section"] == "3-2-6"
    assert record["text"] == "A licensee shall not sell to a minor."


def test_citation_metadata_is_restored_from_the_exact_rag_block():
    context = """--- DOCUMENT: Cody Municipal Code — 3-2-6 | PAGE 1 ---
CITATION_DOC_NAME: Cody Municipal Code — 3-2-6
CITATION_SOURCE_TYPE: plugin.locallaws
CITATION_SOURCE_ID: Cody_WY
CITATION_PAGE: 0
CITATION_START: 0
CITATION_END: 0
CITATION_LOCATOR_jurisdiction_id: Cody_WY
CITATION_LOCATOR_section: 3-2-6
A licensee shall not sell alcohol to a minor."""
    raw = '{"citations": [{"doc_name": "wrong", "quote": "shall not sell alcohol to a minor", "note": "support"}]}'

    restored = json.loads(LLMQueryStep._restore_citation_metadata(raw, context))
    citation = restored["citations"][0]

    assert citation["source_type"] == "plugin.locallaws"
    assert citation["source_id"] == "Cody_WY"
    assert citation["source_locator"]["section"] == "3-2-6"
