"""
gui/registry/extension_spec_bridge.py

Translates legacy PluginExtensionRegistry specs into ActionSpec entries
so the new ActionRegistry / ContextMenuRegistry system picks them up.

Called once at startup from MainWindow after all plugins are loaded.
Old plugin code using the legacy API continues to work with zero changes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.plugins.extension_registry import PluginExtensionRegistry
    from gui.registry.action_spec import ActionRegistry


# Map from WorkspaceContextMenuSpec.context to ActionSpec mount point suffix
_CONTEXT_MAP = {
    "node":      "workspace:node",
    "edge":      "workspace:edge",
    "canvas":    "workspace:canvas",
    "selection": "workspace:selection",
    "always":    "workspace",   # matches all workspace sub-contexts
}


def bridge_plugin_extensions(
    plugin_registry: "PluginExtensionRegistry",
    action_registry: "ActionRegistry",
) -> None:
    """
    Read all WorkspaceContextMenuSpec entries from plugin_registry and
    register equivalent ActionSpec entries in action_registry.

    This is a one-way, additive operation — call it once after plugins load.
    Hot-reload cleanup uses action_registry.remove_plugin(plugin_id).
    """
    from gui.registry.action_spec import ActionSpec

    # 1. Transfer direct ActionSpec registrations (new plugin API)
    for spec in plugin_registry.get_actions():
        try:
            action_registry.register(spec)
        except Exception as exc:
            print(f"[ExtensionBridge] Failed to register ActionSpec '{getattr(spec, 'action_id', '?')}': {exc}")

    # 2. Bridge legacy WorkspaceContextMenuSpec entries
    for spec in plugin_registry.get_workspace_context_menu_items():
        context_suffix = _CONTEXT_MAP.get(spec.context, f"workspace:{spec.context}")
        mount = f"context_menu:{context_suffix}"

        def _make_callback(original_cb, plugin_spec=spec):
            def _cb(ctx):
                if original_cb:
                    try:
                        from core.plugins.extension_registry import WorkspaceContextMenuContext
                        legacy_ctx = WorkspaceContextMenuContext(
                            node_ids=ctx.selected_ids,
                            edge_ids=[],
                            workspace_id=ctx.payload.get("workspace_id", 0),
                            event_bus=ctx.event_bus,
                        )
                        original_cb(legacy_ctx)
                    except Exception as exc:
                        print(f"[ExtensionBridge] Callback for '{plugin_spec.item_id}' raised: {exc}")
            return _cb

        action_registry.register(ActionSpec(
            action_id=f"bridge.{spec.item_id}",
            label=spec.label,
            icon=spec.icon,
            callback=_make_callback(spec.callback),
            mounts=[mount],
            plugin_id=spec.plugin_id,
        ))
