# Backward-compatible re-exports. Import directly from core.registries going forward.
from core.registries.blueprint_registry import (
    BlueprintDefinition,
    BlueprintFactory,
    BlueprintRegistry,
    build_default_blueprint_registry,
)
from core.registries.workflow_registry import (
    BlueprintNodeTypeRegistry as WorkflowNodeTypeRegistry,
    build_default_blueprint_node_type_registry as build_default_workflow_node_type_registry,
)

__all__ = [
    "BlueprintDefinition",
    "BlueprintFactory",
    "BlueprintRegistry",
    "build_default_blueprint_registry",
    "WorkflowNodeTypeRegistry",
    "build_default_workflow_node_type_registry",
]
