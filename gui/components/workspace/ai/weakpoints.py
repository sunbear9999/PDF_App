from PyQt6.QtWidgets import QMessageBox

from gui.components.workspace_items import Node
from gui.components.workspace.dialogs import WeakpointsDialog
from core.ai_weakpoints_worker import AIWeakpointsWorker


class WorkspaceAIWeakpointsHandler:
    def __init__(self, view):
        self.view = view

    def trigger_identify_weakpoints(self):
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
                "Please add or select some notes in the workspace to evaluate.",
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
            "✨ AI is evaluating argument strength and identifying weak points...\nThis may take a moment."
        )
        v.loading_overlay.resize(v.viewport().size())
        v.loading_overlay.show()

        if getattr(v, "weakpoints_worker", None) and v.weakpoints_worker.isRunning():
            v.weakpoints_worker.stop()
            v.weakpoints_worker.wait()

        v.weakpoints_worker = AIWeakpointsWorker(
            llm_manager,
            model,
            nodes_data,
            edges_data,
            parent=v,
        )
        v.weakpoints_worker.finished.connect(v._on_identify_weakpoints_finished)
        v.weakpoints_worker.start()

    def _on_identify_weakpoints_finished(self, analysis_text, error_msg):
        v = self.view
        v.loading_overlay.hide()
        v.loading_label.setText(
            "✨ AI is analyzing and organizing your notes...\nThis may take a moment."
        )

        if error_msg:
            QMessageBox.warning(v, "Analysis Failed", error_msg)
            return

        dialog = WeakpointsDialog(analysis_text, v)

        if hasattr(v.main_window, "theme_manager"):
            theme = v.main_window.theme_manager.get_theme()
            dialog.setStyleSheet(
                f"background-color: {theme['bg_main']}; color: {theme['text_main']};"
            )
            dialog.text_edit.setStyleSheet(
                f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};"
            )
            for btn in dialog.buttons:
                btn.setStyleSheet(
                    f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; "
                    f"border: 1px solid {theme['border']}; padding: 8px; border-radius: 4px; font-weight: bold;"
                )

            dialog.btn_save_node.setStyleSheet(
                f"background-color: {theme['accent']}; color: #ffffff; border: none; "
                f"padding: 8px; border-radius: 4px; font-weight: bold;"
            )

        dialog.exec()

