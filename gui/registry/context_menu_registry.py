"""
gui/registry/context_menu_registry.py

Universal context menu builder.

Every context menu in the app is built through ContextMenuRegistry:
  1. A "core builder" function provides the hardcoded items for that context.
  2. The registry appends all ActionSpec entries registered for that context
     type, sorted by priority.

This decouples the GUI from plugin code: plugins register ActionSpecs,
the registry wires them in, no widget needs to know plugin names.

Usage in a widget:
    menu = self.ctx_menu_registry.build_menu(
        ContextMenuContext(context_type="pdf:text_selection",
                           selected_ids=[], payload={"text": sel_text},
                           event_bus=self.bus),
        parent_widget=self,
    )
    menu.exec(global_pos)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMenu
    from core.events.event_bus import EventBus
    from gui.registry.action_spec import ActionRegistry, ActionSpec


@dataclass
class ContextMenuContext:
    """
    Passed to every context menu callback and to core builder functions.

    context_type examples:
        "workspace:node"      "workspace:edge"      "workspace:canvas"
        "workspace:selection" "pdf:text_selection"  "document_list:item"
        "citation:item"       "essay:selection"     "notes:item"
    """
    context_type: str
    selected_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    event_bus: Optional[Any] = None   # EventBus — typed as Any to avoid circular imports


class ContextMenuRegistry:
    """
    Builds QMenus from a combination of registered core builders and
    plugin-contributed ActionSpecs.
    """

    def __init__(self, action_registry: "ActionRegistry") -> None:
        self._action_registry = action_registry
        self._builders: dict[str, Callable] = {}

    def register_builder(
        self,
        context_type: str,
        builder_fn: Callable[["ContextMenuContext", "QMenu"], None],
    ) -> None:
        """
        Register a builder function for a context type.

        builder_fn(context, menu) — receives the context and a fresh QMenu.
        It should add hardcoded core items to ``menu`` in place.
        Plugin items are appended automatically after the builder returns.
        """
        self._builders[context_type] = builder_fn

    def build_menu(
        self,
        context: ContextMenuContext,
        parent_widget=None,
    ) -> "QMenu":
        """
        Build and return a fully-populated QMenu for the given context.
        """
        from PySide6.QtWidgets import QMenu
        menu = QMenu(parent_widget)

        # 1. Run the core builder for this context type (adds hardcoded items)
        builder = self._builders.get(context.context_type)
        if builder:
            try:
                builder(context, menu)
            except Exception as exc:
                print(f"[ContextMenuRegistry] Builder for '{context.context_type}' raised: {exc}")

        # 2. Collect plugin ActionSpecs for "context_menu:{context_type}"
        mount_key = f"context_menu:{context.context_type}"
        plugin_specs = list(self._action_registry.iter_mount(mount_key))
        if plugin_specs:
            if not menu.isEmpty():
                menu.addSeparator()
            for spec in plugin_specs:
                if spec.separator_before and not menu.isEmpty():
                    menu.addSeparator()
                action = menu.addAction(f"{spec.icon} {spec.label}".strip() if spec.icon else spec.label)
                if spec.tooltip:
                    action.setToolTip(spec.tooltip)
                if spec.enabled_predicate:
                    try:
                        action.setEnabled(bool(spec.enabled_predicate()))
                    except Exception:
                        pass
                if spec.callback:
                    action.triggered.connect(lambda checked=False, cb=spec.callback, ctx=context: cb(ctx))

        return menu

    def has_builder(self, context_type: str) -> bool:
        return context_type in self._builders
