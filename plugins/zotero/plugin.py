"""
plugins/zotero/plugin.py

Zotero Integration Plugin for Papyrus.

Provides:
  - A Zotero Library tab inside the Research Assistant dock
  - A "Sync Zotero" toolbar button on the Citation Dock that opens a
    smart matching dialog to link Zotero metadata to project PDFs
  - Project-scoped right-click actions for Document Explorer and Citation Dock

No code in the main application references Zotero by name.
Remove this directory entirely to disable the plugin with zero side-effects.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.plugins.papyrus_api import PapyrusAPI
    from core.registries import BlueprintRegistry, WorkspaceAIToolRegistry, OntologyRegistry
    from core.plugins.base_plugins import PluginDockSpec
    from core.plugins.extension_registry import PluginExtensionRegistry


class Plugin:
    plugin_id = "zotero"
    name = "Zotero Integration"
    version = "1.0.0"
    dependencies: list = []
    require_internet: bool = False
    def __init__(self):
        self._db = None
        self._formatter = None
        self._outbound_adapter = None
        self._api = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, api: "PapyrusAPI") -> None:
        from .zotero_db import ZoteroDB
        from .zotero_formatter import ZoteroFormatter
        from .zotero_sync_adapter import ZoteroLocalReadOnlySyncAdapter

        self._api = api
        self._db = ZoteroDB()
        self._formatter = ZoteroFormatter()
        self._outbound_adapter = ZoteroLocalReadOnlySyncAdapter()

        if not self._db.is_available():
            print(
                "[Zotero] No Zotero library found — plugin running in passive mode. "
                "Set ZOTERO_DB_PATH env var or install Zotero."
            )

        api.register_service("zotero.outbound_sync", self._outbound_adapter)

    def on_register(self, api: "PapyrusAPI") -> None:
        pass

    def on_unload(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Registration hooks
    # ------------------------------------------------------------------

    def register_blueprints(self, registry: "BlueprintRegistry") -> None:
        pass

    def register_workspace_tools(self, registry: "WorkspaceAIToolRegistry") -> None:
        pass

    def register_ontology_types(self, registry: "OntologyRegistry") -> None:
        pass

    def get_dock_spec(self) -> "Optional[PluginDockSpec]":
        # No standalone dock — Zotero lives in the Research Assistant tab
        return None

    def register_gui_extensions(self, registry: "PluginExtensionRegistry") -> None:
        from core.plugins.extension_registry import ResearchTabSpec, ToolbarButtonSpec
        from gui.registry.action_spec import ActionSpec

        db = self._db
        formatter = self._formatter

        # Zotero search tab in the Research Assistant panel
        registry.add_research_tab(ResearchTabSpec(
            tab_id="zotero_search",
            label="📚 Zotero",
            factory=lambda ctx: _make_research_tab(ctx, db, formatter),
        ))

        # "Sync Zotero" button injected into the Citation Dock toolbar
        registry.add_toolbar_button(ToolbarButtonSpec(
            dock_target="citations",
            button_id="zotero_sync_citations",
            label="⚡ Sync Zotero",
            tooltip="Link Zotero library entries to project PDFs",
            callback=self._open_sync_dialog,
        ))

        registry.add_action(ActionSpec(
            action_id="zotero.sync_document_list",
            label="Sync with Zotero",
            tooltip="Match selected project PDFs to Zotero metadata",
            callback=self._open_sync_dialog_from_context,
            mounts=["context_menu:document_list:item"],
            priority=70,
            plugin_id=self.plugin_id,
        ))
        registry.add_action(ActionSpec(
            action_id="zotero.sync_citation_row",
            label="Sync with Zotero",
            tooltip="Match selected citation rows to Zotero metadata",
            callback=self._open_sync_dialog_from_context,
            mounts=["context_menu:citation:item"],
            priority=70,
            plugin_id=self.plugin_id,
        ))
        registry.add_action(ActionSpec(
            action_id="zotero.copy_citation",
            label="Copy Zotero Citation",
            tooltip="Copy the selected Zotero-enriched citation",
            callback=self._copy_zotero_citation,
            mounts=["context_menu:citation:item"],
            priority=80,
            plugin_id=self.plugin_id,
        ))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _open_sync_dialog(self, paths=None) -> None:
        """Open the Zotero sync/matching dialog from the citation dock toolbar button."""
        from PySide6.QtWidgets import QApplication
        from .gui.sync_dialog import ZoteroSyncDialog

        parent = QApplication.activeWindow()
        project_manager = None
        app_context = getattr(parent, "app_context", None)
        if app_context:
            project_manager = getattr(app_context, "project_manager", None)

        dialog = ZoteroSyncDialog(
            db=self._db,
            formatter=self._formatter,
            project_manager=project_manager,
            initial_pdf_paths=paths,
            outbound_adapter=self._outbound_adapter,
            parent=parent,
        )
        dialog.exec()

    def _open_sync_dialog_from_context(self, ctx) -> None:
        paths = []
        payload = getattr(ctx, "payload", {}) or {}
        paths.extend(payload.get("paths") or [])
        paths.extend(payload.get("doc_ids") or [])
        paths.extend(getattr(ctx, "selected_ids", []) or [])
        project_paths = set(getattr(self._api.project_manager, "pdfs", []) or []) if self._api else set()
        filtered = []
        for path in paths:
            if path and path in project_paths and path not in filtered:
                filtered.append(path)
        self._open_sync_dialog(filtered or None)

    def _copy_zotero_citation(self, ctx) -> None:
        from PySide6.QtWidgets import QApplication

        citation = (getattr(ctx, "payload", {}) or {}).get("citation") or {}
        if citation.get("source_provider") != "zotero" and citation.get("source") != "zotero":
            if self._api:
                self._api.notify("This citation is not linked to Zotero metadata.", level="warning")
            return
        try:
            text = self._api.citation_manager.format_entry(citation)
        except Exception:
            text = (
                f"{citation.get('authors', '')} ({citation.get('year', '')}). "
                f"{citation.get('title', '')}."
            )
        QApplication.clipboard().setText(text)
        if self._api:
            self._api.notify("Zotero citation copied.", level="success", duration=2000)

    # Backward-compat stubs
    def on_project_open(self, filepath: str) -> None:
        pass

    def on_project_close(self) -> None:
        pass


# ------------------------------------------------------------------
# Factory helpers
# ------------------------------------------------------------------

def _make_research_tab(ctx, db, formatter):
    from .gui.research_tab import ZoteroResearchTab
    return ZoteroResearchTab(ctx, db, formatter)
