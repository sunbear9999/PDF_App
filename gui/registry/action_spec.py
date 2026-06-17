"""
gui/registry/action_spec.py

Unified action system.  A single ActionSpec can declare all the places
("mount points") where it should appear — toolbar, context menu, menu bar,
command palette — so plugins only need to register an action once.

Mount point format (hierarchical prefix strings):
    "context_menu:workspace:node"     — workspace node right-click menu
    "context_menu:workspace:edge"     — workspace edge right-click menu
    "context_menu:workspace:canvas"   — workspace canvas right-click menu
    "context_menu:workspace:selection"— workspace multi-select right-click
    "context_menu:pdf:text_selection" — PDF viewer text-selection menu
    "context_menu:document_list:item" — document explorer item right-click
    "context_menu:citation:item"      — citation dock item right-click
    "context_menu:essay:selection"    — essay text-selection right-click
    "context_menu:notes:item"         — notes list item right-click
    "toolbar:main:left"               — main toolbar left zone
    "toolbar:main:center"             — main toolbar center zone
    "toolbar:main:right"              — main toolbar right zone
    "menu:File" | "menu:Edit" | ...   — application menu bar items
    "command_palette"                 — Ctrl+P command palette

ActionRegistry.iter_mount(prefix) returns all specs whose mounts list
contains an entry that starts with that prefix, sorted by priority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


@dataclass
class ActionSpec:
    """
    A single, mount-point-aware action that can appear in multiple GUI locations.

    Plugins register these via api.gui_extensions.add_action(spec) or
    directly into the ActionRegistry on AppContext.
    """
    action_id: str
    label: str
    icon: str = ""
    tooltip: str = ""
    callback: Optional[Callable] = None
    shortcut: str = ""
    enabled_predicate: Optional[Callable[[], bool]] = None

    # Where this action appears. See module docstring for valid values.
    mounts: list[str] = field(default_factory=list)

    # Controls ordering within a mount point (lower = earlier).
    priority: int = 50
    separator_before: bool = False

    # Plugin ownership for hot-reload cleanup.
    plugin_id: str = ""

    # Future: if set, callback is derived from running this blueprint.
    blueprint_id: str = ""


class ActionRegistry:
    """
    Central store for ActionSpec instances.

    Plugins and core features register actions once.  GUI components query
    the registry for a mount point prefix to discover what to render.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ActionSpec] = {}   # action_id → spec

    def register(self, spec: ActionSpec) -> None:
        self._specs[spec.action_id] = spec

    def get(self, action_id: str) -> Optional[ActionSpec]:
        return self._specs.get(action_id)

    def iter_mount(self, mount_point: str) -> Iterable[ActionSpec]:
        """
        Return all specs whose mounts list contains mount_point or a prefix of
        it (e.g. "context_menu:workspace" matches "context_menu:workspace:node").
        Results are sorted by priority ascending.
        """
        matched = [
            s for s in self._specs.values()
            if any(
                mount_point == m or mount_point.startswith(m + ":") or m.startswith(mount_point)
                for m in s.mounts
            )
        ]
        return sorted(matched, key=lambda s: s.priority)

    def remove_plugin(self, plugin_id: str) -> None:
        """Remove all specs contributed by plugin_id (used during hot-reload)."""
        self._specs = {
            aid: s for aid, s in self._specs.items()
            if s.plugin_id != plugin_id
        }

    def all_specs(self) -> list[ActionSpec]:
        return list(self._specs.values())
