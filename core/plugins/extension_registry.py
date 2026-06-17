"""
core/plugins/extension_registry.py

Collects all GUI extension specs contributed by plugins.
Plugins call api.gui_extensions.add_*() in register_gui_extensions().
The GUI layer reads these specs at startup to wire up toolbar buttons,
docks, research tabs, menu items, shortcuts, commands, and workspace
context-menu items.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolbarButtonSpec:
    """A button to inject into a named dock's toolbar."""
    button_id: str
    label: str
    tooltip: str = ""
    icon: str = ""
    callback: Optional[Callable] = None
    dock_target: str = ""   # e.g. "research", "citations"
    position: int = 50
    plugin_id: str = ""


@dataclass
class ResearchTabSpec:
    """A tab to inject into the Research Assistant dock."""
    tab_id: str
    label: str
    factory: Optional[Callable] = None   # factory(app_context) -> QWidget
    target_id: str = ""                  # AI output router target id
    icon: str = ""                       # short icon/text for sidebar nav button (emoji or 1-3 chars)
    position: int = 50
    plugin_id: str = ""


@dataclass
class AIOutputRendererSpec:
    """A custom widget factory for a named AI output payload type."""
    payload_type: str
    factory: Optional[Callable] = None
    plugin_id: str = ""


@dataclass
class DockSpec:
    """A standalone dock window contributed by a plugin."""
    id: str
    label: str
    menu_name: str = ""
    factory: Optional[Callable] = None   # factory(app_context) -> QWidget
    area: str = "right"
    closable: bool = True
    is_singleton: bool = True            # True = at most one instance; False = multi-spawnable
    plugin_id: str = ""


@dataclass
class MenuItemSpec:
    """A menu item to inject into a named application menu."""
    item_id: str
    menu_name: str           # "File" | "Edit" | "View" | "Tools" | "Plugins" | custom
    label: str
    callback: Optional[Callable] = None
    shortcut: str = ""
    icon: str = ""
    separator_before: bool = False
    position: int = 999
    plugin_id: str = ""


@dataclass
class MainToolbarButtonSpec:
    """A button to inject into the main application toolbar."""
    button_id: str
    label: str
    tooltip: str = ""
    icon: str = ""
    callback: Optional[Callable] = None
    position: str = "right"   # "left" | "center" | "right"
    priority: int = 50
    plugin_id: str = ""


@dataclass
class ShortcutSpec:
    """A global keyboard shortcut registered with the main window."""
    shortcut_id: str
    key_sequence: str         # e.g. "Ctrl+Shift+Z"
    label: str
    callback: Optional[Callable] = None
    plugin_id: str = ""


@dataclass
class CommandSpec:
    """A command palette entry (Ctrl+P)."""
    command_id: str
    label: str
    description: str = ""
    callback: Optional[Callable] = None
    shortcut: str = ""
    category: str = "Plugin"
    plugin_id: str = ""


@dataclass
class WorkspaceContextMenuSpec:
    """An item to inject into workspace canvas/node context menus."""
    item_id: str
    label: str
    callback: Optional[Callable] = None   # receives WorkspaceContextMenuContext
    icon: str = ""
    context: str = "always"   # "node" | "edge" | "canvas" | "selection" | "always"
    plugin_id: str = ""


@dataclass
class WorkspaceContextMenuContext:
    """Passed to WorkspaceContextMenuSpec.callback when triggered."""
    node_ids: List[str]
    edge_ids: List[str]
    workspace_id: int
    event_bus: Any


class PluginExtensionRegistry:
    """
    Central collector for all GUI extension specs contributed by plugins.

    Plugins call api.gui_extensions.add_*() inside register_gui_extensions().
    The loader tags each new spec with plugin_id for hot-reload cleanup.
    """

    def __init__(self) -> None:
        self._toolbar_buttons: List[ToolbarButtonSpec] = []
        self._research_tabs: List[ResearchTabSpec] = []
        self._ai_renderers: List[AIOutputRendererSpec] = []
        self._extra_docks: List[DockSpec] = []
        self._menu_items: List[MenuItemSpec] = []
        self._main_toolbar_buttons: List[MainToolbarButtonSpec] = []
        self._shortcuts: List[ShortcutSpec] = []
        self._commands: List[CommandSpec] = []
        self._workspace_context_menu_items: List[WorkspaceContextMenuSpec] = []
        self._actions: List[Any] = []  # ActionSpec instances from gui.registry.action_spec

    # ----------------------------------------------------------------
    # Registration
    # ----------------------------------------------------------------

    def add_toolbar_button(self, spec: ToolbarButtonSpec) -> None:
        self._toolbar_buttons.append(spec)

    def add_research_tab(self, spec: ResearchTabSpec) -> None:
        self._research_tabs.append(spec)

    def add_ai_renderer(self, spec: AIOutputRendererSpec) -> None:
        self._ai_renderers.append(spec)

    def add_dock(self, spec: DockSpec) -> None:
        self._extra_docks.append(spec)

    def add_menu_item(self, spec: MenuItemSpec) -> None:
        self._menu_items.append(spec)

    def add_main_toolbar_button(self, spec: MainToolbarButtonSpec) -> None:
        self._main_toolbar_buttons.append(spec)

    def add_shortcut(self, spec: ShortcutSpec) -> None:
        self._shortcuts.append(spec)

    def add_command(self, spec: CommandSpec) -> None:
        self._commands.append(spec)

    def add_workspace_context_menu_item(self, spec: WorkspaceContextMenuSpec) -> None:
        self._workspace_context_menu_items.append(spec)

    def add_action(self, spec: Any) -> None:
        """Register an ActionSpec (from gui.registry.action_spec) for cross-context mounting.

        Example:
            from gui.registry.action_spec import ActionSpec
            api.gui_extensions.add_action(ActionSpec(
                action_id="myplugin.add_note",
                label="Add Note",
                mounts=["context_menu:pdf:text_selection", "context_menu:document_list:item"],
                callback=my_handler,
                plugin_id="myplugin",
            ))
        """
        self._actions.append(spec)

    def get_actions(self) -> List[Any]:
        return list(self._actions)

    # ----------------------------------------------------------------
    # Retrieval
    # ----------------------------------------------------------------

    def get_toolbar_buttons(self, dock_target: str = "") -> List[ToolbarButtonSpec]:
        if dock_target:
            return [s for s in self._toolbar_buttons if s.dock_target == dock_target]
        return list(self._toolbar_buttons)

    def get_research_tabs(self) -> List[ResearchTabSpec]:
        return sorted(self._research_tabs, key=lambda s: s.position)

    def get_ai_renderers(self) -> Dict[str, AIOutputRendererSpec]:
        return {s.payload_type: s for s in self._ai_renderers}

    def get_extra_docks(self) -> List[DockSpec]:
        return list(self._extra_docks)

    def get_menu_items(self, menu_name: str = "") -> List[MenuItemSpec]:
        if menu_name:
            return [s for s in self._menu_items if s.menu_name == menu_name]
        return list(self._menu_items)

    def get_main_toolbar_buttons(self, position: str = "") -> List[MainToolbarButtonSpec]:
        if position:
            return [s for s in self._main_toolbar_buttons if s.position == position]
        return list(self._main_toolbar_buttons)

    def get_shortcuts(self) -> List[ShortcutSpec]:
        return list(self._shortcuts)

    def get_commands(self) -> List[CommandSpec]:
        return list(self._commands)

    def get_workspace_context_menu_items(self, context: str = "") -> List[WorkspaceContextMenuSpec]:
        if context:
            return [s for s in self._workspace_context_menu_items
                    if s.context in (context, "always")]
        return list(self._workspace_context_menu_items)

    # ----------------------------------------------------------------
    # Hot-reload cleanup
    # ----------------------------------------------------------------

    # ----------------------------------------------------------------
    # Backward-compat aliases (old "register_*" names)
    # ----------------------------------------------------------------

    def register_research_tab(self, spec: ResearchTabSpec) -> None:
        self.add_research_tab(spec)

    def register_toolbar_button(self, spec: ToolbarButtonSpec) -> None:
        self.add_toolbar_button(spec)

    def register_dock(self, spec: DockSpec) -> None:
        self.add_dock(spec)

    def register_menu_item(self, spec: MenuItemSpec) -> None:
        self.add_menu_item(spec)

    def register_shortcut(self, spec: ShortcutSpec) -> None:
        self.add_shortcut(spec)

    def register_command(self, spec: CommandSpec) -> None:
        self.add_command(spec)

    def register_main_toolbar_button(self, spec: MainToolbarButtonSpec) -> None:
        self.add_main_toolbar_button(spec)

    def register_workspace_context_menu_item(self, spec: WorkspaceContextMenuSpec) -> None:
        self.add_workspace_context_menu_item(spec)

    def _spec_list_attrs(self) -> List[str]:
        """Canonical list of all spec-list attribute names. Used by hot-reload cleanup and tagging."""
        return [
            "_toolbar_buttons", "_research_tabs", "_ai_renderers", "_extra_docks",
            "_menu_items", "_main_toolbar_buttons", "_shortcuts", "_commands",
            "_workspace_context_menu_items", "_actions",
        ]

    def remove_plugin_specs(self, plugin_id: str) -> None:
        """Remove all specs contributed by plugin_id (called during hot-reload)."""
        for attr in self._spec_list_attrs():
            setattr(self, attr, [s for s in getattr(self, attr)
                                 if getattr(s, "plugin_id", None) != plugin_id])
