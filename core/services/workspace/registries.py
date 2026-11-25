# Backward-compatible re-exports. Import directly from core.registries going forward.
"""Factories for the built-in workspace extension registries."""

from core.registries.workspace_registry import (
    WorkspaceActionDefinition,
    WorkspaceActionRegistry,
    WorkspaceAIToolDefinition,
    WorkspaceAIToolRegistry,
    WorkspaceNodeTypeDefinition,
    WorkspaceNodeTypeRegistry,
    build_default_workspace_action_registry,
    build_default_workspace_ai_tool_registry,
    build_default_workspace_node_type_registry,
    infer_workspace_node_type_id,
    build_text_node_display,
    build_quote_node_display,
)

__all__ = [
    "WorkspaceActionDefinition",
    "WorkspaceActionRegistry",
    "WorkspaceAIToolDefinition",
    "WorkspaceAIToolRegistry",
    "WorkspaceNodeTypeDefinition",
    "WorkspaceNodeTypeRegistry",
    "build_default_workspace_action_registry",
    "build_default_workspace_ai_tool_registry",
    "build_default_workspace_node_type_registry",
    "infer_workspace_node_type_id",
    "build_text_node_display",
    "build_quote_node_display",
]
