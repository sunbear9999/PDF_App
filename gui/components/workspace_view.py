# gui/components/workspace_view.py
import uuid
import json
from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QMenu, QMessageBox, 
                             QInputDialog, QFrame, QLabel, QVBoxLayout)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainter

from gui.components.workspace_items import Node, Edge
from core.ai_organize_worker import AIOrganizeWorker

class WorkspaceView(QGraphicsView):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.scene_obj.view = self 
        
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setBackgroundBrush(QBrush(QColor("#1a1a1a")))
        
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        
        self.nodes = {}
        self.edges = []
        self.connecting_node = None
        self.worker = None

        self.undo_stack = []
        self.redo_stack = []
        self.is_restoring = False

        # --- High Visibility AI Loading Overlay ---
        self.loading_overlay = QFrame(self)
        self.loading_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 210); border-radius: 8px;")
        self.loading_overlay.hide()
        
        overlay_layout = QVBoxLayout(self.loading_overlay)
        self.loading_label = QLabel("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")
        self.loading_label.setStyleSheet("color: #00cc66; font-size: 26px; font-weight: bold; background: transparent;")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(self.loading_label)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Ensure the overlay always covers the full view when resized
        if hasattr(self, 'loading_overlay') and not self.loading_overlay.isHidden():
            self.loading_overlay.resize(self.viewport().size())

    def save_state_for_undo(self):
        if self.is_restoring: return
        state = self.serialize_workspace()
        state_str = json.dumps(state, sort_keys=True)
        
        if not self.undo_stack or self.undo_stack[-1][0] != state_str:
            self.undo_stack.append((state_str, state))
            if len(self.undo_stack) > 50:
                self.undo_stack.pop(0)
            self.redo_stack.clear()
            self._update_buttons()

    def _update_buttons(self):
        if "Notes" in self.main_window.tabs:
            self.main_window.tabs["Notes"].update_undo_redo_buttons()

    def undo(self):
        if not self.undo_stack: return
        self.is_restoring = True
        current_state = self.serialize_workspace()
        current_str = json.dumps(current_state, sort_keys=True)
        self.redo_stack.append((current_str, current_state))
        
        _, prev_state = self.undo_stack.pop()
        self.load_workspace_state(prev_state)
        
        self.is_restoring = False
        self._update_buttons()
        self.main_window.project_manager.mark_dirty("workspace")

    def redo(self):
        if not self.redo_stack: return
        self.is_restoring = True
        current_state = self.serialize_workspace()
        current_str = json.dumps(current_state, sort_keys=True)
        self.undo_stack.append((current_str, current_state))
        
        _, next_state = self.redo_stack.pop()
        self.load_workspace_state(next_state)
        
        self.is_restoring = False
        self._update_buttons()
        self.main_window.project_manager.mark_dirty("workspace")

    def load_workspace_state(self, state_data):
        self.scene_obj.clear()
        self.nodes.clear()
        self.edges.clear()
        
        for n_id, data in state_data.get("nodes", {}).items():
            node = Node(n_id, data["quote"], data["note"], data["color"], data["is_custom"], 
                        data["width"], data["height"], data.get("pdf_path"), data.get("page_num"), data.get("manual_font_size"))
            node.setPos(data["x"], data["y"])
            self.scene_obj.addItem(node)
            self.nodes[n_id] = node
            
        for edge_data in state_data.get("edges", []):
            if edge_data["source"] in self.nodes and edge_data["target"] in self.nodes:
                src = self.nodes[edge_data["source"]]
                tgt = self.nodes[edge_data["target"]]
                edge = Edge(src, tgt, edge_data["label"], edge_data["id"], edge_data.get("color", "#888888"))
                self.scene_obj.addItem(edge)
                self.edges.append(edge)

    def keyPressEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Z:
                self.undo()
                return
            elif event.key() == Qt.Key.Key_Y:
                self.redo()
                return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier):
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
        else:
            super().wheelEvent(event)

    def zoom_in(self):
        self.scale(1.15, 1.15)
        
    def zoom_out(self):
        self.scale(1 / 1.15, 1 / 1.15)

    def mousePressEvent(self, event):
        if self.connecting_node:
            item = self.itemAt(event.pos())
            is_node = False
            current = item
            while current:
                if isinstance(current, Node):
                    is_node = True
                    break
                current = current.parentItem()
                
            if not is_node:
                self.connecting_node.setPen(QPen(QColor("#555555"), 2))
                self.connecting_node = None
                event.accept()
                return

        if event.button() == Qt.MouseButton.LeftButton and event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            item = self.itemAt(event.pos())
            while item and not isinstance(item, (Node, Edge)):
                item = item.parentItem()
            if isinstance(item, Node):
                item.setSelected(not item.isSelected())
                event.accept()
                return
        elif event.button() == Qt.MouseButton.LeftButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.LeftButton and event.modifiers() == Qt.KeyboardModifier.AltModifier):
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def delete_edge(self, edge):
        if edge in edge.source_node.edges:
            edge.source_node.edges.remove(edge)
        if edge in edge.dest_node.edges:
            edge.dest_node.edges.remove(edge)
            
        self.scene_obj.removeItem(edge)
        if edge in self.edges:
            self.edges.remove(edge)
            
        self.main_window.project_manager.mark_dirty("workspace")

    def delete_node(self, node):
        for edge in list(node.edges):
            self.delete_edge(edge)
            
        self.scene_obj.removeItem(node)
        if node.node_id in self.nodes:
            del self.nodes[node.node_id]
            
        if not node.is_custom and node.pdf_path is not None:
            self.main_window.tabs["Notes"].save_workspace_state()
            self.main_window.tabs["Notes"].delete_note(node.pdf_path, node.page_num, node.node_id)
            
        self.main_window.project_manager.mark_dirty("workspace")

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        
        while item and not isinstance(item, (Node, Edge)):
            item = item.parentItem()

        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]

        if len(selected_nodes) > 1 and isinstance(item, Node) and item in selected_nodes:
            menu = QMenu()
            ai_action = menu.addAction("✨ AI Organize Selected")
            del_action = menu.addAction("🗑️ Delete Selected Nodes")
            
            action = menu.exec(event.globalPos())
            if action == ai_action:
                self.trigger_ai_organize(selected_nodes)
            elif action == del_action:
                self.save_state_for_undo()
                for n in selected_nodes:
                    self.delete_node(n)
            return

        if isinstance(item, Node):
            menu = QMenu()
            edit_action = menu.addAction("✏️ Edit Note Text")
            color_action = menu.addAction("🎨 Change Color")
            del_action = menu.addAction("🗑️ Delete Note")
            
            action = menu.exec(event.globalPos())
            if action == edit_action:
                item.trigger_edit()
            elif action == color_action:
                item.trigger_color_change()
            elif action == del_action:
                self.save_state_for_undo()
                self.delete_node(item)
            return
            
        if isinstance(item, Edge):
            menu = QMenu()
            edit_action = menu.addAction("✏️ Edit Connection Text")
            color_action = menu.addAction("🎨 Change Line Color")
            del_action = menu.addAction("🗑️ Delete Connection")
            
            action = menu.exec(event.globalPos())
            if action == edit_action:
                text, ok = QInputDialog.getText(self, "Edit Label", "Enter new text:", text=item.label_text)
                if ok:
                    self.save_state_for_undo()
                    item.label_text = text
                    item.text_item.setPlainText(text)
                    item.update_position()
                    self.main_window.project_manager.mark_dirty("workspace")
            elif action == color_action:
                color = QColorDialog.getColor(item.base_color)
                if color.isValid():
                    self.save_state_for_undo()
                    item.base_color = color
                    item.setPen(QPen(item.base_color, 4 if item.isSelected() else 2, Qt.PenStyle.SolidLine))
                    self.main_window.project_manager.mark_dirty("workspace")
            elif action == del_action:
                self.save_state_for_undo()
                self.delete_edge(item)
            return

        super().contextMenuEvent(event)

    def trigger_ai_organize(self, selected_nodes):
        if not self.loading_overlay.isHidden(): return

        # Validate that a valid model is selected before starting
        model = self.main_window.tabs["LLM Chat"].model_combo.currentText().strip()
        if not model or "Error" in model or "running" in model:
            QMessageBox.warning(self, "No Model Selected", "Please select a valid AI model in the LLM Chat tab first.")
            return

        instructions, ok = QInputDialog.getText(
            self, 
            "AI Organize Options", 
            "Enter custom organization instructions (e.g., 'Group by Timeline' or 'Pros vs Cons'):\nLeave blank for default semantic grouping."
        )
        if not ok: return
        
        # ... (rest of the function continues normally)

        nodes_data = [{"id": n.node_id, "text": n.note or n.quote} for n in selected_nodes]
        llm_manager = self.main_window.tabs["LLM Chat"].llm_manager

        # Show the prominent loading overlay
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()

        self.worker = AIOrganizeWorker(llm_manager, model, nodes_data, custom_instructions=instructions.strip())
        self.worker.finished.connect(self._on_ai_organize_finished)
        self.worker.start()

    def _on_ai_organize_finished(self, clusters, error_msg):
        self.loading_overlay.hide()

        if error_msg or not clusters:
            QMessageBox.warning(self, "AI Organize Failed", error_msg)
            return

        self.save_state_for_undo()
        
        try:
            # Bulletproof ID matching: Find nodes by the exact IDs returned by the AI.
            # This prevents silent failures if you deselect the nodes while waiting
            # or if taking a new PDF note re-rendered the scene mid-generation.
            processed_ids = []
            for cluster in clusters:
                processed_ids.extend(cluster.get("node_ids", []))
                
            selected_nodes = [self.nodes[nid] for nid in processed_ids if nid in self.nodes]
            if not selected_nodes: return

            avg_x = sum(n.pos().x() for n in selected_nodes) / len(selected_nodes)
            avg_y = sum(n.pos().y() for n in selected_nodes) / len(selected_nodes)

            start_x = avg_x - (len(clusters) * 125)
            current_x = start_x
            start_y = avg_y - 150

            for cluster in clusters:
                c_name = cluster.get("cluster_name", "Cluster")
                n_ids = cluster.get("node_ids", [])
                if not n_ids: continue

                cluster_node_id = f"custom_{uuid.uuid4()}"
                cluster_node = Node(cluster_node_id, quote="", note=c_name, color="#0078D7", is_custom=True, width=180, height=60)
                cluster_node.setPos(current_x, start_y)
                self.scene_obj.addItem(cluster_node)
                self.nodes[cluster_node_id] = cluster_node

                child_y = start_y + 120
                for nid in n_ids:
                    if nid in self.nodes:
                        child = self.nodes[nid]
                        child.setPos(current_x, child_y)
                        child_y += child.base_height + 25
                        
                        edge = Edge(cluster_node, child, "")
                        self.scene_obj.addItem(edge)
                        self.edges.append(edge)
                        
                        child.setSelected(False) 

            current_x += 280

            self.main_window.project_manager.mark_dirty("workspace")
        except Exception as e:
            QMessageBox.warning(self, "Layout Error", str(e))

    def start_connection(self, node):
        self.connecting_node = node
        self.connecting_node.setPen(QPen(QColor("#00ff00"), 3, Qt.PenStyle.DashLine))

    def finish_connection(self, target_node):
        text, ok = QInputDialog.getText(self, "Connection Label", "Enter text for connection:")
        if ok:
            edge = Edge(self.connecting_node, target_node, text)
            self.scene_obj.addItem(edge)
            self.edges.append(edge)
            self.main_window.project_manager.mark_dirty("workspace")
            
        self.connecting_node.setPen(QPen(QColor("#555555"), 2))
        self.connecting_node = None

    def add_custom_bubble(self):
        self.save_state_for_undo()
        
        node_id = f"custom_{uuid.uuid4()}"
        node = Node(node_id, quote="", note="", color="#005577", is_custom=True, width=180, height=80)
        
        view_center = self.mapToScene(self.viewport().rect().center())
        node.setPos(view_center)
        
        self.scene_obj.addItem(node)
        self.nodes[node_id] = node
        self.main_window.project_manager.mark_dirty("workspace")
        
        node.hoverEnterEvent(None)
        node.trigger_edit()

    def sync_with_project(self, workspace_data, pdf_annotations):
        # 1. Archive which nodes the user is currently holding/selecting
        selected_ids = [n_id for n_id, n in self.nodes.items() if n.isSelected()]

        self.scene_obj.clear()
        self.nodes.clear()
        self.edges.clear()

        annot_dict = {a["id"]: a for a in pdf_annotations}

        saved_nodes = workspace_data.get("nodes", {})
        for n_id, data in saved_nodes.items():
            quote = data.get("quote", "")
            note = data.get("note", "")

            if n_id in annot_dict:
                quote = annot_dict[n_id]["subject"] or ""
                note = annot_dict[n_id]["content"] or ""

            node = Node(n_id, quote, note, data["color"], data["is_custom"], 
                        data["width"], data["height"], data.get("pdf_path"), data.get("page_num"), data.get("manual_font_size"))
            node.setPos(data["x"], data["y"])
            self.scene_obj.addItem(node)
            self.nodes[n_id] = node

        y_offset = 50
        for annot in pdf_annotations:
            if annot["id"] not in self.nodes:
                quote = annot["subject"] or ""
                note = annot["content"] or ""
                
                l = len(note + quote)
                w = 200 if l < 50 else (250 if l < 150 else 300)
                h = 70 if l < 50 else (110 if l < 150 else 160)
                
                color = "#2d2238" if annot["id"].startswith("AINote") else "#2b2b2b"
                
                node = Node(annot["id"], quote, note, color=color, is_custom=False, 
                            width=w, height=h, pdf_path=annot["pdf_path"], page_num=annot["page_num"])
                node.setPos(50, y_offset)
                y_offset += 100
                self.scene_obj.addItem(node)
                self.nodes[annot["id"]] = node

        for edge_data in workspace_data.get("edges", []):
            if edge_data["source"] in self.nodes and edge_data["target"] in self.nodes:
                src = self.nodes[edge_data["source"]]
                tgt = self.nodes[edge_data["target"]]
                edge = Edge(src, tgt, edge_data["label"], edge_data["id"], edge_data.get("color", "#888888"))
                self.scene_obj.addItem(edge)
                self.edges.append(edge)

        # 2. Resurrect the selection state
        for n_id in selected_ids:
            if n_id in self.nodes:
                self.nodes[n_id].setSelected(True)

    def serialize_workspace(self):
        data = {"nodes": {}, "edges": []}
        for n_id, node in self.nodes.items():
            data["nodes"][n_id] = {
                "quote": node.quote,
                "note": node.note,
                "color": node.color,
                "is_custom": node.is_custom,
                "pdf_path": node.pdf_path,
                "page_num": node.page_num,
                "manual_font_size": node.manual_font_size,
                "x": node.pos().x(),
                "y": node.pos().y(),
                "width": node.base_width,
                "height": node.base_height
            }
        for edge in self.edges:
            data["edges"].append({
                "id": edge.edge_id,
                "source": edge.source_node.node_id,
                "target": edge.dest_node.node_id,
                "label": edge.label_text,
                "color": edge.base_color.name()
            })
        return data