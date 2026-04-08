import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from gui.components.workspace_items import Node
from core.ai_fill_graph_worker import AIFillGraphWorker
from core.ai_consolidate_worker import AIConsolidateWorker


class WorkspaceAIEvidenceHandler:
    """Shared handler for fill-graph and consolidate-notes flows."""

    def __init__(self, view):
        self.view = view

    # ---- Fill graph -------------------------------------------------

    def trigger_fill_graph(self):
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

        if not target_nodes:
            QMessageBox.warning(
                v,
                "No Nodes",
                "Please add or select some nodes in the workspace first.",
            )
            return

        nodes_data = [
            {
                "id": n.node_id,
                "type": "user_created" if n.is_custom else "pdf_note",
                "text": f"{n.quote} \n {n.note}".strip(),
            }
            for n in target_nodes
        ]
        edges_data = [
            {
                "source_id": e.source_node.node_id,
                "target_id": e.dest_node.node_id,
                "label": e.label_text,
            }
            for e in v.edges
            if e.source_node in target_nodes and e.dest_node in target_nodes
        ]

        allowed_docs = v.get_allowed_docs()
        llm_manager = v.main_window.tabs["LLM Chat"].llm_manager

        v.loading_label.setText(
            "✨ AI is analyzing graph to find missing evidence...\nThis may take a moment."
        )
        v.loading_overlay.resize(v.viewport().size())
        v.loading_overlay.show()

        if getattr(v, "fill_worker", None) and v.fill_worker.isRunning():
            v.fill_worker.stop()
            v.fill_worker.wait()

        v.fill_worker = AIFillGraphWorker(
            llm_manager,
            model,
            nodes_data,
            edges_data,
            allowed_docs,
            parent=v,
        )
        v.fill_worker.progress.connect(
            v._update_loading_label,
            Qt.ConnectionType.QueuedConnection,
        )
        v.fill_worker.finished.connect(
            v._on_fill_graph_finished,
            Qt.ConnectionType.QueuedConnection,
        )

        v.main_window.thread_manager.register_worker("ai_fill_graph_worker", v.fill_worker)
        v.fill_worker.start()

    def update_loading_label(self, text: str):
        v = self.view
        v.loading_label.setText(text + "\nThis may take a moment.")

    def _on_fill_graph_finished(self, evidence_items, error_msg):
        v = self.view
        v.loading_overlay.hide()
        v.loading_label.setText(
            "✨ AI is analyzing and organizing your notes...\nThis may take a moment."
        )

        if error_msg:
            QMessageBox.warning(v, "Fill Graph Failed", error_msg)
            return

        if not evidence_items:
            QMessageBox.information(
                v,
                "No Evidence Found",
                "AI could not find or suggest new evidence for the selected graph.",
            )
            return

        v.save_state_for_undo()

        allowed_paths = (
            v.main_window.pdf_controller.get_pdf_paths()
            if v.main_window and hasattr(v.main_window, "pdf_controller")
            else []
        )
        added_count = 0
        new_annot_mappings = []

        for item in evidence_items:
            quote = item["quote"]
            note = item["note"]
            target_doc = item["doc"]
            target_node_id = item["target_node_id"]

            new_annot_id = f"AINote|{uuid.uuid4()}"

            success = v.main_window.add_ai_annotation(
                quote,
                note,
                target_doc_name=target_doc,
                allowed_paths=allowed_paths,
                forced_annot_id=new_annot_id,
                emit_signal=False,
            )
            if success:
                new_annot_mappings.append((new_annot_id, target_node_id))
                added_count += 1

        if added_count > 0:
            workspace_data = v.serialize_workspace()

            for new_annot_id, target_node_id in new_annot_mappings:
                workspace_data["edges"].append(
                    {
                        "id": str(uuid.uuid4()),
                        "source": target_node_id,
                        "target": new_annot_id,
                        "label": "AI Evidence",
                        "color": "#9c27b0",
                        "weight": 3,
                    }
                )

            if v.controller:
                v.controller.save_workspace_data(workspace_data)
            else:
                v.main_window.persistence_controller.save_workspace_data(workspace_data)
                v.main_window.persistence_controller.mark_dirty("workspace")

            all_annots = v.main_window.tabs["Notes"]._get_all_project_annotations_for_workspace()
            v.sync_with_project(workspace_data, all_annots)

            v.main_window.viewer.annot_manager.note_added.emit()
            QMessageBox.information(
                v,
                "Graph Filled",
                f"Successfully found and connected {added_count} piece(s) of evidence!",
            )
        else:
            QMessageBox.information(
                v,
                "Graph Filled",
                "Searched for evidence but could not successfully highlight valid quotes in the documents.",
            )

    # ---- Consolidate notes -----------------------------------------

    def trigger_consolidate_notes(self):
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

        if not target_nodes:
            QMessageBox.warning(
                v,
                "No Nodes",
                "Please add or select some nodes to consolidate.",
            )
            return

        nodes_data = [
            {
                "id": n.node_id,
                "type": "user_created" if n.is_custom else "pdf_note",
                "text": f"{n.quote} \n {n.note}".strip(),
            }
            for n in target_nodes
        ]
        edges_data = [
            {
                "source_id": e.source_node.node_id,
                "target_id": e.dest_node.node_id,
                "label": e.label_text,
            }
            for e in v.edges
            if e.source_node in target_nodes and e.dest_node in target_nodes
        ]

        llm_manager = v.main_window.tabs["LLM Chat"].llm_manager

        v.loading_label.setText(
            "✨ AI is consolidating and summarizing your argument map...\nThis may take a moment."
        )
        v.loading_overlay.resize(v.viewport().size())
        v.loading_overlay.show()

        if getattr(v, "consolidate_worker", None) and v.consolidate_worker.isRunning():
            v.consolidate_worker.stop()
            v.consolidate_worker.wait()

        v.consolidate_worker = AIConsolidateWorker(
            llm_manager,
            model,
            nodes_data,
            edges_data,
            parent=v,
        )
        v.consolidate_worker.finished.connect(
            v._on_consolidate_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        v.main_window.thread_manager.register_worker("ai_consolidate_worker", v.consolidate_worker)
        v.consolidate_worker.start()

    def _on_consolidate_finished(self, result_dict, error_msg):
        v = self.view
        v.loading_overlay.hide()
        v.loading_label.setText(
            "✨ AI is analyzing and organizing your notes...\nThis may take a moment."
        )

        if error_msg:
            QMessageBox.warning(v, "Consolidation Failed", error_msg)
            return

        summary = result_dict.get("summary")
        cluster_summaries = result_dict.get("cluster_summaries", [])

        if not summary and not cluster_summaries:
            QMessageBox.information(
                v,
                "No Consolidation",
                "AI could not generate a meaningful consolidation for these notes.",
            )
            return

        v.main_window.tabs["LLM Chat"].display_consolidation_results(summary, cluster_summaries)

