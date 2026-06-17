from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


ModelProvider = Callable[[], str]


@dataclass
class AgentLoopConfig:
    name: str
    planner_blueprint_id: str
    session_metadata_key: str
    ui_target: str
    goal_state_key: str
    latest_input_state_key: str = "latest_user_input"
    memory_state_key: str = "agent_memory"
    artifacts_state_key: str = "agent_artifacts"
    tool_catalog_state_key: str = "tool_catalog"
    default_tool_persona: str = "RAG Agent Mode"
    memory_limit: int = 6000
    recent_artifact_limit: int = 8
    default_state: Dict[str, Any] = field(default_factory=dict)


class AgentContextBuilder:
    def __init__(self, project_manager, blueprint_registry, model_provider: ModelProvider):
        self.project_manager = project_manager
        self.blueprint_registry = blueprint_registry
        self.model_provider = model_provider

    def build(self, session, config: AgentLoopConfig) -> Dict[str, Any]:
        state = {
            "selected_model": self.model_provider(),
            config.goal_state_key: session.goal if session else "",
            config.latest_input_state_key: session.latest_user_input if session else "",
            config.memory_state_key: session.memory if session else "",
            config.artifacts_state_key: self._artifact_summary(session, config.recent_artifact_limit),
            config.tool_catalog_state_key: self._tool_catalog_json(),
            "project_manifest": "{}",
            "workspace_data": "{}",
            "active_rag_docs": [],
            "active_rag_tags": [],
            "active_rag_tag_logic": "OR",
        }
        state.update(config.default_state)

        pm = self.project_manager
        if not pm:
            return state

        state["project_manifest"] = pm.get_metadata("project_manifest", "{}")
        state["active_rag_docs"] = self._metadata_json("active_rag_docs", [])
        state["active_rag_tags"] = self._metadata_json("active_rag_tags", [])
        state["active_rag_tag_logic"] = pm.get_metadata("active_rag_tag_logic", "OR")
        if getattr(pm, "project_filepath", None):
            state["__db_path__"] = pm.project_filepath

        try:
            from core.api.workspace_ai import WorkspaceAIApi
            state["workspace_data"] = WorkspaceAIApi(pm).build_ai_context(pm.get_workspace_data())
        except Exception:
            state["workspace_data"] = "{}"

        return state

    def _metadata_json(self, key: str, default: List[Any]):
        raw = self.project_manager.get_metadata(key, json.dumps(default))
        if isinstance(raw, list):
            return raw
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else default
        except Exception:
            return default

    def _tool_catalog_json(self) -> str:
        tools = self.blueprint_registry.agent_tools() if self.blueprint_registry else []
        return json.dumps(tools, indent=2)

    def _artifact_summary(self, session, limit: int) -> str:
        if not session:
            return "[]"
        recent = [artifact.to_dict() for artifact in session.artifacts[-limit:]]
        return json.dumps(recent, indent=2)
