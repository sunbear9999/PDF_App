from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from gui.components.workspace_items import Node, Edge
from core.ai_connections_worker import AIFindConnectionsWorker


class WorkspaceAIFindConnectionsHandler:
    def __init__(self, view):
        self.view = view

    def trigger_find_connections(self):
        v = self.view
        if not v.loading_overlay.isHidden():
            return

        model = v.main_window.tabs["LLM Chat"].model_combo.currentText().strip()
        if not model or "Error" in model or "running" in model:
            QMessageBox.warning(
                v,
                "No Model Selected",
                "Please select a valid AI model in the LLM Chat tab first.",
            )
            return

        selected_nodes = [n for n in v.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in v.nodes.values() if n.isVisible()]

        if len(target_nodes) < 2:
            QMessageBox.warning(
                v,
                "Not Enough Nodes",
                "Please select at least 2 nodes to find connections between.",
            )
            return

        nodes_data = [{"id": n.node_id, "text": f"{n.quote} \n {n.note}".strip()} for n in target_nodes]
        edges_data = [
            {"source_id": e.source_node.node_id, "target_id": e.dest_node.node_id}
            for e in v.edges
            if e.source_node in target_nodes and e.dest_node in target_nodes
        ]

        llm_manager = v.main_window.tabs["LLM Chat"].llm_manager

        v.loading_label.setText(
            "✨ AI is analyzing relationships and finding new connections...\nThis may take a moment."
        )
        v.loading_overlay.resize(v.viewport().size())
        v.loading_overlay.show()

        if getattr(v, "conn_worker", None) and v.conn_worker.isRunning():
            v.conn_worker.stop()
            v.conn_worker.wait()

        v.conn_worker = AIFindConnectionsWorker(
            llm_manager,
            model,
            nodes_data,
            edges_data,
            parent=v,
        )
        v.conn_worker.finished.connect(
            v._on_find_connections_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        v.main_window.thread_manager.register_worker("ai_connections_worker", v.conn_worker)
        v.conn_worker.start()

    def _on_find_connections_finished(self, new_connections, error_msg):
        v = self.view
        v.loading_overlay.hide()
        v.loading_label.setText(
            "✨ AI is analyzing and organizing your notes...\nThis may take a moment."
        )

        if error_msg:
            QMessageBox.warning(v, "AI Connection Failed", error_msg)
            return

        if not new_connections:
            QMessageBox.information(
                v,
                "No Connections Found",
                "The AI did not find any strong new connections between these nodes.",
            )
            return

        v.save_state_for_undo()

        added_count = 0
        for conn in new_connections:
            src_id = conn.get("source_id")
            tgt_id = conn.get("target_id")

            if src_id in v.nodes and tgt_id in v.nodes and src_id != tgt_id:
                src_node = v.nodes[src_id]
                tgt_node = v.nodes[tgt_id]

                exists = False
                for existing_edge in v.edges:
                    if (
                        existing_edge.source_node == src_node
                        and existing_edge.dest_node == tgt_node
                    ) or (
                        existing_edge.source_node == tgt_node
                        and existing_edge.dest_node == src_node
                    ):
                        exists = True
                        break

                if not exists:
                    label = conn.get("label", "AI Connection")
                    weight = max(1, min(10, int(conn.get("weight", 3))))

                    edge = Edge(src_node, tgt_node, label, color="#9c27b0", weight=weight)
                    v.scene_obj.addItem(edge)
                    v.edges.append(edge)
                    added_count += 1

        if added_count > 0:
            if v.controller:
                v.controller.mark_dirty("workspace")
            else:
                v.main_window.persistence_controller.mark_dirty("workspace")
        else:
            QMessageBox.information(
                v,
                "No Connections Added",
                "The AI suggested connections that already existed.",
            )

