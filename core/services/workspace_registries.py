"""Compatibility imports for workspace registry factories."""

from core.services.workspace.registries import (
    build_default_workspace_ai_tool_registry,
    build_default_workspace_node_type_registry,
)

__all__ = [
    "build_default_workspace_ai_tool_registry",
    "build_default_workspace_node_type_registry",
]
