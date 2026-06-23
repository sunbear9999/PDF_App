from __future__ import annotations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.plugins.papyrus_api import PapyrusAPI
    from core.plugins.extension_registry import PluginExtensionRegistry

from .law_manager import LocalLawManager


class Plugin:
    plugin_id = "locallaws"
    name = "Local Laws & Precedent Integration"
    version = "1.2.0"
    dependencies: list = []
    requires_internet: bool = True

    def __init__(self):
        self._api: Optional["PapyrusAPI"] = None
        self.law_manager: Optional[LocalLawManager] = None

    def on_load(self, api: "PapyrusAPI") -> None:
        self._api = api
        self.law_manager = LocalLawManager(api)

        # Register RAG sources (both municipal and case law route through the same query fn)
        api.register_rag_source("municipal_codes", self._query_law_source)
        api.register_rag_source("case_law", self._query_law_source)

        # Expose a single per-project toggle in the Global AI Settings filter dialog
        api.contribute_rag_source_filter(
            "laws",
            "⚖️  Laws & Precedent",
            "Search indexed municipal codes and case law in every RAG query",
        )

        # Contribute legal analysis templates to the Analysis Tab
        from .templates import get_legal_analysis_templates
        for template in get_legal_analysis_templates():
            api.contribute_analysis_template(template)

        # Register AI Query Assist prompts so they appear in Prompt Manager and are editable
        from .prompts import (
            LAW_QUERY_SYSTEM_KEY, LAW_QUERY_SYSTEM_CONTENT,
            LAW_QUERY_QUERY_KEY, LAW_QUERY_QUERY_CONTENT,
        )
        api.prompts.pm.register_prompt(LAW_QUERY_SYSTEM_KEY, LAW_QUERY_SYSTEM_CONTENT, "Laws & Precedent")
        api.prompts.pm.register_prompt(LAW_QUERY_QUERY_KEY, LAW_QUERY_QUERY_CONTENT, "Laws & Precedent")

        # Register the Legal Query Assist blueprint through the master runner architecture
        from .blueprints import build_legal_query_blueprint
        from core.registries.blueprint_registry import BlueprintDefinition
        api.blueprints.register(BlueprintDefinition(
            id="locallaws.query_assist",
            label="Legal Query Assist",
            description=(
                "Generates optimized boolean search parameters from a plain-text legal issue description. "
                "Auto-fills CourtListener and LOCUS search fields."
            ),
            factory=lambda **_kw: build_legal_query_blueprint(),
            plugin_id=self.plugin_id,
            agent_visible=False,
        ))

    def on_unload(self) -> None:
        pass

    def _query_law_source(
        self, *, embedding_vector, n_results, allowed_docs=None,
        tag_filters=None, tag_logic="AND",
    ):
        # Per-project enable/disable (toggled from Global AI Settings filter dialog)
        try:
            pm = self._api.project_manager
            if pm and getattr(pm, "project_filepath", None):
                enabled = pm.get_metadata("rag_source_enabled:locallaws:laws", "true")
                if enabled == "false":
                    return None
        except Exception:
            pass

        active_dbs = self._api.config.get("active_laws", [])
        if not active_dbs or not self.law_manager.is_available():
            return None
        return self.law_manager.query_laws(
            embedding_vector, n_results, active_dbs,
            tag_filters=tag_filters, tag_logic=tag_logic,
        )

    def register_gui_extensions(self, registry: "PluginExtensionRegistry") -> None:
        from core.plugins.extension_registry import CitationSourceHandlerSpec, DockSpec

        registry.add_dock(DockSpec(
            id="locallaws_dock",
            label="Laws & Precedent Directory",
            menu_name="⚖️ Laws & Precedent Directory",
            area="right",
            is_singleton=True,
            factory=self._make_laws_dock,
            plugin_id=self.plugin_id,
        ))

        registry.add_citation_source_handler(CitationSourceHandlerSpec(
            source_type="plugin.locallaws",
            callback=self._jump_to_law_citation,
            plugin_id=self.plugin_id,
        ))
        registry.add_citation_source_handler(CitationSourceHandlerSpec(
            source_type="plugin.caselaw",
            callback=self._jump_to_law_citation,
            plugin_id=self.plugin_id,
        ))

    def _make_laws_dock(self, app_context):
        from .gui.laws_dock import LocalLawsDock
        return LocalLawsDock(self._api, self.law_manager, app_context,
                             query_blueprint_id="locallaws.query_assist")

    def _jump_to_law_citation(self, citation: dict, app_context) -> None:
        dock_manager = getattr(app_context, "dock_manager", None)
        if not dock_manager:
            return
        dock = dock_manager.spawn("locallaws_dock")
        widget = dock.widget() if hasattr(dock, "widget") else None
        if widget and hasattr(widget, "open_citation"):
            widget.open_citation(citation)

    def on_register(self, api: "PapyrusAPI") -> None: pass
    def register_blueprints(self, registry) -> None: pass
    def register_workspace_tools(self, registry) -> None: pass

    def register_ontology_types(self, registry) -> None:
        from .ontology.nodes import ALL_NODE_TYPES
        from .ontology.edges import ALL_EDGE_TYPES
        for cls in ALL_NODE_TYPES:
            cls.plugin_id = self.plugin_id
            registry.register_node_plugin(cls)
        for cls in ALL_EDGE_TYPES:
            cls.plugin_id = self.plugin_id
            registry.register_edge_plugin(cls)

    def get_dock_spec(self) -> Optional[object]: return None
    def on_project_open(self, filepath: str) -> None:
        """Sync plugin-local law tags into the open project so they appear in the RAG filter."""
        try:
            all_tags = self.law_manager.tag_db.get_all_tags()
            if all_tags and hasattr(self._api, "tags"):
                self._api.tags.sync_tags(all_tags)
        except Exception:
            pass
    def on_project_close(self) -> None: pass
