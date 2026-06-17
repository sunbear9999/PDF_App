from __future__ import annotations

from typing import Dict, Iterable, Optional

from core.engine.workflow_model import WorkflowNodeType


class BlueprintNodeTypeRegistry:
    """Registry for workflow execution node types (LLM_QUERY, RAG_SEARCH, etc.).

    Named 'Blueprint' to distinguish from WorkspaceNodeTypeRegistry, which
    governs node types on the user's research canvas.
    """

    def __init__(self):
        self._types: Dict[str, WorkflowNodeType] = {}

    def register(self, node_type: WorkflowNodeType):
        self._types[node_type.id] = node_type

    def get(self, type_id: str) -> Optional[WorkflowNodeType]:
        return self._types.get(type_id)

    def all(self) -> Iterable[WorkflowNodeType]:
        return self._types.values()

    def iter_category(self, category: str) -> Iterable[WorkflowNodeType]:
        for node_type in self._types.values():
            if node_type.category == category:
                yield node_type

    def remove_by_plugin(self, plugin_id: str) -> None:
        self._types = {k: v for k, v in self._types.items()
                       if getattr(v, "plugin_id", None) != plugin_id}


def build_default_blueprint_node_type_registry() -> BlueprintNodeTypeRegistry:
    registry = BlueprintNodeTypeRegistry()
    defaults = [
        WorkflowNodeType("workflow.llm_query", "LLM Query", "AI", "LLM_QUERY", "Generate text or structured JSON with a model.", default_ui_format="live_stream"),
        WorkflowNodeType("workflow.rag_search", "RAG Search", "Search", "RAG_SEARCH", "Search indexed documents and return context."),
        WorkflowNodeType("workflow.foreach", "For Each", "Control", "FOREACH", "Run nested steps for every item in a list."),
        WorkflowNodeType("workflow.branch", "Branch", "Control", "BRANCH", "Route execution based on a condition.", output_ports=["true", "false"]),
        WorkflowNodeType("workflow.python_script", "Python Script", "Transform", "PYTHON_SCRIPT", "Run a constrained local transform script."),
        WorkflowNodeType("workflow.user_input", "User Input", "Interaction", "USER_INPUT", "Pause and collect user input."),
        WorkflowNodeType("workflow.database_write", "Database Write", "Persistence", "DATABASE_WRITE", "Persist workflow output to a project table."),
        WorkflowNodeType("workflow.library_ref", "Reusable Step", "Library", "LIBRARY_REF", "Run a saved step definition from the step library."),
    ]
    for node_type in defaults:
        registry.register(node_type)
    return registry
