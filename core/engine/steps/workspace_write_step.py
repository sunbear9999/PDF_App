"""
core/engine/steps/workspace_write_step.py

Writes entities (nodes) and optionally relations (edges) directly into the active
workspace graph via the EventBus, without requiring an LLM step.

Input: a JSON object or array. If the input is a list of dicts with a "claim",
"text", or "label" key, each item is converted to a workspace node automatically.
"""
from __future__ import annotations

import json

from core.engine.steps.base_step import BaseStep
from core.engine.execution_result import SystemEvent
from core.plugins.plugin_step_protocol import StepContext


def _items_to_graph(items: list, node_type: str, source_path: str = "") -> dict:
    """Convert a flat list of dicts into a minimal workspace graph payload."""
    entities = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            label = item
            props = {}
        else:
            label = (
                item.get("claim") or item.get("text") or item.get("label")
                or item.get("title") or str(item)
            )
            props = {k: v for k, v in item.items() if k not in ("claim", "text", "label", "title")}
        entity = {
            "id": f"n_{i}",
            "type": node_type,
            "label": label[:200],
            "properties": props,
        }
        if source_path:
            entity["properties"]["source"] = source_path
        entities.append(entity)
    return {"entities": entities, "relations": []}


class WorkspaceWriteStep(BaseStep):
    step_type = "WORKSPACE_WRITE"
    label = "Workspace Write"
    category = "Persistence"
    description = (
        "Write entities and/or relations directly to the active workspace graph. "
        "Accepts a JSON list of items or a {entities, relations} dict."
    )
    input_schema = {
        "data": {"type": "string", "label": "JSON data (list or {entities, relations})", "required": True},
        "node_type": {"type": "string", "label": "Node type for list items (default: 'claim')"},
        "source_path": {"type": "string", "label": "Source document path (optional)"},
        "workspace_id": {"type": "integer", "label": "Workspace ID (default: active workspace)"},
    }

    def execute(self, context: StepContext, inputs: dict):
        raw = inputs.get("data") or ""
        if isinstance(raw, (list, dict)):
            data = raw
        else:
            try:
                data = json.loads(raw)
            except Exception:
                return self.build_result("Error: data must be valid JSON")

        node_type = inputs.get("node_type") or "claim"
        source_path = str(inputs.get("source_path") or "")

        if isinstance(data, list):
            graph = _items_to_graph(data, node_type, source_path)
        elif isinstance(data, dict) and ("entities" in data or "relations" in data):
            graph = data
        else:
            graph = _items_to_graph([data], node_type, source_path)

        workspace_id = inputs.get("workspace_id") or None
        system_events = [
            SystemEvent(
                event_type="bus_emit",
                payload={
                    "signal": "workspace_action_requested",
                    "intent": "IMPORT_GRAPH",
                    "data": {"graph": graph, "workspace_id": workspace_id},
                },
            )
        ]

        n = len(graph.get("entities", []))
        r = len(graph.get("relations", []))
        return self.build_result(
            f"Written {n} node(s), {r} relation(s) to workspace.",
            system_events=system_events,
        )
