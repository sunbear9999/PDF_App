import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QInputDialog

from gui.components.workspace_items import Node, Edge
from core.ai_organize_worker import AIOrganizeWorker


class WorkspaceAIOrganizeHandler:
    def __init__(self, view):
        self.view = view

    def trigger_ai_organize(self, selected_nodes):
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

        instructions, ok = QInputDialog.getText(
            v,
            "AI Organize Options",
            "Enter custom organization instructions (e.g., 'Group by Timeline' or 'Pros vs Cons'):\nLeave blank for default semantic grouping.",
        )
        if not ok:
            return

        nodes_data = [{"id": n.node_id, "text": n.note or n.quote} for n in selected_nodes]
        llm_manager = v.main_window.tabs["LLM Chat"].llm_manager

        v.loading_label.setText(
            "✨ AI is analyzing and organizing your notes...\nThis may take a moment."
        )
        v.loading_overlay.resize(v.viewport().size())
        v.loading_overlay.show()

        if getattr(v, "worker", None) and v.worker.isRunning():
            v.worker.stop()
            v.worker.wait()

        v.worker = AIOrganizeWorker(
            llm_manager,
            model,
            nodes_data,
            custom_instructions=instructions.strip(),
            parent=v,
        )
        v.worker.finished.connect(
            v._on_ai_organize_finished,
            Qt.ConnectionType.QueuedConnection,
        )

        v.main_window.thread_manager.register_worker("ai_organize_worker", v.worker)
        v.worker.start()

    def _on_ai_organize_finished(self, clusters, error_msg):
        v = self.view
        v.loading_overlay.hide()

        if error_msg or not clusters:
            QMessageBox.warning(v, "AI Organize Failed", error_msg)
            return

        v.save_state_for_undo()

        try:
            processed_ids = []
            for cluster in clusters:
                processed_ids.extend(cluster.get("node_ids", []))

            selected_nodes = [v.nodes[nid] for nid in processed_ids if nid in v.nodes]
            if not selected_nodes:
                return

            avg_x = sum(n.pos().x() for n in selected_nodes) / len(selected_nodes)
            avg_y = sum(n.pos().y() for n in selected_nodes) / len(selected_nodes)

            start_x = avg_x - (len(clusters) * 125)
            current_x = start_x
            start_y = avg_y - 150

            for cluster in clusters:
                c_name = cluster.get("cluster_name", "Cluster")
                n_ids = cluster.get("node_ids", [])
                if not n_ids:
                    continue

                cluster_node_id = f"custom_{uuid.uuid4()}"
                cluster_node = Node(
                    cluster_node_id,
                    quote="",
                    note=c_name,
                    color="#0078D7",
                    is_custom=True,
                    width=180,
                    height=60,
                )
                cluster_node.setPos(current_x, start_y)
                v.scene_obj.addItem(cluster_node)
                v.nodes[cluster_node_id] = cluster_node

                child_y = start_y + 120
                for nid in n_ids:
                    if nid in v.nodes:
                        child = v.nodes[nid]
                        child.setPos(current_x, child_y)
                        child_y += child.base_height + 25

                        edge = Edge(cluster_node, child, "")
                        v.scene_obj.addItem(edge)
                        v.edges.append(edge)

                        child.setSelected(False)

                current_x += 280

            if v.controller:
                v.controller.mark_dirty("workspace")
            else:
                v.main_window.persistence_controller.mark_dirty("workspace")
        except Exception as e:
            QMessageBox.warning(v, "Layout Error", str(e))

