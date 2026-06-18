"""
Example Papyrus plugin skeleton.

Drop a directory like this under plugins/<name>/plugin.py and name the
main class ``Plugin``.  It will be discovered and registered automatically
at startup.  This skeleton demonstrates the full API surface.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.plugins.papyrus_api import PapyrusAPI
    from core.registries import BlueprintRegistry, WorkspaceAIToolRegistry, OntologyRegistry
    from core.plugins.base_plugins import PluginDockSpec
    from core.plugins.extension_registry import PluginExtensionRegistry


class Plugin:
    plugin_id = "example_plugin"
    name = "Example Plugin"
    version = "0.1.0"
    dependencies: list = []
    requires_internet: bool = False

    def on_load(self, api: "PapyrusAPI") -> None:
        """Primary lifecycle hook — API is fully available here."""
        pass

    def on_register(self, api: "PapyrusAPI") -> None:
        """Backward-compat alias for on_load."""
        pass

    def register_blueprints(self, registry: "BlueprintRegistry") -> None:
        pass

    def register_workspace_tools(self, registry: "WorkspaceAIToolRegistry") -> None:
        pass

    def register_ontology_types(self, registry: "OntologyRegistry") -> None:
        pass

    def get_dock_spec(self) -> "Optional[PluginDockSpec]":
        return None

    def register_gui_extensions(self, registry: "PluginExtensionRegistry") -> None:
        pass

    def on_project_open(self, filepath: str) -> None:
        pass

    def on_project_close(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
