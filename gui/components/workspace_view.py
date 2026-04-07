# gui/components/workspace_view.py
import os
import uuid
import json
from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QMenu, QMessageBox, 
                             QInputDialog, QFrame, QLabel, QVBoxLayout,
                             QHBoxLayout, QComboBox, QPushButton, QDialog,
                             QScrollArea, QWidget, QFormLayout, QDialogButtonBox, 
                             QColorDialog, QFileDialog, QTextEdit)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QImage, QStandardItemModel, QStandardItem

from gui.components.workspace_items import Node, Edge
from core.ai_organize_worker import AIOrganizeWorker
from core.ai_connections_worker import AIFindConnectionsWorker
from core.ai_outline_worker import AIOutlineWorker
from core.ai_weakpoints_worker import AIWeakpointsWorker
from core.ai_fill_graph_worker import AIFillGraphWorker
from core.ai_consolidate_worker import AIConsolidateWorker
from core.layout_engine import calculate_radial_layout

class CheckableComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view().pressed.connect(self.handle_item_pressed)
        self._changed = False

    def handle_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        if not item: return

        clicked_data = item.data(Qt.ItemDataRole.UserRole)
        current_state = item.checkState()
        new_state = Qt.CheckState.Unchecked if current_state == Qt.CheckState.Checked else Qt.CheckState.Checked

        all_item = self.model().item(0) # 'All PDFs' is always at index 0

        # Block signals so we can update multiple checkboxes silently without firing the filter 50 times
        self.model().blockSignals(True)

        if clicked_data == "ALL":
            # If clicking ALL, force it to checked and uncheck everything else
            item.setCheckState(Qt.CheckState.Checked)
            for i in range(1, self.model().rowCount()):
                self.model().item(i).setCheckState(Qt.CheckState.Unchecked)
        else:
            item.setCheckState(new_state)

            if new_state == Qt.CheckState.Checked:
                # If transitioning from "ALL" to a specific PDF, clear everything else out for a fresh start
                if all_item and all_item.checkState() == Qt.CheckState.Checked:
                    all_item.setCheckState(Qt.CheckState.Unchecked)
                    for i in range(1, self.model().rowCount()):
                        other_item = self.model().item(i)
                        if other_item != item:
                            other_item.setCheckState(Qt.CheckState.Unchecked)
            else:
                # If we unchecked a specific PDF, verify if we need to auto-fallback to "ALL"
                any_checked = False
                for i in range(1, self.model().rowCount()):
                    if self.model().item(i).checkState() == Qt.CheckState.Checked:
                        any_checked = True
                        break
                if not any_checked and all_item:
                    all_item.setCheckState(Qt.CheckState.Checked)

        self._changed = True
        self.model().blockSignals(False)

        # Emit dataChanged once for the whole list to trigger the workspace filter and UI update instantly
        top_left = self.model().index(0, 0)
        bottom_right = self.model().index(self.model().rowCount() - 1, 0)
        self.model().dataChanged.emit(top_left, bottom_right)

    def hidePopup(self):
        if not self._changed:
            super().hidePopup()
        self._changed = False

    def addItem(self, text, userData=None, checked=False):
        # Force a QStandardItemModel if it isn't one already
        if not isinstance(self.model(), QStandardItemModel):
            self.setModel(QStandardItemModel(self))
            
        item = QStandardItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setData(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        item.setData(userData, Qt.ItemDataRole.UserRole)
        self.model().appendRow(item)

    def get_checked_items(self):
        checked = []
        model = self.model()
        if isinstance(model, QStandardItemModel):
            for i in range(model.rowCount()):
                item = model.item(i)
                if item and item.checkState() == Qt.CheckState.Checked:
                    checked.append(item.data(Qt.ItemDataRole.UserRole))
        return checked

    def clear(self):
        model = self.model()
        if isinstance(model, QStandardItemModel):
            model.clear()
            # CRITICAL FIX: model.clear() wipes columns. We must restore the column count so text renders!
            model.setColumnCount(1)
        else:
            super().clear()


class OutlineDialog(QDialog):
    def __init__(self, outline_text, workspace_view, parent=None):
        super().__init__(parent or workspace_view)
        self.workspace_view = workspace_view
        self.setWindowTitle("AI Generated Outline")
        self.setMinimumSize(600, 700)
        
        layout = QVBoxLayout(self)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(outline_text)
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        
        self.btn_save_txt = QPushButton("💾 Save as .txt")
        self.btn_save_txt.clicked.connect(self.save_as_txt)
        
        self.btn_save_node = QPushButton("📌 Save as Workspace Node")
        self.btn_save_node.clicked.connect(self.save_as_node)
        
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        
        self.buttons = [self.btn_save_txt, self.btn_save_node, self.btn_close]
        
        btn_layout.addWidget(self.btn_save_txt)
        btn_layout.addWidget(self.btn_save_node)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        
        layout.addLayout(btn_layout)
        
    def save_as_txt(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Outline", "outline.txt", "Text Files (*.txt)")
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(self.text_edit.toPlainText())
            QMessageBox.information(self, "Success", "Outline saved successfully!")
            
    def save_as_node(self):
        text = self.text_edit.toPlainText().strip()
        if not text: return
        
        self.workspace_view.save_state_for_undo()
        
        node_id = f"custom_{uuid.uuid4()}"
        node = Node(node_id, quote="", note=text, color="#1e4034", is_custom=True, width=350, height=250)
        
        view_center = self.workspace_view.mapToScene(self.workspace_view.viewport().rect().center())
        node.setPos(view_center)
        
        self.workspace_view.scene_obj.addItem(node)
        self.workspace_view.nodes[node_id] = node
        self.workspace_view.main_window.project_manager.mark_dirty("workspace")
        
        QMessageBox.information(self, "Success", "Outline added as a new node to the workspace!")
        self.accept()

class WeakpointsDialog(QDialog):
    def __init__(self, weakpoints_text, workspace_view, parent=None):
        super().__init__(parent or workspace_view)
        self.workspace_view = workspace_view
        self.setWindowTitle("AI Weakpoint Analysis")
        self.setMinimumSize(600, 700)
        
        layout = QVBoxLayout(self)
        
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(weakpoints_text)
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        
        self.btn_save_txt = QPushButton("💾 Save as .txt")
        self.btn_save_txt.clicked.connect(self.save_as_txt)
        
        self.btn_save_node = QPushButton("📌 Save as Workspace Node")
        self.btn_save_node.clicked.connect(self.save_as_node)
        
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        
        self.buttons = [self.btn_save_txt, self.btn_save_node, self.btn_close]
        
        btn_layout.addWidget(self.btn_save_txt)
        btn_layout.addWidget(self.btn_save_node)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        
        layout.addLayout(btn_layout)
        
    def save_as_txt(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Analysis", "weakpoints.txt", "Text Files (*.txt)")
        if file_path:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(self.text_edit.toPlainText())
            QMessageBox.information(self, "Success", "Analysis saved successfully!")
            
    def save_as_node(self):
        text = self.text_edit.toPlainText().strip()
        if not text: return
        
        self.workspace_view.save_state_for_undo()
        
        node_id = f"custom_{uuid.uuid4()}"
        node = Node(node_id, quote="", note=text, color="#4a0e28", is_custom=True, width=350, height=250)
        
        view_center = self.workspace_view.mapToScene(self.workspace_view.viewport().rect().center())
        node.setPos(view_center)
        
        self.workspace_view.scene_obj.addItem(node)
        self.workspace_view.nodes[node_id] = node
        self.workspace_view.main_window.project_manager.mark_dirty("workspace")
        
        QMessageBox.information(self, "Success", "Analysis added as a new node to the workspace!")
        self.accept()


class PDFColorDialog(QDialog):
    def __init__(self, pdfs, current_colors, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Organize by PDF Colors")
        self.setMinimumSize(350, 400)
        self.pdf_colors = dict(current_colors)
        
        layout = QVBoxLayout(self)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.form = QFormLayout(self.scroll_widget)
        
        self.buttons = {}
        for pdf in pdfs:
            btn = QPushButton()
            btn.setFixedSize(80, 30)
            color = self.pdf_colors.get(pdf, "#2b2b2b")
            btn.setStyleSheet(f"background-color: {color}; border: 1px solid #aaaaaa; border-radius: 4px;")
            btn.clicked.connect(lambda checked, p=pdf, b=btn: self.pick_color(p, b))
            self.buttons[pdf] = btn
            self.form.addRow(os.path.basename(pdf), btn)
            
        scroll.setWidget(self.scroll_widget)
        layout.addWidget(scroll)
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        
    def pick_color(self, pdf, btn):
        initial = QColor(self.pdf_colors.get(pdf, "#2b2b2b"))
        color = QColorDialog.getColor(initial, self, f"Select Color for {os.path.basename(pdf)}")
        if color.isValid():
            self.pdf_colors[pdf] = color.name()
            btn.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #aaaaaa; border-radius: 4px;")
            
    def get_colors(self):
        return self.pdf_colors


class WorkspaceView(QGraphicsView):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.scene_obj.view = self 
        
        self.scene_obj.setSceneRect(-100000, -100000, 200000, 200000)
        
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        
        self.nodes = {}
        self.edges = []
        self.connecting_node = None
        self.worker = None

        self.undo_stack = []
        self.redo_stack = []
        self.is_restoring = False

        self.loading_overlay = QFrame(self)
        self.loading_overlay.hide()
        
        overlay_layout = QVBoxLayout(self.loading_overlay)
        self.loading_label = QLabel("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(self.loading_label)

        self.toolbar_frame = QFrame(self)
        self.toolbar_frame.setObjectName("WorkspaceToolbar")
        tb_layout = QHBoxLayout(self.toolbar_frame)
        tb_layout.setContentsMargins(10, 8, 10, 8)
        
        tb_layout.addWidget(QLabel("Filter View:"))
        self.filter_combo = CheckableComboBox()
        self.filter_combo.model().dataChanged.connect(self._apply_filter)
        tb_layout.addWidget(self.filter_combo)
        
        self.btn_color_pdf = QPushButton("🎨 Color by PDF")
        self.btn_color_pdf.clicked.connect(self._open_color_by_pdf_dialog)
        tb_layout.addWidget(self.btn_color_pdf)

        self.btn_declutter = QPushButton("🧹 Declutter")
        self.btn_declutter.clicked.connect(self.trigger_declutter)
        tb_layout.addWidget(self.btn_declutter)

        self.btn_export = QPushButton("📸 Export Image")
        self.btn_export.clicked.connect(self._export_workspace)
        tb_layout.addWidget(self.btn_export)

        self.btn_ai_tools = QPushButton("🤖 AI Tools")
        self.ai_menu = self.create_ai_menu(self.btn_ai_tools)
        self.btn_ai_tools.setMenu(self.ai_menu)
        tb_layout.addWidget(self.btn_ai_tools)

    def create_ai_menu(self, parent_widget):
        menu = QMenu("🤖 AI Tools", parent_widget)
        # CRITICAL FIX: Ensure the submenu actively identifies itself with the correct title when nested
        menu.setTitle("🤖 AI Tools") 
        
        action_categorize = menu.addAction("✨ Organize Selected Nodes")
        action_find_connections = menu.addAction("🔗 Find New Connections")
        action_generate_outline = menu.addAction("📝 Generate Outline")
        action_identify_weakpoints = menu.addAction("🔍 Identify Weakpoints")
        action_fill_graph = menu.addAction("🕸️ Fill Out Graph")
        action_consolidate = menu.addAction("🏗️ Consolidate Notes")
        
        # Make the Consolidate Notes action visibly disabled/unusable for now
        action_consolidate.setEnabled(False)
        
        action_categorize.triggered.connect(lambda: self.trigger_ai_organize([n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]))
        action_find_connections.triggered.connect(self.trigger_find_connections)
        action_generate_outline.triggered.connect(self.trigger_generate_outline)
        action_identify_weakpoints.triggered.connect(self.trigger_identify_weakpoints)
        action_fill_graph.triggered.connect(self.trigger_fill_graph)
        action_consolidate.triggered.connect(self.trigger_consolidate_notes)
        
        return menu

    def update_theme(self, theme):
        self.setBackgroundBrush(QBrush(QColor(theme['canvas'])))
        self.loading_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 180); border-radius: 8px;")
        self.loading_label.setStyleSheet(f"color: {theme['success']}; font-size: 26px; font-weight: bold; background: transparent;")
        
        if hasattr(self, 'toolbar_frame'):
            self.toolbar_frame.setStyleSheet(f"""
                QFrame#WorkspaceToolbar {{
                    background-color: {theme['bg_panel']};
                    border: 1px solid {theme['border']};
                    border-radius: 8px;
                }}
                QLabel {{ color: {theme['text_main']}; font-weight: bold; }}
                QComboBox {{ background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px; border-radius: 4px; font-weight: bold; min-width: 150px; }}
                QComboBox QAbstractItemView {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; }}
                QPushButton {{ background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
                QPushButton:hover {{ background-color: {theme['accent_hover']}; }}
                QPushButton::menu-indicator {{ image: none; }}
                QMenu {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; font-weight: bold; padding: 5px; }}
                QMenu::item {{ padding: 6px 20px 6px 20px; border-radius: 4px; }}
                QMenu::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
                QMenu::item:disabled {{ color: {theme['text_muted']}; }}
            """)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'loading_overlay') and not self.loading_overlay.isHidden():
            self.loading_overlay.resize(self.viewport().size())
            
        if hasattr(self, 'toolbar_frame'):
            self.toolbar_frame.move(15, 15)

    def _refresh_pdf_list(self):
        checked_data = self.filter_combo.get_checked_items()
        
        # Default all checked on first load if nothing exists
        if not checked_data and self.filter_combo.count() == 0:
            checked_data = ["ALL"]
            
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        
        self.filter_combo.addItem("All PDFs", "ALL", checked=("ALL" in checked_data))
        
        if self.main_window and hasattr(self.main_window, 'project_manager') and self.main_window.project_manager:
            for pdf in self.main_window.project_manager.pdfs:
                self.filter_combo.addItem(os.path.basename(pdf), pdf, checked=(pdf in checked_data))
                
        self.filter_combo.blockSignals(False)

    def get_allowed_docs(self):
        checked = self.filter_combo.get_checked_items()
        if "ALL" in checked or not checked:
            if self.main_window and hasattr(self.main_window, 'project_manager') and self.main_window.project_manager:
                return [os.path.basename(p) for p in self.main_window.project_manager.pdfs]
            return []
        return [os.path.basename(p) for p in checked if p != "ALL"]

    def _apply_filter(self):
        checked_pdfs = self.filter_combo.get_checked_items()
        show_all = "ALL" in checked_pdfs or not checked_pdfs
        
        # Filter Nodes
        for node in self.nodes.values():
            if show_all:
                node.show()
            else:
                if node.pdf_path is None:
                    node.show() # Always show custom structural nodes
                elif node.pdf_path in checked_pdfs:
                    node.show()
                else:
                    node.hide()
                    
        # Filter Edges (hide edge if either connected node is hidden)
        for edge in self.edges:
            if edge.source_node.isVisible() and edge.dest_node.isVisible():
                edge.show()
            else:
                edge.hide()

    def _open_color_by_pdf_dialog(self):
        if not self.main_window.project_manager.pdfs:
            QMessageBox.information(self, "No PDFs", "There are no PDFs in this project.")
            return
            
        current_colors = {}
        for node in self.nodes.values():
            if node.pdf_path and node.pdf_path not in current_colors:
                current_colors[node.pdf_path] = node.color
                
        for pdf in self.main_window.project_manager.pdfs:
            if pdf not in current_colors:
                current_colors[pdf] = "#2b2b2b"
                
        dialog = PDFColorDialog(self.main_window.project_manager.pdfs, current_colors, self)
        
        if hasattr(self.main_window, 'theme_manager'):
            theme = self.main_window.theme_manager.get_theme()
            dialog.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")
            dialog.scroll_widget.setStyleSheet(f"background-color: {theme['bg_panel']};")
            
        if dialog.exec():
            new_colors = dialog.get_colors()
            self.save_state_for_undo()
            
            for node in self.nodes.values():
                if node.pdf_path and node.pdf_path in new_colors:
                    node.color = new_colors[node.pdf_path]
                    node.setBrush(QBrush(QColor(node.color)))
                    node.refresh_layout()
                    
            self.main_window.project_manager.mark_dirty("workspace")
            if "Notes" in self.main_window.tabs:
                self.main_window.tabs["Notes"].save_workspace_state()

    def trigger_declutter(self):
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", "Please add or select some nodes to declutter.")
            return

        # Prepare layout data
        nodes_info = {}
        for node in target_nodes:
            nodes_info[node.node_id] = {
                'width': node.base_width,
                'height': node.base_height
            }

        edges_info = []
        for edge in self.edges:
            if edge.source_node in target_nodes and edge.dest_node in target_nodes:
                edges_info.append((edge.source_node.node_id, edge.dest_node.node_id))

        # Calculate a center point based on the current user focus
        avg_x = sum(n.pos().x() + n.base_width / 2 for n in target_nodes) / len(target_nodes)
        avg_y = sum(n.pos().y() + n.base_height / 2 for n in target_nodes) / len(target_nodes)

        # Call our mathematical layout engine
        new_positions = calculate_radial_layout(nodes_info, edges_info, avg_x, avg_y)

        if not new_positions:
            return

        self.save_state_for_undo()

        # Apply the mathematically generated positions to the nodes
        for node in target_nodes:
            if node.node_id in new_positions:
                pos = new_positions[node.node_id]
                node.setPos(pos['x'], pos['y'])

        # Refresh all connections
        for edge in self.edges:
            if edge.source_node in target_nodes and edge.dest_node in target_nodes:
                edge.update_position()

        self.main_window.project_manager.mark_dirty("workspace")
        
        # Snap the viewport directly over the freshly organized nodes
        items_rect = self.scene_obj.itemsBoundingRect()
        self.centerOn(items_rect.center())

    def _export_workspace(self):
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        
        if selected_nodes:
            target_nodes = selected_nodes
        else:
            target_nodes = [n for n in self.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.information(self, "Export", "Nothing to export! Ensure nodes are visible.")
            return

        target_edges = [e for e in self.edges if e.source_node in target_nodes and e.dest_node in target_nodes]

        visibility_states = {}
        for item in self.scene_obj.items():
            if item.parentItem() is None: 
                visibility_states[item] = item.isVisible()
                if item not in target_nodes and item not in target_edges:
                    item.setVisible(False)

        original_selection = self.scene_obj.selectedItems()
        self.scene_obj.clearSelection()

        for node in target_nodes:
            if hasattr(node, 'proxy_toolbar'):
                node.proxy_toolbar.hide()
            if hasattr(node, 'resize_handle'):
                node.resize_handle.hide()

        bounding_rect = QRectF()
        for item in target_nodes + target_edges:
            bounding_rect = bounding_rect.united(item.sceneBoundingRect())

        padding = 40
        bounding_rect.adjust(-padding, -padding, padding, padding)

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Workspace", "workspace_export.png", "PNG Image (*.png);;JPEG Image (*.jpg)"
        )

        if file_path:
            image = QImage(int(bounding_rect.width()), int(bounding_rect.height()), QImage.Format.Format_ARGB32)
            
            theme = self.main_window.theme_manager.get_theme() if hasattr(self.main_window, 'theme_manager') else {'canvas': '#1a1a1a'}
            image.fill(QColor(theme['canvas']))

            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            
            self.scene_obj.render(painter, QRectF(image.rect()), bounding_rect)
            painter.end()

            image.save(file_path)
            QMessageBox.information(self, "Export Successful", f"Workspace exported successfully to:\n{file_path}")

        for item, was_visible in visibility_states.items():
            item.setVisible(was_visible)
            
        for item in original_selection:
            item.setSelected(True)
            
        for node in target_nodes:
            node.refresh_layout()

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
                edge = Edge(src, tgt, edge_data["label"], edge_data["id"], edge_data.get("color", "#888888"), edge_data.get("weight", 2))
                self.scene_obj.addItem(edge)
                self.edges.append(edge)
                
        self._apply_filter()

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
                if self.connecting_node.isSelected():
                    self.connecting_node.setPen(QPen(QColor("#ffffff"), 4))
                else:
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
            menu = QMenu(self)
            del_action = menu.addAction("🗑️ Delete Selected Nodes")
            declutter_action = menu.addAction("🧹 Declutter Selected Nodes")
            
            menu.addSeparator()
            ai_menu = self.create_ai_menu(menu)
            menu.addMenu(ai_menu)
            
            action = menu.exec(event.globalPos())
            if action == del_action:
                self.save_state_for_undo()
                for n in selected_nodes:
                    self.delete_node(n)
            elif action == declutter_action:
                self.trigger_declutter()
            return

        if isinstance(item, Node):
            menu = QMenu(self)
            edit_action = menu.addAction("✏️ Edit Note Text")
            color_action = menu.addAction("🎨 Change Color")
            
            connect_action = None
            if len(selected_nodes) == 1 and item != selected_nodes[0]:
                connect_action = menu.addAction("🔗 Connect Selected Node to This")
                
            del_action = menu.addAction("🗑️ Delete Note")
            declutter_action = menu.addAction("🧹 Declutter Selected Node")
            
            menu.addSeparator()
            ai_menu = self.create_ai_menu(menu)
            menu.addMenu(ai_menu)
            
            action = menu.exec(event.globalPos())
            if action == edit_action:
                item.trigger_edit()
            elif action == color_action:
                item.trigger_color_change()
            elif connect_action and action == connect_action:
                self.save_state_for_undo()
                self.connecting_node = selected_nodes[0]
                self.finish_connection(item)
            elif action == del_action:
                self.save_state_for_undo()
                self.delete_node(item)
            elif action == declutter_action:
                self.trigger_declutter()
            return
            
        if isinstance(item, Edge):
            menu = QMenu(self)
            edit_action = menu.addAction("✏️ Edit Connection Text")
            color_action = menu.addAction("🎨 Change Line Color")
            weight_action = menu.addAction("📏 Change Line Weight")
            del_action = menu.addAction("🗑️ Delete Connection")
            
            menu.addSeparator()
            ai_menu = self.create_ai_menu(menu)
            menu.addMenu(ai_menu)
            
            action = menu.exec(event.globalPos())
            if action == edit_action:
                item.trigger_edit()
            elif action == color_action:
                item.trigger_color_change()
            elif action == weight_action:
                item.trigger_weight_change()
            elif action == del_action:
                self.save_state_for_undo()
                self.delete_edge(item)
            return

        # If empty canvas clicked, allow full tools menu
        if item is None:
            menu = QMenu(self)
            declutter_action = menu.addAction("🧹 Declutter All Notes")
            
            menu.addSeparator()
            ai_menu = self.create_ai_menu(menu)
            menu.addMenu(ai_menu)
            
            action = menu.exec(event.globalPos())
            if action == declutter_action:
                self.trigger_declutter()
            return

        super().contextMenuEvent(event)

    def trigger_ai_organize(self, selected_nodes):
        if not self.loading_overlay.isHidden(): return

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

        nodes_data = [{"id": n.node_id, "text": n.note or n.quote} for n in selected_nodes]
        llm_manager = self.main_window.tabs["LLM Chat"].llm_manager

        self.loading_label.setText("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()

        self.worker = AIOrganizeWorker(llm_manager, model, nodes_data, custom_instructions=instructions.strip(), parent=self)
        self.worker.finished.connect(self._on_ai_organize_finished)
        self.worker.start()

    def _on_ai_organize_finished(self, clusters, error_msg):
        self.loading_overlay.hide()

        if error_msg or not clusters:
            QMessageBox.warning(self, "AI Organize Failed", error_msg)
            return

        self.save_state_for_undo()
        
        try:
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

    def trigger_find_connections(self):
        if not self.loading_overlay.isHidden(): return

        model = self.main_window.tabs["LLM Chat"].model_combo.currentText().strip()
        if not model or "Error" in model or "running" in model:
            QMessageBox.warning(self, "No Model Selected", "Please select a valid AI model in the LLM Chat tab first.")
            return

        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        if len(target_nodes) < 2:
            QMessageBox.warning(self, "Not Enough Nodes", "Please select at least 2 nodes to find connections between.")
            return

        nodes_data = [{"id": n.node_id, "text": f"{n.quote} \n {n.note}".strip()} for n in target_nodes]
        edges_data = [{"source_id": e.source_node.node_id, "target_id": e.dest_node.node_id} 
                      for e in self.edges if e.source_node in target_nodes and e.dest_node in target_nodes]

        llm_manager = self.main_window.tabs["LLM Chat"].llm_manager

        self.loading_label.setText("✨ AI is analyzing relationships and finding new connections...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()

        self.conn_worker = AIFindConnectionsWorker(llm_manager, model, nodes_data, edges_data, parent=self)
        self.conn_worker.finished.connect(self._on_find_connections_finished)
        self.conn_worker.start()

    def _on_find_connections_finished(self, new_connections, error_msg):
        self.loading_overlay.hide()
        self.loading_label.setText("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")

        if error_msg:
            QMessageBox.warning(self, "AI Connection Failed", error_msg)
            return

        if not new_connections:
            QMessageBox.information(self, "No Connections Found", "The AI did not find any strong new connections between these nodes.")
            return

        self.save_state_for_undo()
        
        added_count = 0
        for conn in new_connections:
            src_id = conn.get("source_id")
            tgt_id = conn.get("target_id")
            
            if src_id in self.nodes and tgt_id in self.nodes and src_id != tgt_id:
                src_node = self.nodes[src_id]
                tgt_node = self.nodes[tgt_id]
                
                exists = False
                for existing_edge in self.edges:
                    if (existing_edge.source_node == src_node and existing_edge.dest_node == tgt_node) or \
                       (existing_edge.source_node == tgt_node and existing_edge.dest_node == src_node):
                        exists = True
                        break
                        
                if not exists:
                    label = conn.get("label", "AI Connection")
                    weight = max(1, min(10, int(conn.get("weight", 3))))
                    
                    edge = Edge(src_node, tgt_node, label, color="#9c27b0", weight=weight)
                    self.scene_obj.addItem(edge)
                    self.edges.append(edge)
                    added_count += 1

        if added_count > 0:
            self.main_window.project_manager.mark_dirty("workspace")
        else:
            QMessageBox.information(self, "No Connections Added", "The AI suggested connections that already existed.")

    def trigger_generate_outline(self):
        if not self.loading_overlay.isHidden(): return

        model = self.main_window.tabs["LLM Chat"].model_combo.currentText().strip()
        if not model or "Error" in model or "running" in model:
            QMessageBox.warning(self, "No Model Selected", "Please select a valid AI model in the LLM Chat tab first.")
            return

        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", "Please add or select some notes in the workspace first.")
            return

        nodes_data = [{"id": n.node_id, "type": "user_created" if n.is_custom else "pdf_note", "text": f"{n.quote} \n {n.note}".strip()} for n in target_nodes]
        edges_data = [{"source_id": e.source_node.node_id, "target_id": e.dest_node.node_id, "label": e.label_text} 
                      for e in self.edges if e.source_node in target_nodes and e.dest_node in target_nodes]

        llm_manager = self.main_window.tabs["LLM Chat"].llm_manager

        self.loading_label.setText("✨ AI is analyzing argument structure and drafting outline...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()

        self.outline_worker = AIOutlineWorker(llm_manager, model, nodes_data, edges_data, parent=self)
        self.outline_worker.finished.connect(self._on_generate_outline_finished)
        self.outline_worker.start()

    def _on_generate_outline_finished(self, outline_text, error_msg):
        self.loading_overlay.hide()
        self.loading_label.setText("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")

        if error_msg:
            QMessageBox.warning(self, "Outline Generation Failed", error_msg)
            return

        dialog = OutlineDialog(outline_text, self)
        
        if hasattr(self.main_window, 'theme_manager'):
            theme = self.main_window.theme_manager.get_theme()
            dialog.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")
            dialog.text_edit.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};")
            for btn in dialog.buttons:
                btn.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 8px; border-radius: 4px; font-weight: bold;")
                
            dialog.btn_save_node.setStyleSheet(f"background-color: {theme['accent']}; color: #ffffff; border: none; padding: 8px; border-radius: 4px; font-weight: bold;")

        dialog.exec()

    def trigger_identify_weakpoints(self):
        if not self.loading_overlay.isHidden(): return

        model = self.main_window.tabs["LLM Chat"].model_combo.currentText().strip()
        if not model or "Error" in model or "running" in model:
            QMessageBox.warning(self, "No Model Selected", "Please select a valid AI model in the LLM Chat tab first.")
            return

        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", "Please add or select some notes in the workspace to evaluate.")
            return

        nodes_data = [{"id": n.node_id, "type": "user_created" if n.is_custom else "pdf_note", "text": f"{n.quote} \n {n.note}".strip()} for n in target_nodes]
        edges_data = [{"source_id": e.source_node.node_id, "target_id": e.dest_node.node_id, "label": e.label_text} 
                      for e in self.edges if e.source_node in target_nodes and e.dest_node in target_nodes]

        llm_manager = self.main_window.tabs["LLM Chat"].llm_manager

        self.loading_label.setText("✨ AI is evaluating argument strength and identifying weak points...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()

        self.weakpoints_worker = AIWeakpointsWorker(llm_manager, model, nodes_data, edges_data, parent=self)
        self.weakpoints_worker.finished.connect(self._on_identify_weakpoints_finished)
        self.weakpoints_worker.start()

    def _on_identify_weakpoints_finished(self, analysis_text, error_msg):
        self.loading_overlay.hide()
        self.loading_label.setText("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")

        if error_msg:
            QMessageBox.warning(self, "Analysis Failed", error_msg)
            return

        dialog = WeakpointsDialog(analysis_text, self)
        
        if hasattr(self.main_window, 'theme_manager'):
            theme = self.main_window.theme_manager.get_theme()
            dialog.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")
            dialog.text_edit.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};")
            for btn in dialog.buttons:
                btn.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 8px; border-radius: 4px; font-weight: bold;")
                
            # Keep standard accent for the save node button, node background is handled in the dialog
            dialog.btn_save_node.setStyleSheet(f"background-color: {theme['accent']}; color: #ffffff; border: none; padding: 8px; border-radius: 4px; font-weight: bold;")

        dialog.exec()

    def trigger_fill_graph(self):
        if not self.loading_overlay.isHidden(): return

        model = self.main_window.tabs["LLM Chat"].model_combo.currentText().strip()
        if not model or "Error" in model or "running" in model:
            QMessageBox.warning(self, "No Model Selected", "Please select a valid AI model in the LLM Chat tab first.")
            return

        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", "Please add or select some nodes in the workspace first.")
            return

        nodes_data = [{"id": n.node_id, "type": "user_created" if n.is_custom else "pdf_note", "text": f"{n.quote} \n {n.note}".strip()} for n in target_nodes]
        edges_data = [{"source_id": e.source_node.node_id, "target_id": e.dest_node.node_id, "label": e.label_text} 
                      for e in self.edges if e.source_node in target_nodes and e.dest_node in target_nodes]

        allowed_docs = self.get_allowed_docs()

        llm_manager = self.main_window.tabs["LLM Chat"].llm_manager

        self.loading_label.setText("✨ AI is analyzing graph to find missing evidence...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()

        self.fill_worker = AIFillGraphWorker(llm_manager, model, nodes_data, edges_data, allowed_docs, parent=self)
        self.fill_worker.progress.connect(self._update_loading_label)
        self.fill_worker.finished.connect(self._on_fill_graph_finished)
        self.fill_worker.start()

    def _update_loading_label(self, text):
        self.loading_label.setText(text + "\nThis may take a moment.")

    def _on_fill_graph_finished(self, evidence_items, error_msg):
        self.loading_overlay.hide()
        self.loading_label.setText("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")

        if error_msg:
            QMessageBox.warning(self, "Fill Graph Failed", error_msg)
            return

        if not evidence_items:
            QMessageBox.information(self, "No Evidence Found", "AI could not find or suggest new evidence for the selected graph.")
            return

        self.save_state_for_undo()
        
        allowed_paths = self.main_window.project_manager.pdfs
        added_count = 0
        new_annot_mappings = []

        for item in evidence_items:
            quote = item['quote']
            note = item['note']
            target_doc = item['doc']
            target_node_id = item['target_node_id']
            
            new_annot_id = f"AINote|{uuid.uuid4()}"
            
            success = self.main_window.add_ai_annotation(quote, note, target_doc_name=target_doc, allowed_paths=allowed_paths, forced_annot_id=new_annot_id, emit_signal=False)
            if success:
                new_annot_mappings.append((new_annot_id, target_node_id))
                added_count += 1
                
        if added_count > 0:
            workspace_data = self.serialize_workspace()
            
            for new_annot_id, target_node_id in new_annot_mappings:
                workspace_data["edges"].append({
                    "id": str(uuid.uuid4()),
                    "source": target_node_id,
                    "target": new_annot_id,
                    "label": "AI Evidence",
                    "color": "#9c27b0",
                    "weight": 3
                })
                
            self.main_window.project_manager.save_workspace_data(workspace_data)
            self.main_window.project_manager.mark_dirty("workspace")
            
            all_annots = self.main_window.tabs["Notes"]._get_all_project_annotations_for_workspace()
            self.sync_with_project(workspace_data, all_annots)
            
            self.main_window.viewer.annot_manager.note_added.emit()
            
            QMessageBox.information(self, "Graph Filled", f"Successfully found and connected {added_count} piece(s) of evidence!")
        else:
            QMessageBox.information(self, "Graph Filled", "Searched for evidence but could not successfully highlight valid quotes in the documents.")

    def trigger_consolidate_notes(self):
        if not self.loading_overlay.isHidden(): return

        model = self.main_window.tabs["LLM Chat"].model_combo.currentText().strip()
        if not model or "Error" in model or "running" in model:
            QMessageBox.warning(self, "No Model Selected", "Please select a valid AI model in the LLM Chat tab first.")
            return

        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", "Please add or select some nodes to consolidate.")
            return

        nodes_data = [{"id": n.node_id, "type": "user_created" if n.is_custom else "pdf_note", "text": f"{n.quote} \n {n.note}".strip()} for n in target_nodes]
        edges_data = [{"source_id": e.source_node.node_id, "target_id": e.dest_node.node_id, "label": e.label_text} 
                      for e in self.edges if e.source_node in target_nodes and e.dest_node in target_nodes]

        llm_manager = self.main_window.tabs["LLM Chat"].llm_manager

        self.loading_label.setText("✨ AI is restructuring and streamlining your argument...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()

        self.consolidate_worker = AIConsolidateWorker(llm_manager, model, nodes_data, edges_data, parent=self)
        self.consolidate_worker.progress.connect(self._update_loading_label)
        self.consolidate_worker.finished.connect(self._on_consolidate_finished)
        self.consolidate_worker.start()

    def _on_consolidate_finished(self, result_dict, error_msg):
        self.loading_overlay.hide()
        self.loading_label.setText("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")

        if error_msg:
            QMessageBox.warning(self, "Consolidation Failed", error_msg)
            return

        if not result_dict.get("new_custom_nodes") and not result_dict.get("new_edges"):
            QMessageBox.information(self, "Consolidation Complete", "The AI didn't find any necessary structural changes.")
            return

        self.save_state_for_undo()
        
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        # Remove ONLY custom structural nodes from the target group (and attached edges)
        for node in list(target_nodes):
            if node.is_custom:
                self.delete_node(node)
            else:
                for edge in list(node.edges):
                    if edge.source_node in target_nodes and edge.dest_node in target_nodes:
                        self.delete_edge(edge)

        # Plot new custom nodes
        id_map = {}
        avg_x = sum(n.pos().x() for n in target_nodes) / len(target_nodes) if target_nodes else 0
        avg_y = sum(n.pos().y() for n in target_nodes) / len(target_nodes) if target_nodes else 0

        new_nodes_data = result_dict.get("new_custom_nodes", [])
        
        start_x = avg_x - (len(new_nodes_data) * 120)
        current_x = start_x
        start_y = avg_y - 250

        for n_data in new_nodes_data:
            old_id = n_data.get("id")
            text = n_data.get("text", "")
            if not old_id: continue
            
            new_id = f"custom_{uuid.uuid4()}"
            id_map[old_id] = new_id
            
            node = Node(new_id, quote="", note=text, color="#005577", is_custom=True, width=200, height=100)
            node.setPos(current_x, start_y)
            current_x += 240
            
            self.scene_obj.addItem(node)
            self.nodes[new_id] = node

        # Attach new connections
        for e_data in result_dict.get("new_edges", []):
            src_id = e_data.get("source_id")
            tgt_id = e_data.get("target_id")
            label = e_data.get("label", "Supports")
            
            src_id = id_map.get(src_id, src_id)
            tgt_id = id_map.get(tgt_id, tgt_id)
            
            if src_id in self.nodes and tgt_id in self.nodes:
                edge = Edge(self.nodes[src_id], self.nodes[tgt_id], label, color="#e91e63", weight=3)
                self.scene_obj.addItem(edge)
                self.edges.append(edge)
                
        self.main_window.project_manager.mark_dirty("workspace")
        QMessageBox.information(self, "Consolidated", "Workspace successfully restructured!")

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
            
        if self.connecting_node.isSelected():
            self.connecting_node.setPen(QPen(QColor("#ffffff"), 4))
        else:
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
        
        # Select the newly created node visually
        self.scene_obj.clearSelection()
        node.setSelected(True)
        
        # Trigger hover properties and editor
        node.is_hovered = True
        node.refresh_layout()
        node.trigger_edit()

    def sync_with_project(self, workspace_data, pdf_annotations):
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
                edge = Edge(src, tgt, edge_data["label"], edge_data["id"], edge_data.get("color", "#888888"), edge_data.get("weight", 2))
                self.scene_obj.addItem(edge)
                self.edges.append(edge)

        for n_id in selected_ids:
            if n_id in self.nodes:
                self.nodes[n_id].setSelected(True)
                
        self._refresh_pdf_list()
        self._apply_filter()

        if self.nodes:
            items_rect = self.scene_obj.itemsBoundingRect()
            self.centerOn(items_rect.center())

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
                "color": edge.base_color.name(),
                "weight": edge.weight
            })
        return data