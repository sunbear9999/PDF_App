import uuid

from gui.components.workspace_items import Node, Edge
from models.workspace_models import NodeData, EdgeData


class WorkspaceProjectSync:
    """Sync workspace graph from persisted state + PDF annotations."""

    def __init__(self, view):
        self.view = view

    @property
    def v(self):
        return self.view

    def add_custom_bubble(self):
        v = self.v
        v.save_state_for_undo()

        node_id = f"custom_{uuid.uuid4()}"
        node = Node(
            node_id,
            quote="",
            note="",
            color="#005577",
            is_custom=True,
            width=180,
            height=80,
        )

        view_center = v.mapToScene(v.viewport().rect().center())
        node.setPos(view_center)

        v.scene_obj.addItem(node)
        v.nodes[node_id] = node

        if v.controller:
            v.controller.mark_dirty("workspace")
        else:
            v.main_window.persistence_controller.mark_dirty("workspace")

        v.scene_obj.clearSelection()
        node.setSelected(True)

        node.is_hovered = True
        node.refresh_layout()
        node.trigger_edit()

    def sync_with_project(self, workspace_data, pdf_annotations):
        v = self.v

        selected_ids = [n_id for n_id, n in v.nodes.items() if n.isSelected()]

        v.scene_obj.clear()
        v.nodes.clear()
        v.edges.clear()

        annot_dict = {a["id"]: a for a in pdf_annotations}

        saved_nodes = workspace_data.get("nodes", {})
        for n_id, data in saved_nodes.items():
            quote = data.get("quote", "")
            note = data.get("note", "")

            if n_id in annot_dict:
                quote = annot_dict[n_id]["subject"] or ""
                note = annot_dict[n_id]["content"] or ""

            node_data = NodeData.from_dict({**data, "node_id": n_id})
            node = Node(node_data=node_data)
            node.setPos(node_data.x, node_data.y)
            v.scene_obj.addItem(node)
            v.nodes[n_id] = node

        y_offset = 50
        for annot in pdf_annotations:
            if annot["id"] not in v.nodes:
                quote = annot["subject"] or ""
                note = annot["content"] or ""

                l = len(note + quote)
                w = 200 if l < 50 else (250 if l < 150 else 300)
                h = 70 if l < 50 else (110 if l < 150 else 160)

                color = "#2d2238" if annot["id"].startswith("AINote") else "#2b2b2b"

                node = Node(
                    annot["id"],
                    quote,
                    note,
                    color=color,
                    is_custom=False,
                    width=w,
                    height=h,
                    pdf_path=annot["pdf_path"],
                    page_num=annot["page_num"],
                )
                node.setPos(50, y_offset)
                y_offset += 100
                v.scene_obj.addItem(node)
                v.nodes[annot["id"]] = node

        for edge_dict in workspace_data.get("edges", []):
            if edge_dict["source"] in v.nodes and edge_dict["target"] in v.nodes:
                src = v.nodes[edge_dict["source"]]
                tgt = v.nodes[edge_dict["target"]]
                edge_data = EdgeData.from_dict(edge_dict)
                edge = Edge(src, tgt, edge_data=edge_data)
                v.scene_obj.addItem(edge)
                v.edges.append(edge)

        for n_id in selected_ids:
            if n_id in v.nodes:
                v.nodes[n_id].setSelected(True)

        v._refresh_pdf_list()
        v._apply_filter()

        if v.nodes:
            items_rect = v.scene_obj.itemsBoundingRect()
            v.centerOn(items_rect.center())

