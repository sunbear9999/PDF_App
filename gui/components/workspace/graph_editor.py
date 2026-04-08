from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPen, QImage, QPainter
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QInputDialog

from gui.components.workspace_items import Node, Edge


class WorkspaceGraphEditor:
    """Graph editing utilities for WorkspaceView."""

    def __init__(self, view):
        self.view = view

    @property
    def v(self):
        return self.view

    def export_workspace(self):
        v = self.v

        selected_nodes = [n for n in v.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in v.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.information(v, "Export", "Nothing to export! Ensure nodes are visible.")
            return

        target_edges = [e for e in v.edges if e.source_node in target_nodes and e.dest_node in target_nodes]

        visibility_states = {}
        for item in v.scene_obj.items():
            if item.parentItem() is None:
                visibility_states[item] = item.isVisible()
                if item not in target_nodes and item not in target_edges:
                    item.setVisible(False)

        original_selection = v.scene_obj.selectedItems()
        v.scene_obj.clearSelection()

        for node in target_nodes:
            if hasattr(node, "proxy_toolbar"):
                node.proxy_toolbar.hide()
            if hasattr(node, "resize_handle"):
                node.resize_handle.hide()

        bounding_rect = QRectF()
        for item in target_nodes + target_edges:
            bounding_rect = bounding_rect.united(item.sceneBoundingRect())

        padding = 40
        bounding_rect.adjust(-padding, -padding, padding, padding)

        file_path, _ = QFileDialog.getSaveFileName(
            v,
            "Export Workspace",
            "workspace_export.png",
            "PNG Image (*.png);;JPEG Image (*.jpg)",
        )

        if file_path:
            image = QImage(
                int(bounding_rect.width()),
                int(bounding_rect.height()),
                QImage.Format.Format_ARGB32,
            )

            theme = (
                v.main_window.theme_manager.get_theme()
                if hasattr(v.main_window, "theme_manager")
                else {"canvas": "#1a1a1a"}
            )
            image.fill(QColor(theme["canvas"]))

            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            v.scene_obj.render(painter, QRectF(image.rect()), bounding_rect)
            painter.end()

            image.save(file_path)
            QMessageBox.information(
                v,
                "Export Successful",
                f"Workspace exported successfully to:\n{file_path}",
            )

        for item, was_visible in visibility_states.items():
            item.setVisible(was_visible)

        for item in original_selection:
            item.setSelected(True)

        for node in target_nodes:
            node.refresh_layout()

    def delete_edge(self, edge):
        v = self.v

        if edge in edge.source_node.edges:
            edge.source_node.edges.remove(edge)
        if edge in edge.dest_node.edges:
            edge.dest_node.edges.remove(edge)

        v.scene_obj.removeItem(edge)
        if edge in v.edges:
            v.edges.remove(edge)

        if v.controller:
            v.controller.mark_dirty("workspace")
        else:
            v.main_window.persistence_controller.mark_dirty("workspace")

    def delete_node(self, node):
        v = self.v

        for edge in list(node.edges):
            self.delete_edge(edge)

        v.scene_obj.removeItem(node)

        if node.node_id in v.nodes:
            del v.nodes[node.node_id]

        if not node.is_custom and node.pdf_path is not None:
            v.main_window.tabs["Notes"].save_workspace_state()
            v.main_window.tabs["Notes"].delete_note(node.pdf_path, node.page_num, node.node_id)

        if v.controller:
            v.controller.mark_dirty("workspace")
        else:
            v.main_window.persistence_controller.mark_dirty("workspace")

    def start_connection(self, node):
        v = self.v
        v.connecting_node = node
        v.connecting_node.setPen(QPen(QColor("#00ff00"), 3, Qt.PenStyle.DashLine))

    def finish_connection(self, target_node):
        v = self.v

        text, ok = QInputDialog.getText(
            v,
            "Connection Label",
            "Enter text for connection:",
        )

        if ok:
            edge = Edge(v.connecting_node, target_node, text)
            v.scene_obj.addItem(edge)
            v.edges.append(edge)

            if v.controller:
                v.controller.mark_dirty("workspace")
            else:
                v.main_window.persistence_controller.mark_dirty("workspace")

        if v.connecting_node.isSelected():
            v.connecting_node.setPen(QPen(QColor("#ffffff"), 4))
        else:
            v.connecting_node.setPen(QPen(QColor("#555555"), 2))

        v.connecting_node = None

