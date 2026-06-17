"""
gui/registry — GUI extensibility registries.

Provides:
  ActionSpec / ActionRegistry  — mount-point-driven action system
  ContextMenuRegistry          — universal context menu builder
  extension_spec_bridge        — translates PluginExtensionRegistry → ActionRegistry
"""
from gui.registry.action_spec import ActionSpec, ActionRegistry
from gui.registry.context_menu_registry import ContextMenuRegistry, ContextMenuContext

__all__ = [
    "ActionSpec",
    "ActionRegistry",
    "ContextMenuRegistry",
    "ContextMenuContext",
]
