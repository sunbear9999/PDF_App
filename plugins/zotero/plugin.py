"""
plugins/zotero/plugin.py

Zotero Integration Plugin for Papyrus.

Provides:
  - A Zotero Library tab inside the Research Assistant dock
  - Automatic citation provider: Zotero items appear alongside PDF
    entries in the main Citation Dock
  - A "Sync Zotero" toolbar button on the Citation Dock that opens a
    smart matching dialog to link Zotero metadata to project PDFs
  - PDF auto-linking: when a Zotero attachment matches an open project
    PDF, metadata is automatically enriched

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
        self._provider = None
        self._api = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, api: "PapyrusAPI") -> None:
        from .zotero_db import ZoteroDB
        from .zotero_formatter import ZoteroFormatter
        from .zotero_provider import ZoteroProvider

        self._api = api
        self._db = ZoteroDB()
        self._formatter = ZoteroFormatter()
        self._provider = ZoteroProvider(self._db, self._formatter)

        if not self._db.is_available():
            print(
                "[Zotero] No Zotero library found — plugin running in passive mode. "
                "Set ZOTERO_DB_PATH env var or install Zotero."
            )

        # Register as a citation provider so Zotero items appear in the
        # Citation Dock table alongside PDF-extracted entries.
        api.citation_service.register_provider("zotero", self._provider)

        # Refresh provider cache on project open/close
        api.on_project_open(self._provider.on_project_open)
        api.on_project_close(self._provider.on_project_close)

        # Auto-link: when any document opens, check if Zotero has matching metadata
        api.subscribe("document_added", self._on_document_added)

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

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_document_added(self, event, payload) -> None:
        if not self._db or not self._db.is_available():
            return
        path = getattr(payload, "path", None) or getattr(payload, "source_id", None)
        if not path:
            return
        try:
            zotero_item = self._db.find_matching_pdf(path)
            if zotero_item and self._provider:
                cit = self._formatter.to_citation_dict(zotero_item)
                self._provider._cache[cit["doc_id"]] = cit
                print(f"[Zotero] Auto-linked '{path}' → Zotero item: {zotero_item.get('title', '')}")
        except Exception as exc:
            print(f"[Zotero] Auto-link error: {exc}")

    def _open_sync_dialog(self) -> None:
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
            parent=parent,
        )
        dialog.exec()

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
