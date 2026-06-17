# core/plugins/base_plugins.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from core.papyrus_core import PapyrusCore
    from core.plugins.papyrus_api import PapyrusAPI
    from core.registries import BlueprintRegistry, WorkspaceAIToolRegistry, OntologyRegistry


@dataclass
class PluginDockSpec:
    """Lightweight dock description a plugin can return.

    The GUI layer converts this into a full DockDefinition using the plugin's
    factory. Keeps core free of Qt imports.
    """
    id: str
    menu_name: str
    area: str = "right"
    is_singleton: bool = True
    factory: Optional[Callable[[Any], Any]] = None


@runtime_checkable
class PapyrusPlugin(Protocol):
    """Protocol that every Papyrus plugin must implement.

    Drop a class implementing this protocol into plugins/<name>/plugin.py
    and name it ``Plugin``. The plugin loader discovers and registers it
    automatically at startup.

    Lifecycle call order:
        1. __init__()              — constructor, no API available yet
        2. on_load(api)            — primary hook; API fully available
        3. on_register(api)        — backward-compat alias for on_load
        4. register_blueprints     — called after on_load
        5. register_workspace_tools
        6. register_ontology_types
        7. get_dock_spec           — read by GUI after all plugins loaded
        8. register_gui_extensions
        9. on_project_open/close   — emitted on each subsequent project event
       10. on_unload               — called on app close or hot-reload
    """

    plugin_id: str
    name: str
    version: str
    dependencies: List[str]  # list of plugin_ids this plugin requires

    def on_load(self, api: "PapyrusAPI") -> None:
        """
        Primary lifecycle hook. Called once after discovery with a fully
        constructed PapyrusAPI.

        - Register custom services: api.register_service(...)
        - Subscribe to events: api.subscribe(...)
        - Access required services: api.require_service(...)
        """
        ...

    def on_register(self, api: "PapyrusAPI") -> None:
        """Backward-compat alias for on_load. New plugins should use on_load."""
        ...

    def register_blueprints(self, registry: "BlueprintRegistry") -> None:
        """Register any AI action blueprints this plugin provides."""
        ...

    def register_workspace_tools(self, registry: "WorkspaceAIToolRegistry") -> None:
        """Register workspace AI tools (toolbar / context menu entries)."""
        ...

    def register_ontology_types(self, registry: "OntologyRegistry") -> None:
        """Register custom entity or relation types for the knowledge graph."""
        ...

    def get_dock_spec(self) -> Optional[PluginDockSpec]:
        """Return a dock spec if this plugin provides a GUI dock, else None."""
        ...

    def register_gui_extensions(self, registry: Any) -> None:
        """Register toolbar buttons, research tabs, AI renderers, or docks."""
        ...

    def on_project_open(self, filepath: str) -> None:
        """Called each time a project is opened. Override for per-project init."""
        ...

    def on_project_close(self) -> None:
        """Called just before a project closes. Override to flush plugin state."""
        ...

    def on_unload(self) -> None:
        """Called on app close or hot-reload. Override to release resources."""
        ...
