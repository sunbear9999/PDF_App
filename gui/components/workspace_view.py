# gui/components/workspace_view.py
import os
import uuid
import json
import weakref  # [PERF FIX] Avoid circular references
from PyQt6.QtWidgets import (QGraphicsView, QGraphicsScene, QMenu, QMessageBox, 
                             QInputDialog, QFrame, QLabel, QVBoxLayout,
                             QHBoxLayout, QComboBox, QPushButton, QDialog,
                             QScrollArea, QWidget, QFormLayout, QDialogButtonBox, 
                             QColorDialog, QFileDialog, QTextEdit, QSizePolicy)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QImage, QStandardItemModel, QStandardItem

from gui.components.workspace_items import Node, Edge
from models.workspace_models import EdgeData, NodeData
from core.ai_organize_worker import AIOrganizeWorker
from core.ai_connections_worker import AIFindConnectionsWorker
from core.ai_outline_worker import AIOutlineWorker
from core.ai_weakpoints_worker import AIWeakpointsWorker
from core.ai_fill_graph_worker import AIFillGraphWorker
from core.ai_consolidate_worker import AIConsolidateWorker
from core.layout_engine import calculate_radial_layout
from controllers.workspace_controller import WorkspaceController
from gui.components.workspace.filters import CheckableComboBox
from gui.components.workspace.dialogs import OutlineDialog, WeakpointsDialog, PDFColorDialog
from gui.components.workspace.ai_tools import WorkspaceAITools
from gui.components.workspace.state_manager import WorkspaceStateManager
from gui.components.workspace.graph_editor import WorkspaceGraphEditor
from gui.components.workspace.context_menu import WorkspaceContextMenu
from gui.components.workspace.project_sync import WorkspaceProjectSync

class WorkspaceView(QGraphicsView):
    def __init__(self, main_window):
        super().__init__()
        self.setWindowTitle("Workspace")
        self.resize(1200, 800)
        self.setWindowFlags(Qt.WindowType.Window)
        
        self.main_window = main_window
        self.controller = getattr(self.main_window, 'workspace_controller', None)
        if isinstance(self.controller, WorkspaceController):
            self.controller.set_view(self)
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        # [PERF FIX] Use weakref.proxy to avoid circular reference: scene -> view -> main_window -> scene
        self.scene_obj.view = weakref.proxy(self)
        
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
        self.state_manager = WorkspaceStateManager(self)
        self.graph_editor = WorkspaceGraphEditor(self)
        self.context_menu = WorkspaceContextMenu(self)
        self.project_sync = WorkspaceProjectSync(self)

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
        
        # PDF selector
        pdf_label = QLabel("Project:")
        pdf_label.setStyleSheet("font-weight: bold;")
        tb_layout.addWidget(pdf_label)
        
        self.pdf_selector = QComboBox()
        self.pdf_selector.setFixedWidth(200)
        if hasattr(self.main_window, '_on_pdf_dropdown_changed'):
            self.pdf_selector.currentIndexChanged.connect(self.main_window._on_pdf_dropdown_changed)
        tb_layout.addWidget(self.pdf_selector)
        
        # tb_layout.addSeparator()  # Removed: QHBoxLayout has no addSeparator
        
        # Zoom controls
        self.btn_zoom_out = QPushButton("🔍-")
        self.btn_zoom_out.clicked.connect(lambda: self.main_window.viewer.zoom_out() if hasattr(self.main_window, 'viewer') else None)
        tb_layout.addWidget(self.btn_zoom_out)
        
        self.btn_fit_width = QPushButton("↔️ Fit Width")
        self.btn_fit_width.clicked.connect(lambda: self.main_window.viewer.fit_width() if hasattr(self.main_window, 'viewer') else None)
        tb_layout.addWidget(self.btn_fit_width)
        
        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.clicked.connect(lambda: self.main_window.viewer.zoom_in() if hasattr(self.main_window, 'viewer') else None)
        tb_layout.addWidget(self.btn_zoom_in)
        
        # tb_layout.addSeparator()  # Removed: QHBoxLayout has no addSeparator
        
        # Tools button
        self.btn_tools = QPushButton("🛠️ Tools")
        tools_menu = QMenu(self.btn_tools)
        
        # Add dock toggle actions
        if hasattr(self.main_window, 'dock_widgets'):
            for dock_name in ["Notes", "OCR", "Audio (TTS)", "LLM Chat"]:
                if dock_name in self.main_window.dock_widgets:
                    tools_menu.addAction(self.main_window.dock_widgets[dock_name].toggleViewAction())
        
        tools_menu.addSeparator()
        
        # Theme selector
        theme_menu = tools_menu.addMenu("🎨 Theme")
        if hasattr(self.main_window, 'theme_manager'):
            for theme_name in self.main_window.theme_manager.themes.keys():
                action = theme_menu.addAction(theme_name)
                action.triggered.connect(lambda checked, name=theme_name: self.main_window._on_theme_changed(name))
        
        self.btn_tools.setMenu(tools_menu)
        tb_layout.addWidget(self.btn_tools)
        
        # tb_layout.addSeparator()  # Removed: QHBoxLayout has no addSeparator
        
        # Workspace-specific controls
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
        self.ai_tools_locked = False
        self.ai_tools = WorkspaceAITools(self)
        self.ai_menu = self.create_ai_menu(self.btn_ai_tools)
        self.btn_ai_tools.setMenu(self.ai_menu)
        tb_layout.addWidget(self.btn_ai_tools)
        
        # Refresh PDF selector
        self._refresh_pdf_selector()

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

        if self.ai_tools_locked:
            for action in [action_categorize, action_find_connections, action_generate_outline, action_identify_weakpoints, action_fill_graph, action_consolidate]:
                action.setEnabled(False)
            menu.setTitle("🤖 AI Tools (Indexing...)" )
            menu.setEnabled(False)
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

    def lock_ai_tools(self):
        if hasattr(self, 'btn_ai_tools') and self.btn_ai_tools:
            self.ai_tools_locked = True
            self.btn_ai_tools.setEnabled(False)
            self.btn_ai_tools.setText("⏳ AI Indexing...")

    def unlock_ai_tools(self):
        if hasattr(self, 'btn_ai_tools') and self.btn_ai_tools:
            self.ai_tools_locked = False
            self.btn_ai_tools.setEnabled(True)
            self.btn_ai_tools.setText("🤖 AI Tools")

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
        
        if self.main_window and hasattr(self.main_window, 'pdf_controller'):
            for pdf in self.main_window.pdf_controller.get_pdf_paths():
                self.filter_combo.addItem(os.path.basename(pdf), pdf, checked=(pdf in checked_data))
                
        self.filter_combo.blockSignals(False)

    def _refresh_pdf_selector(self):
        if hasattr(self, 'pdf_selector'):
            self.pdf_selector.blockSignals(True)
            self.pdf_selector.clear()
            if self.main_window and hasattr(self.main_window, 'pdf_controller'):
                for path in self.main_window.pdf_controller.get_pdf_paths():
                    self.pdf_selector.addItem(os.path.basename(path), userData=path)
            self.pdf_selector.blockSignals(False)

    def get_allowed_docs(self):
        checked = self.filter_combo.get_checked_items()
        if "ALL" in checked or not checked:
            if self.main_window and hasattr(self.main_window, 'pdf_controller'):
                return [os.path.basename(p) for p in self.main_window.pdf_controller.get_pdf_paths()]
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
        pdf_paths = self.main_window.pdf_controller.get_pdf_paths() if self.main_window and hasattr(self.main_window, 'pdf_controller') else []
        if not pdf_paths:
            QMessageBox.information(self, "No PDFs", "There are no PDFs in this project.")
            return
            
        current_colors = {}
        for node in self.nodes.values():
            if node.pdf_path and node.pdf_path not in current_colors:
                current_colors[node.pdf_path] = node.color
                
        for pdf in pdf_paths:
            if pdf not in current_colors:
                current_colors[pdf] = "#2b2b2b"
                
        dialog = PDFColorDialog(pdf_paths, current_colors, self)
        
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
                    
            if self.controller:
                self.controller.mark_dirty("workspace")
            else:
                self.main_window.persistence_controller.mark_dirty("workspace")
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

        if self.controller:
            self.controller.mark_dirty("workspace")
        else:
            self.main_window.persistence_controller.mark_dirty("workspace")
        
        # Snap the viewport directly over the freshly organized nodes
        items_rect = self.scene_obj.itemsBoundingRect()
        self.centerOn(items_rect.center())

    def _export_workspace(self):
        self.graph_editor.export_workspace()

    def save_state_for_undo(self):
        self.state_manager.save_state_for_undo()

    def _update_buttons(self):
        self.state_manager._update_buttons()

    def undo(self):
        self.state_manager.undo()

    def redo(self):
        self.state_manager.redo()

    def load_workspace_state(self, state_data):
        self.state_manager.load_workspace_state(state_data)

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
        self.graph_editor.delete_edge(edge)

    def delete_node(self, node):
        self.graph_editor.delete_node(node)

    def contextMenuEvent(self, event):
        self.context_menu.context_menu_event(event)

    def _fallback_context_menu_event(self, event):
        # Used by WorkspaceContextMenu for any unexpected fallback paths.
        super().contextMenuEvent(event)

    def trigger_ai_organize(self, selected_nodes):
        self.ai_tools.trigger_ai_organize(selected_nodes)

    def _on_ai_organize_finished(self, clusters, error_msg):
        self.ai_tools._on_ai_organize_finished(clusters, error_msg)

    def trigger_find_connections(self):
        self.ai_tools.trigger_find_connections()

    def _on_find_connections_finished(self, new_connections, error_msg):
        self.ai_tools._on_find_connections_finished(new_connections, error_msg)

    def trigger_generate_outline(self):
        self.ai_tools.trigger_generate_outline()

    def _on_generate_outline_finished(self, outline_text, error_msg):
        self.ai_tools._on_generate_outline_finished(outline_text, error_msg)

    def trigger_identify_weakpoints(self):
        self.ai_tools.trigger_identify_weakpoints()

    def _on_identify_weakpoints_finished(self, analysis_text, error_msg):
        self.ai_tools._on_identify_weakpoints_finished(analysis_text, error_msg)

    def trigger_fill_graph(self):
        self.ai_tools.trigger_fill_graph()

    def _update_loading_label(self, text):
        self.ai_tools._update_loading_label(text)

    def _on_fill_graph_finished(self, evidence_items, error_msg):
        self.ai_tools._on_fill_graph_finished(evidence_items, error_msg)

    def trigger_consolidate_notes(self):
        self.ai_tools.trigger_consolidate_notes()

    def _on_consolidate_finished(self, result_dict, error_msg):
        self.ai_tools._on_consolidate_finished(result_dict, error_msg)

    def start_connection(self, node):
        self.graph_editor.start_connection(node)

    def finish_connection(self, target_node):
        self.graph_editor.finish_connection(target_node)

    def add_custom_bubble(self):
        self.project_sync.add_custom_bubble()

    def sync_with_project(self, workspace_data, pdf_annotations):
        self.project_sync.sync_with_project(workspace_data, pdf_annotations)

    def serialize_workspace(self):
        return self.state_manager.serialize_workspace()