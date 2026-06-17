from __future__ import annotations

from typing import Iterable

from core.models.workspace_models import WorkspaceModel


class WorkspaceGraphService:
    def __init__(self, event_bus=None):
        self.bus = event_bus

    def selected_subset(self, model: WorkspaceModel, selected_ids: Iterable[str]) -> WorkspaceModel:
        selected = set(selected_ids)
        subset = WorkspaceModel(workspace_id=model.workspace_id)
        subset.nodes = [n for n in model.nodes if n.id in selected]
        subset.edges = [e for e in model.edges if e.source in selected and e.target in selected]
        return subset

    def validate_delta(self, delta: WorkspaceModel, existing_node_ids: Iterable[str]) -> WorkspaceModel:
        known = set(existing_node_ids) | {n.id for n in delta.nodes}
        delta.edges = [e for e in delta.edges if e.source in known and e.target in known]
        delta.deleted_edge_ids = [e_id for e_id in delta.deleted_edge_ids if e_id]
        delta.deleted_node_ids = [n_id for n_id in delta.deleted_node_ids if n_id]
        return delta

    def copy_selection_payload(self, nodes, edges) -> dict:
        selected_node_set = set(nodes)
        return {
            "nodes": [
                {
                    "old_id": n.node_id,
                    "highlight_id": n.highlight_id,
                    "quote": n.quote,
                    "note_text": n.note,
                    "color": n.color,
                    "is_custom": n.is_custom,
                    "pdf_path": n.pdf_path,
                    "page_num": n.page_num,
                    "manual_font_size": n.manual_font_size,
                    "width": n.base_width,
                    "height": n.base_height,
                    "x": n.pos().x(),
                    "y": n.pos().y(),
                    "node_type_id": getattr(n, "node_type_id", ""),
                    "entity_type": getattr(n, "entity_type", ""),
                    "source_id": getattr(n, "source_id", None),
                    "entity_properties": dict(getattr(n, "entity_properties", {}) or {}),
                    "entity_state": dict(getattr(n, "entity_state", {}) or {}),
                    "tags": n.get_tag_names() if hasattr(n, "get_tag_names") else [],
                }
                for n in nodes
            ],
            "edges": [
                {
                    "source_old_id": e.source_node.node_id,
                    "dest_old_id": e.dest_node.node_id,
                    "label": e.label_text,
                    "color": e.base_color.name(),
                    "weight": e.weight,
                    "relation_type": getattr(e, "relation_type", "relation.basic"),
                    "evidence_ids": list(getattr(e, "evidence_ids", []) or []),
                    "relation_properties": dict(getattr(e, "relation_properties", {}) or {}),
                    "relation_state": dict(getattr(e, "relation_state", {}) or {}),
                }
                for e in edges
                if e.source_node in selected_node_set and e.dest_node in selected_node_set
            ],
        }
