"""
plugins/locallaws/plugin.py
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.plugins.papyrus_api import PapyrusAPI
    from core.plugins.extension_registry import PluginExtensionRegistry

from .law_manager import LocalLawManager


class Plugin:
    plugin_id = "locallaws"
    name = "Local Laws RAG Integration"
    version = "1.0.0"
    dependencies: list = []
    requires_internet: bool = True

    def __init__(self):
        self._api: Optional["PapyrusAPI"] = None
        self.law_manager: Optional[LocalLawManager] = None
        self._original_query_method = None

    def on_load(self, api: "PapyrusAPI") -> None:
        self._api = api
        self.law_manager = LocalLawManager(api)
        
        # Native registration! No more monkeypatching.
        api.llm.register_query_interceptor(self._laws_rag_interceptor)

    def on_unload(self) -> None:
        # Cleanly remove the hook so hot-reloads don't stack duplicates
        if self._api:
            self._api.llm.unregister_query_interceptor(self._laws_rag_interceptor)

    def _laws_rag_interceptor(self, embedding_vector, n_results, allowed_docs, tag_filters, current_results):
        active_dbs = self._api.config.get("active_laws", [])
        if not active_dbs or not self.law_manager.is_available():
            return current_results

        # Plugin runs its own DB query and merges it with the core's current_results
        law_res = self.law_manager.query_laws(embedding_vector, n_results, active_dbs)
        return self.law_manager.merge_chroma_results(current_results, law_res, n_results)
    def register_gui_extensions(self, registry: "PluginExtensionRegistry") -> None:
        from core.plugins.extension_registry import MenuItemSpec, MainToolbarButtonSpec
        
        # 1. Adds it to the Plugins dropdown menu
        registry.add_menu_item(MenuItemSpec(
            item_id="locallaws_settings",
            menu_name="Plugins",
            label="Local Laws Settings",
            callback=self._open_settings_dialog,
            position=60,
            plugin_id=self.plugin_id,
        ))

        # 2. Adds a persistent layout button to open the Directory Panel
        registry.add_main_toolbar_button(MainToolbarButtonSpec(
            button_id="locallaws_dock_trigger",
            label="⚖️ Laws",
            tooltip="Open Local Laws Directory Explorer",
            callback=self._toggle_laws_dock,
            position="center", # Places it in the center block with Data Dock & Assistant
            priority=60,       # Orders it nicely right next to your standard tabs
            plugin_id=self.plugin_id
        ))

    def _toggle_laws_dock(self) -> None:
        """Forces the DockManager to mount and reveal our Custom Directory."""
        from PySide6.QtWidgets import QApplication
        parent = QApplication.activeWindow()
        
        # Pulls the active framework dock context safely from Main Window
        if hasattr(parent, "dock_manager"):
            parent.dock_manager.spawn("locallaws_dock")

    def _hooked_query_by_raw_embedding(self, embedding_vector, n_results=5, allowed_docs=None, tag_filters=None):
        project_res = self._original_query_method(embedding_vector, n_results, allowed_docs, tag_filters)
        
        active_dbs = self._api.config.get("active_laws", [])
        if not active_dbs or not self.law_manager.is_available():
            return project_res

        law_res = self.law_manager.query_laws(embedding_vector, n_results, active_dbs)
        return self.law_manager.merge_chroma_results(project_res, law_res, n_results)
    def _open_settings_dialog(self) -> None:
        from PySide6.QtWidgets import QApplication
        from gui.managers.dialog_manager import exec_as_modal
        from .gui.settings_dialog import LocalLawsSettingsDialog
        
        parent = QApplication.activeWindow()
        dialog = LocalLawsSettingsDialog(self._api, self.law_manager, parent=parent)
        
        # Apply the active workspace palette before executing
        self._apply_dialog_theme(dialog, parent)
        
        exec_as_modal(dialog)

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
    def on_register(self, api: "PapyrusAPI") -> None:
        pass

    def register_blueprints(self, registry) -> None:
        pass

    def register_workspace_tools(self, registry) -> None:
        pass

    def register_ontology_types(self, registry) -> None:
        pass

    def get_dock_spec(self) -> Optional[object]:
        """Registers the Local Laws Directory as a right-side dock in the workspace."""
        from core.plugins.base_plugins import PluginDockSpec
        from .gui.laws_dock import LocalLawsDock
        
        return PluginDockSpec(
            id="locallaws_dock",
            menu_name="Local Laws Directory",
            area="right",
            is_singleton=True,
            factory=lambda app_context: LocalLawsDock(self._api, self.law_manager, app_context),
            plugin_id=self.plugin_id
        )

    def on_project_open(self, filepath: str) -> None:
        pass

    def on_project_close(self) -> None:
        pass