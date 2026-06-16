from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, List, Optional
from typing import Protocol, runtime_checkable

if TYPE_CHECKING:
    from core.papyrus_core import PapyrusCore
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
    """

    plugin_id: str
    name: str
    version: str

    def on_register(self, core: "PapyrusCore") -> None:
        """Called once after the plugin is discovered, before the GUI starts."""
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
