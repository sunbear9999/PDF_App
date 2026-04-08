import json

from gui.components.workspace_items import Node, Edge
from models.workspace_models import NodeData, EdgeData


class WorkspaceStateManager:
    """
    Undo/redo stack + workspace serialization/deserialization.

    Extracted from WorkspaceView to keep the GUI class focused on rendering and interaction.
    """

    def __init__(self, view):
        self.view = view

    @property
    def v(self):
        return self.view

    def _update_buttons(self):
        v = self.v
        if "Notes" in v.main_window.tabs:
            v.main_window.tabs["Notes"].update_undo_redo_buttons()

    def serialize_workspace(self):
        v = self.v
        data = {"nodes": {}, "edges": []}

        for n_id, node in v.nodes.items():
            node_data = NodeData(
                node_id=n_id,
                quote=node.quote,
                note=node.note,
                color=node.color,
                is_custom=node.is_custom,
                pdf_path=node.pdf_path,
                page_num=node.page_num,
                manual_font_size=node.manual_font_size,
                x=node.pos().x(),
                y=node.pos().y(),
                width=node.base_width,
                height=node.base_height,
            )
            data["nodes"][n_id] = node_data.to_dict()

        for edge in v.edges:
            edge_data = EdgeData(
                edge_id=edge.edge_id,
                source=edge.source_node.node_id,
                target=edge.dest_node.node_id,
                label=edge.label_text,
                color=edge.base_color.name(),
                weight=edge.weight,
            )
            data["edges"].append(edge_data.to_dict())

        return data

    def load_workspace_state(self, state_data):
        v = self.v
        v.scene_obj.clear()
        v.nodes.clear()
        v.edges.clear()

        for n_id, data in state_data.get("nodes", {}).items():
            node_data = NodeData.from_dict({**data, "node_id": n_id})
            node = Node(node_data=node_data)
            node.setPos(node_data.x, node_data.y)
            v.scene_obj.addItem(node)
            v.nodes[n_id] = node

        for edge_dict in state_data.get("edges", []):
            if edge_dict["source"] in v.nodes and edge_dict["target"] in v.nodes:
                src = v.nodes[edge_dict["source"]]
                tgt = v.nodes[edge_dict["target"]]
                edge_data = EdgeData.from_dict(edge_dict)
                edge = Edge(src, tgt, edge_data=edge_data)
                v.scene_obj.addItem(edge)
                v.edges.append(edge)

        v._apply_filter()

    def save_state_for_undo(self):
        v = self.v
        if v.is_restoring:
            return

        state = self.serialize_workspace()
        state_str = json.dumps(state, sort_keys=True)

        if not v.undo_stack or v.undo_stack[-1][0] != state_str:
            v.undo_stack.append((state_str, state))
            if len(v.undo_stack) > 50:
                v.undo_stack.pop(0)
            v.redo_stack.clear()
            self._update_buttons()

    def undo(self):
        v = self.v
        if not v.undo_stack:
            return

        v.is_restoring = True
        current_state = self.serialize_workspace()
        current_str = json.dumps(current_state, sort_keys=True)
        v.redo_stack.append((current_str, current_state))

        _, prev_state = v.undo_stack.pop()
        self.load_workspace_state(prev_state)

        v.is_restoring = False
        self._update_buttons()
        if v.controller:
            v.controller.mark_dirty("workspace")
        else:
            v.main_window.persistence_controller.mark_dirty("workspace")

    def redo(self):
        v = self.v
        if not v.redo_stack:
            return

        v.is_restoring = True
        current_state = self.serialize_workspace()
        current_str = json.dumps(current_state, sort_keys=True)
        v.undo_stack.append((current_str, current_state))

        _, next_state = v.redo_stack.pop()
        self.load_workspace_state(next_state)

        v.is_restoring = False
        self._update_buttons()
        if v.controller:
            v.controller.mark_dirty("workspace")
        else:
            v.main_window.persistence_controller.mark_dirty("workspace")

