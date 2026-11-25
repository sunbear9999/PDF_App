from __future__ import annotations

import json
import os
from typing import Any, Iterable

from core.api.workspace_ai import WorkspaceAIApi
from core.services.workspace.workspace_graph_service import WorkspaceGraphService


GLOBAL_AI_SETTING_DEFAULTS = {
    "autopilot_enabled": False,
    "rag_enabled": True,
    "output_workspace": False,
    "search_analyses": False,
    "include_manifest": True,
    "allow_manifest_updates": True,
    "include_selected_nodes": False,
}

_RESTRICTIVE_CONTEXT_FLAGS = {
    "rag_enabled",
    "search_analyses",
    "include_manifest",
    "allow_manifest_updates",
    "include_selected_nodes",
}
MAX_SAVED_ANALYSIS_CONTEXT_CHARS = 20_000


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _json_value(raw: Any, default: Any) -> Any:
    if isinstance(raw, type(default)):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def load_global_ai_settings(project_manager) -> dict[str, bool]:
    """Return the complete, normalized filter-context settings contract."""
    settings = dict(GLOBAL_AI_SETTING_DEFAULTS)
    if not project_manager or not hasattr(project_manager, "get_metadata"):
        return settings
    raw = project_manager.get_metadata("global_ai_settings", "{}")
    stored = _json_value(raw, {})
    for key, default in GLOBAL_AI_SETTING_DEFAULTS.items():
        if key in stored:
            settings[key] = _as_bool(stored[key])
        else:
            settings[key] = default
    return settings


class WorkflowContextResolver:
    """Hydrate one workflow state from the authoritative project filters."""

    def __init__(self, project_manager):
        self.project_manager = project_manager

    def resolve(self, blueprint, initial_state: dict | None = None) -> dict:
        state = dict(initial_state or {})
        settings = load_global_ai_settings(self.project_manager)
        self._apply_settings(state, settings)
        if not self.project_manager or not hasattr(self.project_manager, "get_metadata"):
            state.setdefault("project_manifest", "")
            state.setdefault("selected_nodes", "[]")
            state.setdefault("analysis_context", "")
            state.setdefault("manifest_permissions", "")
            return state

        self._hydrate_rag_filters(state)
        self._hydrate_manifest(state)
        state.setdefault("manifest_permissions", "")
        required = self._required_contexts(blueprint)
        if required & {"workspace", "selected_nodes"} or state["include_selected_nodes"]:
            self._hydrate_workspace(state, required)
        else:
            state.setdefault("selected_nodes", "[]")
        if "analyses" in required:
            self._hydrate_analyses(state)
        return state

    def _apply_settings(self, state: dict, settings: dict[str, bool]) -> None:
        for key, global_value in settings.items():
            if key in _RESTRICTIVE_CONTEXT_FLAGS and key in state:
                state[key] = global_value and _as_bool(state[key])
            elif key not in state:
                state[key] = global_value
            elif key in GLOBAL_AI_SETTING_DEFAULTS:
                state[key] = _as_bool(state[key])
        state["autopilot_disable_rag"] = (
            _as_bool(state.get("autopilot_disable_rag", False))
            or not state["rag_enabled"]
        )

    def _hydrate_rag_filters(self, state: dict) -> None:
        pm = self.project_manager
        state.setdefault(
            "active_rag_docs",
            _json_value(pm.get_metadata("active_rag_docs", "[]"), []),
        )
        state.setdefault(
            "active_rag_tags",
            _json_value(pm.get_metadata("active_rag_tags", "[]"), []),
        )
        state.setdefault(
            "active_rag_tag_logic",
            pm.get_metadata("active_rag_tag_logic", "OR"),
        )

    def _hydrate_manifest(self, state: dict) -> None:
        if state["include_manifest"]:
            state["project_manifest"] = self.project_manager.get_metadata(
                "project_manifest", "{}"
            )
        else:
            state["project_manifest"] = ""

    def _hydrate_workspace(self, state: dict, required: set[str]) -> None:
        workspace = None
        if "workspace_data" not in state or "selected_nodes" in required:
            try:
                workspace = self.project_manager.get_workspace_data()
            except Exception:
                workspace = None
        if "workspace_data" not in state:
            state["workspace_data"] = self._workspace_json(workspace)

        if not state["include_selected_nodes"]:
            state["selected_nodes"] = "[]"
            return
        if "selected_nodes" in state:
            return
        selected_ids = state.get("_selected_workspace_node_ids", [])
        state["selected_nodes"] = self._selected_workspace_json(workspace, selected_ids)

    def _workspace_json(self, workspace) -> str:
        if workspace is None:
            return "{}"
        try:
            return WorkspaceAIApi(self.project_manager).build_ai_context(workspace)
        except Exception:
            return "{}"

    def _selected_workspace_json(self, workspace, selected_ids: Iterable[str]) -> str:
        if workspace is None or not selected_ids:
            return "[]"
        try:
            subset = WorkspaceGraphService().selected_subset(workspace, selected_ids)
            return WorkspaceAIApi(self.project_manager).build_ai_context(subset)
        except Exception:
            return "[]"

    def _hydrate_analyses(self, state: dict) -> None:
        if not state["search_analyses"]:
            state["analysis_context"] = ""
            return
        if "analysis_context" in state:
            return
        state["analysis_context"] = self._saved_analysis_context(
            state.get("active_rag_docs", [])
        )

    def _saved_analysis_context(self, allowed_docs: Iterable[str]) -> str:
        pm = self.project_manager
        if not all(hasattr(pm, name) for name in (
            "list_sources", "get_analysis_templates", "get_document_analyses"
        )):
            return ""
        allowed = {os.path.basename(str(name)) for name in allowed_docs if name}
        pieces: list[str] = []
        total_chars = 0
        for source in pm.list_sources():
            path = source.get("path", "")
            if allowed and os.path.basename(path) not in allowed:
                continue
            for template in pm.get_analysis_templates():
                template_id = template.get("id")
                if not template_id:
                    continue
                rows = pm.get_document_analyses(path, template_id)
                if rows:
                    piece = json.dumps({
                        "document": os.path.basename(path),
                        "template": template.get("title", template_id),
                        "analyses": rows,
                    }, ensure_ascii=False)
                    pieces.append(piece)
                    total_chars += len(piece)
                if total_chars >= MAX_SAVED_ANALYSIS_CONTEXT_CHARS:
                    return "\n".join(pieces)[:MAX_SAVED_ANALYSIS_CONTEXT_CHARS]
        return "\n".join(pieces)[:MAX_SAVED_ANALYSIS_CONTEXT_CHARS]

    def _required_contexts(self, blueprint) -> set[str]:
        required = set(getattr(blueprint, "active_contexts", []) or [])

        def collect(steps) -> None:
            for step in steps or []:
                required.update(getattr(step, "required_context", []) or [])
                collect(getattr(step, "if_true", []) or [])
                collect(getattr(step, "if_false", []) or [])

        collect(getattr(blueprint, "steps", []) or [])
        aliases = {"project": "manifest", "user_selection": "selected_nodes"}
        return {aliases.get(key, key) for key in required}
