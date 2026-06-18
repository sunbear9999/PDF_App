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

from PySide6.QtCore import QThread, Signal
from gui.managers.dialog_manager import exec_as_modal, get_for_widget

if TYPE_CHECKING:
    from core.plugins.papyrus_api import PapyrusAPI
    from core.registries import BlueprintRegistry, WorkspaceAIToolRegistry, OntologyRegistry
    from core.plugins.base_plugins import PluginDockSpec
    from core.plugins.extension_registry import PluginExtensionRegistry


class _AutoAddWorker(QThread):
    finished_sync = Signal(object, object, object, object)

    def __init__(self, adapter, path: str, citation: dict, collection_name: str):
        super().__init__()
        self._adapter = adapter
        self._path = path
        self._citation = citation
        self._collection_name = collection_name

    def run(self):
        result = None
        error = None
        try:
            result = self._adapter.sync_pdfs(
                [self._path],
                {self._path: self._citation},
                collection_name=self._collection_name,
            )
        except Exception as exc:
            error = exc
        self.finished_sync.emit(self._path, self._citation, result, error)


class Plugin:
    plugin_id = "zotero"
    name = "Zotero Integration"
    version = "1.0.0"
    dependencies: list = []
    requires_internet: bool = False

    def __init__(self):
        self._db = None
        self._formatter = None
        self._outbound_adapter = None
        self._api = None
        self._warned_auto_sync_unavailable = False
        self._auto_add_workers = []
        self._library_cache = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_load(self, api: "PapyrusAPI") -> None:
        from .zotero_db import ZoteroDB
        from .zotero_formatter import ZoteroFormatter
        from .zotero_library_cache import ZoteroLibraryCache
        from .zotero_sync_adapter import ZoteroOutboundSyncController, ZoteroReadOnlyAdapter

        self._api = api
        self._db = ZoteroDB()
        self._formatter = ZoteroFormatter()
        self._library_cache = ZoteroLibraryCache()
        self._outbound_adapter = ZoteroOutboundSyncController(ZoteroReadOnlyAdapter())

        if not self._db.is_available():
            print(
                "[Zotero] No Zotero library found — plugin running in passive mode. "
                "Set ZOTERO_DB_PATH env var or install Zotero."
            )

        api.register_service("zotero.outbound_sync", self._outbound_adapter)
        api.subscribe("document_added", self._on_document_added)

        # Probe the Zotero connection in the background so the adapter state
        # is ready before the user opens the sync dialog.
        self._refresh_outbound_adapter_async()

    def on_register(self, api: "PapyrusAPI") -> None:
        pass

    def on_unload(self) -> None:
        for worker in list(self._auto_add_workers):
            if worker.isRunning():
                worker.requestInterruption()
                worker.wait(1500)
        self._auto_add_workers.clear()

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
        from core.plugins.extension_registry import MenuItemSpec, ResearchTabSpec, ToolbarButtonSpec
        from gui.registry.action_spec import ActionSpec

        db = self._db
        formatter = self._formatter

        # Zotero search tab in the Research Assistant panel
        registry.add_research_tab(ResearchTabSpec(
            tab_id="zotero_search",
            label="📚 Zotero",
            factory=lambda ctx: _make_research_tab(
                ctx,
                db,
                formatter,
                self._api.config if self._api else None,
                self._library_cache,
            ),
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
        registry.add_menu_item(MenuItemSpec(
            item_id="zotero_settings",
            menu_name="Plugins",
            label="Zotero Settings",
            callback=self._open_settings_dialog,
            position=20,
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

        # Use the cached adapter — no synchronous network probe on the main thread.
        dialog = ZoteroSyncDialog(
            db=self._db,
            formatter=self._formatter,
            project_manager=project_manager,
            initial_pdf_paths=paths,
            outbound_adapter=self._outbound_adapter,
            outbound_enabled=self._auto_add_enabled(),
            outbound_collection_name=self._target_collection_name(project_manager),
            config=self._api.config if self._api else None,
            library_cache=self._library_cache,
            parent=parent,
        )
        self._apply_dialog_theme(dialog, parent)
        dm = get_for_widget(parent)
        if dm:
            dm.show_instance(dialog)
        else:
            exec_as_modal(dialog)

    def _open_settings_dialog(self) -> None:
        from PySide6.QtWidgets import QApplication
        from .gui.settings_dialog import ZoteroSettingsDialog

        parent = QApplication.activeWindow()
        dialog = ZoteroSettingsDialog(self._api, self._db, parent=parent)
        self._apply_dialog_theme(dialog, parent)
        dm = get_for_widget(parent)
        if dm:
            dialog.finished.connect(lambda _: self._refresh_outbound_adapter_async())
            dm.show_instance(dialog)
        else:
            exec_as_modal(dialog)
            self._refresh_outbound_adapter_async()

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

    def _on_document_added(self, event, payload) -> None:
        # Always let the user know the Zotero sync requirement, regardless of
        # whether auto-add is enabled — because the Zotero library tab also
        # needs Zotero running to show up-to-date items.
        if self._api and not getattr(self, "_shown_sync_hint", False):
            self._shown_sync_hint = True
            self._api.notify(
                "PDF added to project. To keep your Zotero library in sync, "
                "make sure Zotero is open (or running in the background with "
                "sync enabled) so the local library stays up to date.",
                level="info",
                duration=7000,
            )
        try:
            self._sync_added_document_to_zotero(payload)
        except Exception as exc:
            if self._api:
                self._api.notify(
                    f"Zotero auto-add did not finish, but the PDF stayed in the project: {exc}",
                    level="warning",
                    duration=7000,
                )

    def _sync_added_document_to_zotero(self, payload) -> None:
        if not self._auto_add_enabled():
            return
        path = getattr(payload, "path", "") or ""
        if not path:
            return
        # Use the cached adapter — never probe the network on the main thread.
        adapter = self._outbound_adapter
        if not adapter or not adapter.can_write():
            if not self._warned_auto_sync_unavailable and self._api:
                self._warned_auto_sync_unavailable = True
                self._api.notify(
                    "Zotero auto-add is enabled, but PyZotero write settings are not ready. "
                    "Open Zotero Settings to enable local communication and configure write access.",
                    level="warning",
                    duration=7000,
                )
            return
        pm = self._api.project_manager
        citation = pm.get_citation(path) if hasattr(pm, "get_citation") else {}
        collection_name = self._target_collection_name(pm)
        worker = _AutoAddWorker(adapter, path, dict(citation or {}), collection_name)
        self._auto_add_workers.append(worker)
        worker.finished_sync.connect(self._on_auto_add_finished)
        worker.finished.connect(lambda w=worker: self._release_auto_add_worker(w))
        worker.start()
        if self._api:
            self._api.notify("Adding PDF to Zotero in the background.", level="info", duration=2500)

    def _on_auto_add_finished(self, path, citation, result, error) -> None:
        if error is not None:
            if self._api:
                self._api.notify(
                    f"Zotero auto-add did not finish, but the PDF stayed in the project: {error}",
                    level="warning",
                    duration=7000,
                )
            return
        if result is None:
            return
        pm = self._api.project_manager
        if result.ok and hasattr(pm, "upsert_citation"):
            updated = dict(citation or {})
            updated.setdefault("doc_id", path)
            updated["source_provider"] = "zotero"
            if result.synced_ids:
                updated["source_item_key"] = result.synced_ids[0]
            fields = dict(updated.get("fields") or {})
            fields["zotero_auto_synced"] = True
            fields["zotero_target_collection"] = self._target_collection_name(pm)
            updated["fields"] = fields
            pm.upsert_citation(updated)
            self._cache_synced_item(path, updated, result)
        if self._api:
            self._api.notify(result.message, level="success" if result.ok else "warning", duration=5000)

    def _cache_synced_item(self, path: str, citation: dict, result) -> None:
        if not self._library_cache:
            return
        details = getattr(result, "details", None) or {}
        response = details.get(path, {}) if isinstance(details, dict) else {}
        item = response.get("item", {}) if isinstance(response, dict) else {}
        key = item.get("key") or (result.synced_ids[0] if result.synced_ids else "")
        cached = {
            "item_id": key,
            "key": key,
            "doc_id": f"zotero:{key}" if key else "",
            "title": item.get("title") or citation.get("title") or "",
            "item_type": item.get("itemType") or citation.get("item_type") or "journalArticle",
            "date": item.get("date") or citation.get("date") or citation.get("year") or "",
            "year": str(item.get("date") or citation.get("year") or "")[:4],
            "DOI": item.get("DOI") or citation.get("doi") or citation.get("DOI") or "",
            "url": item.get("url") or citation.get("url") or "",
            "creators": self._normalize_cached_creators(item.get("creators") or citation.get("creators") or []),
            "authors_display": citation.get("authors") or "",
            "fields": dict(item or {}),
            "_source": "pyzotero_cache",
            "_has_pdf": True,
        }
        self._library_cache.upsert(cached)

    def _normalize_cached_creators(self, creators):
        normalized = []
        if isinstance(creators, list):
            for creator in creators:
                if not isinstance(creator, dict):
                    continue
                normalized.append({
                    "first": creator.get("firstName") or creator.get("first") or "",
                    "last": creator.get("lastName") or creator.get("last") or "",
                    "type": creator.get("creatorType") or creator.get("type") or "author",
                })
        return normalized

    def _release_auto_add_worker(self, worker) -> None:
        if worker in self._auto_add_workers:
            self._auto_add_workers.remove(worker)
        worker.deleteLater()

    def _auto_add_enabled(self) -> bool:
        return bool(self._api and self._api.config.get("auto_add_pdfs_to_zotero", False))

    def _target_collection_name(self, project_manager=None) -> str:
        if not self._api:
            return ""
        mode = self._api.config.get("target_collection_mode", "project_named")
        if mode == "existing_collection":
            return self._api.config.get("target_collection_name", "") or self._api.config.get("target_collection_key", "")
        pm = project_manager or self._api.project_manager
        project_name = getattr(pm, "project_name", "") or ""
        if not project_name:
            filepath = getattr(pm, "project_filepath", "") or ""
            if filepath:
                import os
                project_name = os.path.basename(filepath).replace(".pdfproj", "")
        return project_name or "Papyrus Project"

    def _build_outbound_adapter(self):
        from .zotero_sync_adapter import (
            DEFAULT_ZOTERO_LOCAL_API_BASE_URL,
            PyZoteroClient,
            PyZoteroOutboundSyncAdapter,
            ZoteroReadOnlyAdapter,
        )

        config = self._api.config if self._api else None
        client = PyZoteroClient(
            local_api_base_url=config.get("pyzotero_local_api_base_url", DEFAULT_ZOTERO_LOCAL_API_BASE_URL) if config else DEFAULT_ZOTERO_LOCAL_API_BASE_URL,
            library_id=config.get("pyzotero_library_id", "") if config else "",
            library_type=config.get("pyzotero_library_type", "user") if config else "user",
            api_key=config.get("pyzotero_api_key", "") if config else "",
        )
        probe = client.probe()
        if probe.write_capable:
            return PyZoteroOutboundSyncAdapter(client)
        return ZoteroReadOnlyAdapter(probe.message)

    def _refresh_outbound_adapter(self):
        adapter = self._build_outbound_adapter()
        if self._outbound_adapter and hasattr(self._outbound_adapter, "set_adapter"):
            self._outbound_adapter.set_adapter(adapter)
            return self._outbound_adapter
        self._outbound_adapter = adapter
        return adapter

    def _refresh_outbound_adapter_async(self) -> None:
        """Run the Zotero connection probe on a daemon thread to avoid blocking the GUI."""
        import threading
        def _probe():
            try:
                self._refresh_outbound_adapter()
            except Exception as exc:
                print(f"[Zotero] Background adapter refresh failed: {exc}")
        threading.Thread(target=_probe, daemon=True).start()

    def _apply_dialog_theme(self, dialog, parent=None) -> None:
        theme = None
        app_context = getattr(parent, "app_context", None) if parent else None
        theme_manager = getattr(app_context, "theme_manager", None) if app_context else None
        if theme_manager and hasattr(theme_manager, "get_theme"):
            theme = theme_manager.get_theme()
        elif parent and hasattr(parent, "theme_manager"):
            theme = parent.theme_manager.get_theme()
        if theme and hasattr(dialog, "update_theme"):
            dialog.update_theme(theme)

    # Backward-compat stubs
    def on_project_open(self, filepath: str) -> None:
        pass

    def on_project_close(self) -> None:
        pass


# ------------------------------------------------------------------
# Factory helpers
# ------------------------------------------------------------------

def _make_research_tab(ctx, db, formatter, config=None, library_cache=None):
    from .gui.research_tab import ZoteroResearchTab
    return ZoteroResearchTab(ctx, db, formatter, config=config, library_cache=library_cache)
