"""
Example Papyrus plugin.

Drop a directory like this under plugins/<name>/plugin.py and name the
main class ``Plugin``.  It will be discovered and registered automatically
at startup.  This skeleton demonstrates the full API surface.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.papyrus_core import PapyrusCore
    from core.registries import BlueprintRegistry, WorkspaceAIToolRegistry, OntologyRegistry
    from core.plugins.base_plugins import PluginDockSpec


class Plugin:
    plugin_id = "example_plugin"
    name = "Example Plugin"
    version = "0.1.0"

    def on_register(self, core: "PapyrusCore") -> None:
        """Called once after discovery, before the GUI starts."""
        print(f"[{self.name}] registered with PapyrusCore")

    def register_blueprints(self, registry: "BlueprintRegistry") -> None:
        """Add custom AI action blueprints here."""
        # Example: registry.register(my_blueprint_definition)
        pass

    def register_workspace_tools(self, registry: "WorkspaceAIToolRegistry") -> None:
        """Add custom workspace toolbar/context-menu AI tools here."""
        pass

    def register_ontology_types(self, registry: "OntologyRegistry") -> None:
        """Add custom entity or relation types here."""
        pass

    def get_dock_spec(self) -> "Optional[PluginDockSpec]":
        """Return a PluginDockSpec to register a GUI dock, or None."""
        # Example:
        # from core.plugins.base_plugins import PluginDockSpec
        # return PluginDockSpec(
        #     id="example_dock",
        #     menu_name="Example",
        #     area="right",
        #     is_singleton=True,
        #     factory=lambda parent: ExampleDockWidget(parent),
        # )
        return None
