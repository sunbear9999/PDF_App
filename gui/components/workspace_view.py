# gui/components/workspace_view.py
import os
import uuid
import json
import dataclasses
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QMenu, QMessageBox,
                             QInputDialog, QFrame, QLabel, QVBoxLayout,
                             QHBoxLayout, QComboBox, QPushButton, QDialog,
                             QScrollArea, QWidget, QFormLayout, QDialogButtonBox,
                             QColorDialog, QFileDialog, QTextEdit, QCheckBox, QSlider,
                             QGraphicsLineItem, QGraphicsTextItem, QListWidget,
                             QListWidgetItem,QSizePolicy,QApplication)
from PySide6.QtCore import QPointF, Qt, QRectF, QRunnable, QThreadPool, Slot
from PySide6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QImage, QStandardItemModel, QStandardItem, QCursor, QPainterPath, QPainterPathStroker, QShortcut, QKeySequence

from core.engine.default_blueprints import DefaultBlueprints
from gui.components.workspace_items import Node, Edge
from core.engine.action_model import AIActionBlueprint, ActionStep
from core.engine.master_runner import MasterActionRunner
from core.layout_engine import calculate_force_directed_layout
from core.text_utils import get_semantic_similarity_matrix
from gui.components.dialogs.workspace_dialogs import ColorOrganizerDialog, DeclutterSettingsDialog, OutlineDialog, WeakpointsDialog, WorkspaceProcessOverlay
from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
from gui.components.dialogs.tag_relatives_dialog import AIResultsDialog

# --- NEW IMPORTS ---
from core.models.workspace_models import WorkspaceModel, NodeModel, EdgeModel
from core.api.workspace_ai import WorkspaceAIApi


class GhostLineItem(QGraphicsLineItem):
    def __init__(self, x1, y1, x2, y2, source_id, target_id, sim_score, workspace_view):
        super().__init__(x1, y1, x2, y2)
        self.source_id = source_id
        self.target_id = target_id
        self.sim_score = sim_score
        self.workspace_view = workspace_view
        self.setAcceptedMouseButtons(Qt.MouseButton.RightButton)

    def shape(self):
        p = QPainterPath()
        p.moveTo(self.line().p1())
        p.lineTo(self.line().p2())
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        return stroker.createStroke(p)

    def contextMenuEvent(self, event):
        menu = QMenu()
        pct = int(self.sim_score * 100)
        convert_action = menu.addAction(f"\U0001f517 Convert to Edge  (similarity {pct}%)")
        action = menu.exec(event.screenPos())
        if action == convert_action:
            self.workspace_view._convert_ghost_to_edge(self.source_id, self.target_id, self.sim_score)
        event.accept()


class CollapsingButton(QPushButton):
    _COLLAPSED_WIDTH = 36
    def __init__(self, icon_text, full_text, parent=None):
        super().__init__(icon_text, parent)
        self._icon_text = icon_text
        self._full_text = full_text
        self.setFixedWidth(self._COLLAPSED_WIDTH)

    def _sync_toolbar(self):
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QFrame) and w.objectName() == "WorkspaceToolbar":
                w.adjustSize()
                w.move(15, 15)
                return
            w = w.parentWidget()

    def enterEvent(self, event):
        self.setText(self._full_text)
        self.setFixedWidth(self.sizeHint().width() + 8)
        self._sync_toolbar()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setText(self._icon_text)
        self.setFixedWidth(self._COLLAPSED_WIDTH)
        self._sync_toolbar()
        super().leaveEvent(event)


class CollapsingSection(QFrame):
    def __init__(self, icon_text, content_widget, parent=None):
        super().__init__(parent)
        self._content = content_widget
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)
        self._icon_label = QLabel(icon_text)
        self._icon_label.setObjectName("CollapsingIcon")
        layout.addWidget(self._icon_label)
        layout.addWidget(content_widget)
        content_widget.hide()

    def _sync_toolbar(self):
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QFrame) and w.objectName() == "WorkspaceToolbar":
                w.adjustSize()
                w.move(15, 15)
                return
            w = w.parentWidget()

    def enterEvent(self, event):
        self._content.show()
        self.adjustSize()
        self._sync_toolbar()
        super().enterEvent(event)

    def leaveEvent(self, event):
        local_pos = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(local_pos):
            self._content.hide()
            self.adjustSize()
            self._sync_toolbar()
        super().leaveEvent(event)


class CheckableComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view().clicked.connect(self.handle_item_clicked)
        self._changed = False

    def handle_item_clicked(self, index):
        item = self.model().itemFromIndex(index)
        if not item: return

        clicked_data = item.data(Qt.ItemDataRole.UserRole)
        current_state = item.checkState()
        all_item = self.model().item(0)

        self.model().blockSignals(True)

        if clicked_data == "ALL":
            item.setCheckState(Qt.CheckState.Checked)
            for i in range(1, self.model().rowCount()):
                self.model().item(i).setCheckState(Qt.CheckState.Unchecked)
        else:
            if current_state == Qt.CheckState.Checked:
                if all_item and all_item.checkState() == Qt.CheckState.Checked:
                    all_item.setCheckState(Qt.CheckState.Unchecked)
                    for i in range(1, self.model().rowCount()):
                        other_item = self.model().item(i)
                        if other_item != item:
                            other_item.setCheckState(Qt.CheckState.Unchecked)
            else:
                any_checked = any(self.model().item(i).checkState() == Qt.CheckState.Checked for i in range(1, self.model().rowCount()))
                if not any_checked and all_item:
                    all_item.setCheckState(Qt.CheckState.Checked)

        self._changed = True
        self.model().blockSignals(False)

        top_left = self.model().index(0, 0)
        bottom_right = self.model().index(self.model().rowCount() - 1, 0)
        self.model().dataChanged.emit(top_left, bottom_right)

    def hidePopup(self):
        if not self._changed:
            super().hidePopup()
        self._changed = False

    def addItem(self, text, userData=None, checked=False):
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
            model.setColumnCount(1)
        else:
            super().clear()


class UnusedHighlightsDialog(QDialog):
    def __init__(self, highlights, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unused Highlights")
        self.resize(520, 380)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Highlights in the database that are not in this workspace:"))

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for highlight in highlights:
            text_content = (highlight.get("text_content") or "[Empty Highlight]").strip()
            doc_name = os.path.basename(highlight.get("doc_id") or "Unknown PDF")
            page_num = highlight.get("page_num")
            page_label = f"Pg {page_num + 1}" if isinstance(page_num, int) else "Unknown Page"
            label = f"{doc_name} - {page_label}: {text_content}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, highlight.get("id"))
            item.setToolTip(text_content)
            self.list_widget.addItem(item)

        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_add = QPushButton("Add to Workspace")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_add.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        self.list_widget.itemDoubleClicked.connect(lambda _: self.accept())

    def get_selected_highlight_ids(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.list_widget.selectedItems()]


class NodeEmbeddingTask(QRunnable):
    def __init__(self, node_id, text, llm_manager, project_manager):
        super().__init__()
        self.node_id = node_id
        self.text = text
        self.llm_manager = llm_manager
        self.project_manager = project_manager

    @Slot()
    def run(self):
        if not self.text.strip() or not self.llm_manager or not self.llm_manager.ai_enabled:
            return
            
        try:
            vector = self.llm_manager.get_embedding(self.text)
            if vector:
                self.project_manager.save_node_embedding_threadsafe(self.node_id, vector)
        except Exception:
            pass


class WorkspaceView(QGraphicsView):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.scene_obj.view = self
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
    
        self.current_workspace_id = 1
        self._switching_workspace = False
        self.ai_api = WorkspaceAIApi(self.main_window.project_manager)

        self.nodes = {}
        self.edges = []
        self.ghost_lines = []
        self.similarity_matrix = {}
        self._similarity_signature = None
        self._updating_ghost_links = False
        self.connecting_node = None
        self.worker = None
        self.is_llm_busy = False
        self.is_dialog_open = False
        self.undo_stack = []
        self.redo_stack = []
        self.is_restoring = False

        self.clipboard = {'nodes': [], 'edges': []}
        self.ai_overlay = WorkspaceProcessOverlay(self.main_window.process_registry, parent=self)

        self._copy_sc = QShortcut(QKeySequence("Ctrl+C"), self)
        self._copy_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._copy_sc.activated.connect(self.copy_selection)
        self._copy_sc.activatedAmbiguously.connect(self.copy_selection)
        self._cut_sc = QShortcut(QKeySequence("Ctrl+X"), self)
        self._cut_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._cut_sc.activated.connect(self.cut_selection)
        self._cut_sc.activatedAmbiguously.connect(self.cut_selection)
        self._paste_sc = QShortcut(QKeySequence("Ctrl+V"), self)
        self._paste_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._paste_sc.activated.connect(self.paste_selection)
        self._paste_sc.activatedAmbiguously.connect(self.paste_selection)
        self._refresh_sc = QShortcut(QKeySequence("Ctrl+R"), self)
        self._refresh_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._refresh_sc.activated.connect(self._sync_workspace)
        self._refresh_sc.activatedAmbiguously.connect(self._sync_workspace)

        self._new_node_sc = QShortcut(QKeySequence("Ctrl+N"), self)
        self._new_node_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._new_node_sc.activated.connect(self.add_custom_bubble)
        self._new_node_sc.activatedAmbiguously.connect(self.add_custom_bubble)

        self._clear_filters_sc = QShortcut(QKeySequence("Ctrl+W"), self)
        self._clear_filters_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._clear_filters_sc.activated.connect(self.reset_filters)
        self._clear_filters_sc.activatedAmbiguously.connect(self.reset_filters)

        self._declutter_sc = QShortcut(QKeySequence("Ctrl+D"), self)
        self._declutter_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._declutter_sc.activated.connect(self.trigger_declutter)
        self._declutter_sc.activatedAmbiguously.connect(self.trigger_declutter)

        self.loading_overlay = QFrame(self)
        self.loading_overlay.hide()
        
        overlay_layout = QVBoxLayout(self.loading_overlay)
        self.loading_label = QLabel("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay_layout.addWidget(self.loading_label)

        self.toolbar_frame = QFrame(self)
        self.toolbar_frame.setObjectName("WorkspaceToolbar")
        self.toolbar_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        main_tb_layout = QVBoxLayout(self.toolbar_frame)
        main_tb_layout.setContentsMargins(4, 4, 4, 4)
        main_tb_layout.setSpacing(2) 

        compact_btn_style = """
            QPushButton {
                padding: 4px 6px;
                border-radius: 4px;
                font-weight: bold;
                text-align: center;
            }
        """

        self.row1_widget = QWidget()
        row1_layout = QHBoxLayout(self.row1_widget)
        row1_layout.setContentsMargins(0, 0, 0, 0)
        row1_layout.setSpacing(4) 

        ws_label = QLabel("🗂️")
        row1_layout.addWidget(ws_label)
        
        self.workspace_combo = QComboBox()
        self.workspace_combo.setFixedWidth(110)
        self.workspace_combo.addItem("Main Board", 1)
        self.workspace_combo.currentIndexChanged.connect(self._on_tab_changed)
        row1_layout.addWidget(self.workspace_combo)

        self.btn_add_workspace = QPushButton("➕ Board")
        self.btn_add_workspace.setStyleSheet(compact_btn_style)
        self.btn_add_workspace.clicked.connect(self._add_workspace)
        row1_layout.addWidget(self.btn_add_workspace)

        line1 = QFrame()
        line1.setFrameShape(QFrame.Shape.VLine)
        row1_layout.addWidget(line1)

        self.btn_ai_tools = QPushButton("🤖 AI")
        self.btn_ai_tools.setStyleSheet(compact_btn_style)
        self.ai_menu = self.create_ai_menu(self.btn_ai_tools)
        self.btn_ai_tools.setMenu(self.ai_menu)
        row1_layout.addWidget(self.btn_ai_tools)

        self.btn_add_main_idea = QPushButton("💡 Idea")
        self.btn_add_main_idea.setStyleSheet(compact_btn_style)
        self.btn_add_main_idea.clicked.connect(self.add_custom_bubble)
        row1_layout.addWidget(self.btn_add_main_idea)

        self.btn_undo = QPushButton("↩️")
        self.btn_undo.setFixedWidth(28)
        self.btn_undo.setStyleSheet("padding: 2px; text-align: center;")
        self.btn_undo.clicked.connect(self.undo)
        row1_layout.addWidget(self.btn_undo)

        self.btn_redo = QPushButton("↪️")
        self.btn_redo.setFixedWidth(28)
        self.btn_redo.setStyleSheet("padding: 2px; text-align: center;")
        self.btn_redo.clicked.connect(self.redo)
        row1_layout.addWidget(self.btn_redo)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.VLine)
        row1_layout.addWidget(line2)

        self.btn_recenter = QPushButton("🎯 Center")
        self.btn_recenter.setStyleSheet(compact_btn_style)
        self.btn_recenter.clicked.connect(self.recenter_view)
        row1_layout.addWidget(self.btn_recenter)

        self.btn_clear_filter = QPushButton("⚠️ Clear")
        self.btn_clear_filter.setStyleSheet("background-color: #aa3333; color: white; font-weight: bold; padding: 4px; border-radius: 4px;")
        self.btn_clear_filter.clicked.connect(self.reset_filters)
        self.btn_clear_filter.hide()
        row1_layout.addWidget(self.btn_clear_filter)

        row1_layout.addStretch()

        self.btn_toggle_row2 = QPushButton("🔽")
        self.btn_toggle_row2.setFixedWidth(28)
        self.btn_toggle_row2.setStyleSheet("padding: 2px; font-weight: bold;")
        self.btn_toggle_row2.setCursor(Qt.CursorShape.PointingHandCursor)
        row1_layout.addWidget(self.btn_toggle_row2)

        main_tb_layout.addWidget(self.row1_widget)

        self.row2_widget = QWidget()
        row2_layout = QHBoxLayout(self.row2_widget)
        row2_layout.setContentsMargins(0, 4, 0, 0)
        row2_layout.setSpacing(6)

        self.filter_combo = CheckableComboBox()
        self.filter_combo.setFixedWidth(105)
        self.filter_combo.setToolTip("Filter the workspace to only show nodes from specific PDFs.")
        self.filter_combo.model().dataChanged.connect(self._apply_filter)
        row2_layout.addWidget(self.filter_combo)
        
        self.tag_filter_combo = CheckableComboBox()
        self.tag_filter_combo.setFixedWidth(105)
        self.tag_filter_combo.setToolTip("Filter the workspace to only show nodes with specific Tags.")
        self.tag_filter_combo.model().dataChanged.connect(self._apply_filter)
        row2_layout.addWidget(self.tag_filter_combo)

        line3 = QFrame()
        line3.setFrameShape(QFrame.Shape.VLine)
        row2_layout.addWidget(line3)

        self.btn_color_pdf = QPushButton("🎨 Colors")
        self.btn_color_pdf.setStyleSheet(compact_btn_style)
        self.btn_color_pdf.setToolTip("Automatically color-code all nodes based on which PDF they came from.")
        self.btn_color_pdf.clicked.connect(self._open_color_by_pdf_dialog)
        row2_layout.addWidget(self.btn_color_pdf)

        self.btn_declutter = QPushButton("🧹 Clean")
        self.btn_declutter.setStyleSheet(compact_btn_style)
        self.btn_declutter.setToolTip("Auto-arrange overlapping nodes to tidy up the board.")
        self.btn_declutter.clicked.connect(self.trigger_declutter)
        row2_layout.addWidget(self.btn_declutter)

        self.btn_export = QPushButton("📸 Export")
        self.btn_export.setStyleSheet(compact_btn_style)
        self.btn_export.setToolTip("Save an image copy of your current workspace.")
        self.btn_export.clicked.connect(self._export_workspace)
        row2_layout.addWidget(self.btn_export)

        self.btn_unused_highlights = QPushButton("📥 Inbox")
        self.btn_unused_highlights.setStyleSheet(compact_btn_style)
        self.btn_unused_highlights.setToolTip("View all highlights you've made in your PDFs that haven't been added to this board yet.")
        self.btn_unused_highlights.clicked.connect(self.open_unused_highlights_dialog)
        row2_layout.addWidget(self.btn_unused_highlights)

        row2_layout.addStretch()

        ghost_inner = QHBoxLayout()
        ghost_inner.setSpacing(4)
        
        self.chk_show_ghost_links = QCheckBox("👻")
        self.chk_show_ghost_links.setChecked(False)
        self.chk_show_ghost_links.setToolTip("Show semantic connections between notes with similar meanings.")
        ghost_inner.addWidget(self.chk_show_ghost_links)
        
        self.slider_ghost_threshold = QSlider(Qt.Orientation.Horizontal)
        self.slider_ghost_threshold.setRange(60, 95)
        self.slider_ghost_threshold.setValue(75)
        self.slider_ghost_threshold.setFixedWidth(60)
        self.slider_ghost_threshold.setToolTip("Adjust how strict the AI is when suggesting links.")
        ghost_inner.addWidget(self.slider_ghost_threshold)
        
        row2_layout.addLayout(ghost_inner)
        main_tb_layout.addWidget(self.row2_widget)
        self.row2_widget.hide()

        def toggle_secondary_tools():
            if self.row2_widget.isVisible():
                self.row2_widget.hide()
                self.btn_toggle_row2.setText("🔽")
            else:
                self.row2_widget.show()
                self.btn_toggle_row2.setText("🔼")
            main_tb_layout.invalidate()
            main_tb_layout.activate()
            self.toolbar_frame.setFixedHeight(main_tb_layout.sizeHint().height())

        self.btn_toggle_row2.clicked.connect(toggle_secondary_tools)
        main_tb_layout.activate()
        self.toolbar_frame.setFixedHeight(main_tb_layout.sizeHint().height())

        self.chk_show_ghost_links.toggled.connect(self.update_ghost_connections)
        self.slider_ghost_threshold.valueChanged.connect(self.update_ghost_connections)
        self.scene_obj.selectionChanged.connect(self.update_ghost_connections)
        self.scene_obj.changed.connect(self._on_scene_changed)

        self.update_scene_bounds()

    def get_active_ai_model(self):
        if hasattr(self.main_window, 'chat_docks') and self.main_window.chat_docks:
            try:
                selected = self.main_window.chat_docks[0].model_combo.currentText().strip()
                if selected and "Error" not in selected and "Select" not in selected and "running" not in selected:
                    return selected
            except AttributeError:
                pass 
        return "gemma4:e2b"

    def recenter_view(self):
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            rect.adjust(-50, -50, 50, 50)
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _queue_background_embedding(self, node):
        try:
            llm_manager = self.main_window.shared_llm_manager
            pm = self.main_window.project_manager
            text = f"{node.quote} {node.note}".strip()
            task = NodeEmbeddingTask(node.node_id, text, llm_manager, pm)
            QThreadPool.globalInstance().start(task)
        except Exception:
            pass

    # =========================================================================
    # UNIVERSAL AI WORKSPACE API & MODELS
    # =========================================================================

    def serialize_workspace(self) -> WorkspaceModel:
        """Serializes the canvas directly into the strict Data Model."""
        model = WorkspaceModel(workspace_id=self.current_workspace_id)
        for n_id, node in self.nodes.items():
            model.nodes.append(NodeModel(
                id=n_id, highlight_id=node.highlight_id, workspace_id=self.current_workspace_id,
                quote=node.quote, note=node.note, color=node.color, is_custom=node.is_custom,
                pdf_path=node.pdf_path, page_num=node.page_num, manual_font_size=node.manual_font_size,
                x=node.pos().x(), y=node.pos().y(), width=node.base_width, height=node.base_height,
                node_origin=getattr(node, "node_origin", "human"), 
                is_verified=int(getattr(node, "is_verified", 0)), 
                original_text=getattr(node, "original_text", getattr(node, "note", ""))
            ))
        for edge in self.edges:
            model.edges.append(EdgeModel(
                id=edge.edge_id, source=edge.source_node.node_id, target=edge.dest_node.node_id,
                label=edge.label_text, color=edge.base_color.name(), weight=edge.weight
            ))
        return model

    def save_workspace_state(self):
        """Standalone save method that pushes the Model to the DB."""
        model = self.serialize_workspace()
        if self.main_window and hasattr(self.main_window, "project_manager"):
            self.main_window.project_manager.sync_workspace(model)

    def _mark_workspace_dirty(self, autosave=False):
        pm = getattr(self.main_window, 'project_manager', None)
        if not pm: return
        pm.mark_dirty("workspace")
        if autosave:
            # This triggers the serialization and the smart sync_workspace update
            self.save_workspace_state()

    def get_workspace_state_as_json(self, only_selected=False, filters=None):
        """Passes the model into the new API orchestrator."""
        model = self.serialize_workspace()
        
        if only_selected:
            selected_ids = {n.node_id for n in self.scene_obj.selectedItems() if isinstance(n, Node)}
            model.nodes = [n for n in model.nodes if n.id in selected_ids]
            model.edges = [e for e in model.edges if e.source in selected_ids and e.target in selected_ids]
            
        return self.ai_api.build_ai_context(model, filters)

    def apply_ai_graph_update(self, ai_output_string):
        """Receives LLM JSON, pushes it through the API to get a Delta Model, and syncs."""
        success, result = self.ai_api.process_ai_response(ai_output_string, self.current_workspace_id)
        
        if not success:
            return False, result # Returns the error message
            
        delta_model = result
        self.save_state_for_undo()
        
        # 1. Native Highlighting for newly generated PDF nodes
        for node in delta_model.nodes:
            if node.pdf_path and not node.highlight_id and hasattr(self.main_window, 'add_ai_annotation'):
                new_annot_id = f"AINote|{uuid.uuid4()}"
                ok = self.main_window.add_ai_annotation(
                    node.quote, node.note, target_doc_name=node.pdf_path, 
                    allowed_paths=self.main_window.project_manager.pdfs, 
                    forced_annot_id=new_annot_id, emit_signal=False
                )
                if ok:
                    hl_record = self.main_window.project_manager.get_highlight(new_annot_id)
                    if hl_record:
                        node.id = hl_record["id"]
                        node.highlight_id = hl_record["id"]
                        node.pdf_path = hl_record.get("doc_id")
                        node.page_num = hl_record.get("page_num")
                        node.color = hl_record.get("color", node.color)
                        node.is_custom = False
                        
        # 2. Push Delta directly to the Smart DB
        self.main_window.project_manager.sync_workspace_delta(delta_model)
        
        # 3. Reload UI natively from the freshly updated DB
        self._sync_workspace()
        return True, "Universal graph applied."

    def review_and_apply_ai_graph_update(self, json_str):
        from gui.components.dialogs.workspace_review_dialog import WorkspaceReviewDialog
        theme = getattr(self.main_window, 'theme_manager', None).get_theme() if hasattr(self.main_window, 'theme_manager') else None
        dialog = WorkspaceReviewDialog(json_str, theme, self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_ai_graph_update(json_str)

    def _run_workspace_ai_tool(self, blueprint: AIActionBlueprint, require_selection=True):
        if not self.main_window.shared_llm_manager.ai_enabled:
            QMessageBox.warning(self, "AI Disabled", "Local AI is not running.")
            return

        permissions = blueprint.steps[0].permissions if blueprint.steps else ["all"]
        json_data = self.get_workspace_state_as_json(only_selected=require_selection, filters=permissions)
        
        if require_selection and not json_data:
            QMessageBox.warning(self, "Selection Required", "Please select nodes to process.")
            return

        model_name = self.get_active_ai_model()
        initial_state = {
            "workspace_data": json_data,
            "selected_model": model_name
        }

        import copy
        runtime_blueprint = copy.copy(blueprint)
        if not runtime_blueprint.name.startswith("Workspace:"):
            runtime_blueprint.name = f"Workspace: {runtime_blueprint.name}"

        self.ai_worker = MasterActionRunner(self.main_window, runtime_blueprint, initial_state)
        
        def _handle_completion(state):
            ai_output = state.get(runtime_blueprint.steps[-1].output_key, "")
            output_mode = runtime_blueprint.steps[-1].output_mode if runtime_blueprint.steps else "workspace_update"
            
            if output_mode == "dialog":
                if "Outline" in runtime_blueprint.name:
                    OutlineDialog(ai_output, self).exec()
                elif "Weakpoints" in runtime_blueprint.name:
                    WeakpointsDialog(ai_output, self).exec()
                else:
                    QMessageBox.information(self, runtime_blueprint.name.replace("Workspace: ", ""), ai_output)
                return

            success, msg = self.apply_ai_graph_update(ai_output)
            if not success:
                self.main_window.process_registry.update_job_status(self.ai_worker.job.id, "Error: Invalid AI Format")
                QMessageBox.warning(self, "AI Formatting Error", f"The AI failed to generate a valid graph. Output:\n\n{ai_output[:250]}...")

        def _handle_error(e):
            self.main_window.statusBar().showMessage(f"⚠️ AI Error: {e}", 5000)

        self.ai_worker.action_complete.connect(_handle_completion)
        self.ai_worker.error.connect(_handle_error)
        self.ai_worker.start()

    # =========================================================================
    # SCENE & COMPONENT MANAGEMENT
    # =========================================================================

    def add_node_from_annotation(self, annot, persist=False, position=None, target_workspace_id=None):
        n_id = annot["id"]
        effective_ws_id = target_workspace_id if target_workspace_id is not None else self.current_workspace_id
        origin = "ai" if n_id.startswith("AINote") else "human"
        
        if persist and effective_ws_id != self.current_workspace_id:
            pm = self.main_window.project_manager
            if pm and pm.project_filepath:
                model = pm.get_workspace_data(effective_ws_id)
                quote = annot.get("subject") or annot.get("text_content") or ""
                note = annot.get("content") or annot.get("note_text") or ""
                color = annot.get("color") or "#2b2b2b"
                w = 200 if len(note + quote) < 50 else (250 if len(note + quote) < 150 else 300)
                h = 70 if len(note + quote) < 50 else (110 if len(note + quote) < 150 else 160)
                
                new_node = NodeModel(
                    id=n_id, highlight_id=n_id, workspace_id=effective_ws_id, quote=quote, note=note,
                    color=color, is_custom=False, pdf_path=annot.get("pdf_path") or annot.get("doc_id"),
                    page_num=annot.get("page_num"), x=0.0, y=0.0, width=w, height=h, node_origin=origin
                )
                model.nodes.append(new_node)
                pm.sync_workspace(model)
            return None

        if n_id in self.nodes:
            return self.nodes[n_id]

        quote = annot.get("subject") or annot.get("text_content") or ""
        note = annot.get("content") or annot.get("note_text") or ""
        color = annot.get("color") or ("#2d2238" if n_id.startswith("AINote") else "#2b2b2b")
        w = 200 if len(note + quote) < 50 else (250 if len(note + quote) < 150 else 300)
        h = 70 if len(note + quote) < 50 else (110 if len(note + quote) < 150 else 160)
        
        node = Node(
            n_id, quote, note, color=color, is_custom=False, width=w, height=h,
            pdf_path=annot.get("pdf_path") or annot.get("doc_id"), page_num=annot.get("page_num"),
            highlight_id=n_id, node_origin=origin, is_verified=0, original_text=note
        )
        
        if position is None:
            position = self.mapToScene(self.viewport().rect().center())
        node.setPos(position)
        self.scene_obj.addItem(node)
        self.nodes[n_id] = node
        self._similarity_signature = None
        
        if persist:
            self._mark_workspace_dirty(autosave=True)
            self.update_ghost_connections()
            self._queue_background_embedding(node)
        return node

    def add_node(self, node: Node, position: QPointF):
        self.scene_obj.addItem(node)
        node.setPos(position)
        self.nodes[node.node_id] = node
        self.update_scene_bounds()
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()
        self._queue_background_embedding(node)

    def add_edge(self, source_node: Node, target_node: Node, label=""):
        edge = Edge(source_node, target_node, label)
        self.scene_obj.addItem(edge)
        self.edges.append(edge)
        source_node.edges.append(edge)
        target_node.edges.append(edge)
        self._mark_workspace_dirty(autosave=True)

    def update_scene_bounds(self):
        if not self.nodes:
            self.scene_obj.setSceneRect(-1500, -1500, 3000, 3000)
            return
        rect = self.scene_obj.itemsBoundingRect()
        buffer = 1500
        rect.adjust(-buffer, -buffer, buffer, buffer)
        self.scene_obj.setSceneRect(rect)

    def _get_workspace_id(self):
        return self.current_workspace_id

    def create_ai_menu(self, parent_widget):
        menu = QMenu("🤖 AI Tools", parent_widget)
        menu.setTitle("🤖 AI Tools") 
        ai_enabled = False
        try:
            ai_enabled = self.main_window.shared_llm_manager.ai_enabled
        except: pass

        if not ai_enabled:
            disabled_action = menu.addAction("⚠️ AI Not Installed")
            disabled_action.setEnabled(False)
            menu.setEnabled(False)
            menu.setToolTip("Run the installer to download local AI models.")
            return menu
            
        action_categorize = menu.addAction("✨ Organize Selected Nodes")
        action_find_connections = menu.addAction("🔗 Find New Connections")
        action_generate_outline = menu.addAction("📝 Generate Outline")
        action_identify_weakpoints = menu.addAction("🔍 Identify Weakpoints")
        action_fill_graph = menu.addAction("🕸️ Fill Out Graph")
        action_consolidate = menu.addAction("🏗️ Consolidate Notes")
        
        action_categorize.triggered.connect(self._organize_selection_ai)
        action_find_connections.triggered.connect(self._find_connections_ai)
        action_generate_outline.triggered.connect(self._generate_outline_ai)
        action_identify_weakpoints.triggered.connect(self._weakpoints_ai)
        action_fill_graph.triggered.connect(self._fill_graph_ai)
        action_consolidate.triggered.connect(self._consolidate_nodes_ai)
        
        return menu

    def update_theme(self, theme):
        self.setBackgroundBrush(QBrush(QColor(theme['canvas'])))
        self.loading_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 180); border-radius: 8px;")
        self.loading_label.setStyleSheet(f"color: {theme['success']}; font-size: 26px; font-weight: bold; background: transparent;")

        if hasattr(self, 'toolbar_frame'):
            self.toolbar_frame.setStyleSheet(f"""
                QFrame#WorkspaceToolbar {{ background-color: {theme['bg_panel']}; border: 1px solid {theme['border']}; border-radius: 8px; }}
                QLabel {{ color: {theme['text_main']}; font-weight: bold; }}
                QComboBox {{ background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px; border-radius: 4px; font-weight: bold; min-width: 150px; }}
                QComboBox QAbstractItemView {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; }}
                QComboBox QAbstractItemView::item {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; padding: 4px; }}
                QComboBox QAbstractItemView::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
                QPushButton {{ background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
                QPushButton:hover {{ background-color: {theme['accent_hover']}; }}
                QPushButton::menu-indicator {{ image: none; }}
                QLabel#CollapsingIcon {{ background-color: {theme['accent']}; color: #ffffff; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
                QCheckBox {{ color: {theme['text_main']}; font-weight: bold; background: transparent; }}
                QSlider::groove:horizontal {{ height: 4px; background: {theme['border']}; border-radius: 2px; }}
                QSlider::handle:horizontal {{ background: {theme['accent']}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
                QMenu {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; font-weight: bold; padding: 5px; }}
                QMenu::item {{ padding: 6px 20px 6px 20px; border-radius: 4px; }}
                QMenu::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
            """)

    def _refresh_pdf_list(self):
        checked_data = self.filter_combo.get_checked_items()
        if not checked_data and self.filter_combo.count() == 0:
            checked_data = ["ALL"]
            
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All PDFs", "ALL", checked=("ALL" in checked_data))
        
        pm = getattr(self.main_window, 'project_manager', None)
        if pm:
            for pdf in pm.pdfs:
                full_name = os.path.basename(pdf)
                display_name = (full_name[:16] + "\u2026") if len(full_name) > 18 else full_name
                self.filter_combo.addItem(display_name, pdf, checked=(pdf in checked_data))
                
        self.filter_combo.blockSignals(False)

    def _refresh_tag_list(self, forced_checked=None):
        checked_data = list(forced_checked) if forced_checked is not None else self.tag_filter_combo.get_checked_items()
        if not checked_data and self.tag_filter_combo.count() == 0:
            checked_data = ["ALL_TAGS"]

        self.tag_filter_combo.blockSignals(True)
        self.tag_filter_combo.clear()
        self.tag_filter_combo.addItem("All Tags", "ALL_TAGS", checked=("ALL_TAGS" in checked_data))

        pm = getattr(self.main_window, "project_manager", None)
        all_tags = pm.get_all_tags() if pm else []
        for tag in all_tags:
            tag_name = tag.get("name")
            if tag_name:
                self.tag_filter_combo.addItem(tag_name, tag_name, checked=(tag_name in checked_data))

        self.tag_filter_combo.blockSignals(False)

    def apply_tag_filter(self, tag_name):
        if not tag_name: return
        self._refresh_tag_list(forced_checked=[tag_name])
        self._apply_filter()

    def reset_filters(self):
        self._refresh_pdf_list()
        self._refresh_tag_list(forced_checked=["ALL_TAGS"])

        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All PDFs", "ALL", checked=True)
        pm = getattr(self.main_window, 'project_manager', None)
        if pm:
            for pdf in pm.pdfs:
                full_name = os.path.basename(pdf)
                display_name = (full_name[:16] + "\u2026") if len(full_name) > 18 else full_name
                self.filter_combo.addItem(display_name, pdf, checked=False)
        self.filter_combo.blockSignals(False)
        self._apply_filter()

    def get_allowed_docs(self):
        checked = self.filter_combo.get_checked_items()
        pm = getattr(self.main_window, 'project_manager', None)
        if "ALL" in checked or not checked:
            return [os.path.basename(p) for p in pm.pdfs] if pm else []
        return [os.path.basename(p) for p in checked if p != "ALL"]

    def _apply_filter(self):
        checked_pdfs = self.filter_combo.get_checked_items()
        show_all_pdfs = "ALL" in checked_pdfs or not checked_pdfs
        
        checked_tags = self.tag_filter_combo.get_checked_items() if hasattr(self, "tag_filter_combo") else ["ALL_TAGS"]
        show_all_tags = "ALL_TAGS" in checked_tags or not checked_tags
        
        is_filtered = not show_all_pdfs or not show_all_tags
        if hasattr(self, 'btn_clear_filter'):
            self.btn_clear_filter.setVisible(is_filtered)
        
        for node in self.nodes.values():
            pdf_ok = True if show_all_pdfs else (node.pdf_path is None or node.pdf_path in checked_pdfs)
            tag_ok = True if show_all_tags else any(t in (node.get_tag_names() if hasattr(node, "get_tag_names") else []) for t in checked_tags)
            node.setVisible(pdf_ok and tag_ok)
                    
        for edge in self.edges:
            edge.setVisible(edge.source_node.isVisible() and edge.dest_node.isVisible())

        self.update_ghost_connections()

    def _build_similarity_matrix_if_needed(self):
        node_items = sorted(self.nodes.values(), key=lambda n: n.node_id)
        signature = tuple((n.node_id, (n.quote or ""), (n.note or "")) for n in node_items)
        if signature == self._similarity_signature: return

        self._similarity_signature = signature
        self.similarity_matrix = {}

        if len(node_items) < 2: return

        llm_manager = getattr(self.main_window, 'shared_llm_manager', None)
        if not llm_manager or not llm_manager.ai_enabled: return

        node_ids = [n.node_id for n in node_items]
        texts_to_embed = [f"{n.quote} {n.note}".strip() for n in node_items]
        pm = self.main_window.project_manager
        
        self.similarity_matrix = get_semantic_similarity_matrix(node_ids, texts_to_embed, llm_manager, pm)

    def update_ghost_connections(self):
        if self._updating_ghost_links: return

        self._updating_ghost_links = True
        try:
            for line_item in self.ghost_lines:
                if line_item and line_item.scene() is self.scene_obj:
                    self.scene_obj.removeItem(line_item)
            self.ghost_lines.clear()

            if not hasattr(self, "chk_show_ghost_links") or not self.chk_show_ghost_links.isChecked(): return

            self._build_similarity_matrix_if_needed()
            threshold = self.slider_ghost_threshold.value() / 100.0
            selected_ids = {n.node_id for n in self.scene_obj.selectedItems() if isinstance(n, Node)}
            node_list = [n for n in self.nodes.values() if n.isVisible()]

            seen_pairs = set()
            for i, node_a in enumerate(node_list):
                for node_b in node_list[i + 1:]:
                    pair_key = (min(node_a.node_id, node_b.node_id), max(node_a.node_id, node_b.node_id))
                    if pair_key in seen_pairs: continue
                    seen_pairs.add(pair_key)

                    sim_score = self.similarity_matrix.get(node_a.node_id, {}).get(node_b.node_id)
                    if sim_score is None:
                        sim_score = self.similarity_matrix.get(node_b.node_id, {}).get(node_a.node_id)
                    if sim_score is None or sim_score <= threshold: continue

                    p1 = node_a.mapToScene(node_a.rect().center())
                    p2 = node_b.mapToScene(node_b.rect().center())

                    pen = QPen(QColor("#e8e8ff"), 3, Qt.PenStyle.DashLine) if (node_a.node_id in selected_ids or node_b.node_id in selected_ids) else QPen(QColor("#9090d0"), 2, Qt.PenStyle.DashLine)

                    line_item = GhostLineItem(p1.x(), p1.y(), p2.x(), p2.y(), node_a.node_id, node_b.node_id, sim_score, self)
                    line_item.setPen(pen)
                    line_item.setZValue(-1)
                    self.scene_obj.addItem(line_item)
                    self.ghost_lines.append(line_item)

                    mid_x, mid_y = (p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2
                    text_item = self.scene_obj.addText(f"{int(sim_score * 100)}%")
                    lbl_font = QFont()
                    lbl_font.setPointSize(7)
                    text_item.setFont(lbl_font)
                    text_item.setDefaultTextColor(pen.color())
                    text_item.setPos(mid_x - text_item.boundingRect().width() / 2, mid_y - text_item.boundingRect().height() / 2)
                    text_item.setZValue(-0.5)
                    self.ghost_lines.append(text_item)
        finally:
            self._updating_ghost_links = False

    def _on_scene_changed(self, *args):
        if self._updating_ghost_links: return
        if not hasattr(self, "chk_show_ghost_links") or not self.chk_show_ghost_links.isChecked(): return
        self.update_ghost_connections()

    def _sync_workspace(self):
        pm = getattr(self.main_window, 'project_manager', None)
        if not pm or not pm.project_filepath: return
        try:
            workspace_model = pm.get_workspace_data(self.current_workspace_id)
            all_annots = pm.get_highlights()
            self._populate_workspace_tabs()
            self.sync_with_project(workspace_model, all_annots)
        except Exception as e:
            print(f"Error syncing workspace: {e}")

    def _convert_ghost_to_edge(self, source_id, target_id, sim_score):
        if source_id not in self.nodes or target_id not in self.nodes: return
        src_node, tgt_node = self.nodes[source_id], self.nodes[target_id]

        for existing in self.edges:
            if {existing.source_node, existing.dest_node} == {src_node, tgt_node}: return

        self.save_state_for_undo()
        edge = Edge(src_node, tgt_node, f"~{int(sim_score * 100)}% similar")
        self.scene_obj.addItem(edge)
        self.edges.append(edge)
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()

    def open_unused_highlights_dialog(self):
        pm = getattr(self.main_window, 'project_manager', None)
        if not pm: return
        unused_highlights = pm.get_unused_highlights(self.current_workspace_id)

        if not unused_highlights:
            QMessageBox.information(self, "Unused Highlights", "No unused highlights found.")
            return

        dialog = UnusedHighlightsDialog(unused_highlights, self)
        if hasattr(self.main_window, 'theme_manager'):
            theme = self.main_window.theme_manager.get_theme()
            dialog.setStyleSheet(f"""
                QDialog {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}
                QLabel {{ color: {theme['text_main']}; font-weight: bold; }}
                QListWidget {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; }}
                QListWidget::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
                QPushButton {{ background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
            """)

        if dialog.exec() != QDialog.DialogCode.Accepted: return
        highlight_ids = dialog.get_selected_highlight_ids()
        if not highlight_ids: return

        self.save_state_for_undo()
        view_center = self.mapToScene(self.viewport().rect().center())
        last_node, offset = None, 0
        
        for highlight_id in highlight_ids:
            highlight = pm.get_highlight(highlight_id)
            if not highlight: continue
                
            actual_note, pdf_path, page_num = "", highlight.get("doc_id"), highlight.get("page_num")
            if pdf_path and page_num is not None:
                doc = pm.get_doc(pdf_path)
                if doc:
                    page = doc.load_page(page_num)
                    for annot in page.annots():
                        if annot.info and annot.info.get("title") == highlight["id"]:
                            actual_note = annot.info.get("content", "")
                            break
                            
            node = self.add_node_from_annotation(
                {
                    "id": highlight["id"], "subject": highlight.get("text_content", ""),
                    "content": actual_note, "pdf_path": pdf_path, "page_num": page_num,
                    "color": highlight.get("color"),
                }, persist=True, position=QPointF(view_center.x(), view_center.y() + offset),
            )
            if node:
                last_node = node
                offset += 120

        self.scene_obj.clearSelection()
        if last_node: last_node.setSelected(True)
        self.update_scene_bounds()

    # ------------------------------------------------------------------ workspace tabs

    def _populate_workspace_tabs(self):
        if not hasattr(self, 'workspace_combo'): return
        pm = getattr(self.main_window, 'project_manager', None)
        if not pm or not pm.project_filepath: return

        self.workspace_combo.blockSignals(True)
        try:
            self.workspace_combo.clear()
            current_index = 0
            for i, ws in enumerate(pm.get_workspaces()):
                self.workspace_combo.addItem(ws["name"], ws["id"])
                if ws["id"] == self.current_workspace_id:
                    current_index = i
            self.workspace_combo.setCurrentIndex(current_index)
        finally:
            self.workspace_combo.blockSignals(False)

    def _on_tab_changed(self, index):
        new_ws_id = self.workspace_combo.itemData(index)
        if new_ws_id is None or new_ws_id == self.current_workspace_id: return

        self._mark_workspace_dirty(autosave=True)
        self.current_workspace_id = new_ws_id
        self._similarity_signature = None

        pm = getattr(self.main_window, 'project_manager', None)
        if not pm or not pm.project_filepath: return

        self.sync_with_project(pm.get_workspace_data(self.current_workspace_id), pm.get_highlights())

    def _add_workspace(self):
        pm = getattr(self.main_window, 'project_manager', None)
        if not pm or not pm.project_filepath:
            QMessageBox.information(self, "No Project", "Please open a project first.")
            return

        name, ok = QInputDialog.getText(self, "New Workspace", "Enter workspace name:")
        if not ok or not name.strip(): return

        new_id = pm.create_workspace(name.strip())
        if new_id is None:
            QMessageBox.warning(self, "Error", "Could not create workspace.")
            return

        self.workspace_combo.blockSignals(True)
        self.workspace_combo.addItem(name.strip(), new_id)
        self.workspace_combo.blockSignals(False)
        self.workspace_combo.setCurrentIndex(self.workspace_combo.count() - 1)

    def _open_color_by_pdf_dialog(self):
        pm = getattr(self.main_window, 'project_manager', None)
        if not pm: return
        
        pdfs, tags = pm.pdfs, pm.get_all_tags()
        if not pdfs and not tags:
            QMessageBox.information(self, "Nothing to Color", "There are no PDFs or Tags in this project.")
            return
            
        current_pdf_colors = {pdf: "#2b2b2b" for pdf in pdfs}
        for node in self.nodes.values():
            if node.pdf_path: current_pdf_colors[node.pdf_path] = node.color
                
        current_tag_colors = {t.get("name"): t.get("color", "#808080") for t in tags if t.get("name")}
        dialog = ColorOrganizerDialog(pdfs, tags, current_pdf_colors, current_tag_colors, self)
        
        if hasattr(self.main_window, 'theme_manager'):
            theme = self.main_window.theme_manager.get_theme()
            dialog.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")
            dialog.tab_widget.setStyleSheet(f"""
                QTabWidget::pane {{ border: 1px solid {theme['border']}; border-radius: 4px; }}
                QTabBar::tab {{ background: {theme['bg_panel']}; color: {theme['text_main']}; padding: 8px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
                QTabBar::tab:selected {{ background: {theme['accent']}; color: #ffffff; font-weight: bold; }}
            """)
            
        if dialog.exec():
            mode, new_colors = dialog.get_result()
            self.save_state_for_undo()
            
            if mode == "pdf":
                for node in self.nodes.values():
                    if node.pdf_path and node.pdf_path in new_colors:
                        node.color = new_colors[node.pdf_path]
                        node.setBrush(QBrush(QColor(node.color)))
                        node.refresh_layout()
            elif mode == "tag":
                for node in self.nodes.values():
                    for tag_name in (node.get_tag_names() if hasattr(node, "get_tag_names") else []):
                        if tag_name in new_colors:
                            node.color = new_colors[tag_name]
                            node.setBrush(QBrush(QColor(node.color)))
                            node.refresh_layout()
                            break 
                            
            self._mark_workspace_dirty(autosave=True)

    def trigger_declutter(self):
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", "Please add or select some nodes to declutter.")
            return

        dialog = DeclutterSettingsDialog(self)
        if not dialog.exec(): return 
        use_ai, semantic_strength = dialog.get_settings()

        llm_manager = None
        if use_ai:
            try:
                temp_manager = getattr(self.main_window, 'shared_llm_manager', None)
                if temp_manager and temp_manager.ai_enabled: llm_manager = temp_manager
                else: QMessageBox.information(self, "AI Disabled", "Ollama is not running. Falling back to standard math declutter.")
            except Exception: pass

        similarity_matrix = {}
        if llm_manager:
            node_ids = [n.node_id for n in target_nodes]
            texts_to_embed = [f"{n.quote} {n.note}".strip() for n in target_nodes]
            similarity_matrix = get_semantic_similarity_matrix(node_ids, texts_to_embed, llm_manager, getattr(self.main_window, 'project_manager', None))

        nodes_info = {n.node_id: {'width': n.base_width, 'height': n.base_height} for n in target_nodes}
        edges_info = [(e.source_node.node_id, e.dest_node.node_id) for e in self.edges if e.source_node in target_nodes and e.dest_node in target_nodes]
        avg_x = sum(n.pos().x() + n.base_width / 2 for n in target_nodes) / len(target_nodes)
        avg_y = sum(n.pos().y() + n.base_height / 2 for n in target_nodes) / len(target_nodes)

        self.save_state_for_undo()
        new_positions = calculate_force_directed_layout(nodes_info, edges_info, avg_x, avg_y, similarity_matrix=similarity_matrix, semantic_strength=semantic_strength)

        if new_positions:
            for node in target_nodes:
                if node.node_id in new_positions:
                    pos = new_positions[node.node_id]
                    node.setPos(pos['x'], pos['y'])
            for edge in self.edges:
                if edge.source_node in target_nodes and edge.dest_node in target_nodes:
                    edge.update_position()
            self._mark_workspace_dirty(autosave=True)
            self.update_scene_bounds()

    def _export_workspace(self):
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

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
            if hasattr(node, 'proxy_toolbar'): node.proxy_toolbar.hide()
            if hasattr(node, 'resize_handle'): node.resize_handle.hide()

        bounding_rect = QRectF()
        for item in target_nodes + target_edges:
            bounding_rect = bounding_rect.united(item.sceneBoundingRect())

        bounding_rect.adjust(-40, -40, 40, 40)
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Workspace", "workspace_export.png", "PNG Image (*.png);;JPEG Image (*.jpg)")

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

        for item, was_visible in visibility_states.items(): item.setVisible(was_visible)
        for item in original_selection: item.setSelected(True)
        for node in target_nodes: node.refresh_layout()

    # ------------------------------------------------------------------ undo / redo
    
    def save_state_for_undo(self):
        if self.is_restoring: return
        state_model = self.serialize_workspace()
        state_str = json.dumps(dataclasses.asdict(state_model), sort_keys=True)
        
        if not self.undo_stack or self.undo_stack[-1][0] != state_str:
            self.undo_stack.append((state_str, state_model))
            if len(self.undo_stack) > 50: self.undo_stack.pop(0)
            self.redo_stack.clear()
            self._update_buttons()

    def _update_buttons(self):
        if hasattr(self, 'btn_undo'): self.btn_undo.setEnabled(len(self.undo_stack) > 0)
        if hasattr(self, 'btn_redo'): self.btn_redo.setEnabled(len(self.redo_stack) > 0)

    def undo(self):
        if not self.undo_stack: return
        self.is_restoring = True
        
        current_state = self.serialize_workspace()
        current_str = json.dumps(dataclasses.asdict(current_state), sort_keys=True)
        self.redo_stack.append((current_str, current_state))
        
        _, prev_state = self.undo_stack.pop()
        self.sync_with_project(prev_state, getattr(self.main_window.project_manager, "get_highlights", lambda: {})())
        
        self.is_restoring = False
        self._update_buttons()
        self._mark_workspace_dirty(autosave=True)

    def redo(self):
        if not self.redo_stack: return
        self.is_restoring = True
        
        current_state = self.serialize_workspace()
        current_str = json.dumps(dataclasses.asdict(current_state), sort_keys=True)
        self.undo_stack.append((current_str, current_state))
        
        _, next_state = self.redo_stack.pop()
        self.sync_with_project(next_state, getattr(self.main_window.project_manager, "get_highlights", lambda: {})())
        
        self.is_restoring = False
        self._update_buttons()
        self._mark_workspace_dirty(autosave=True)

    # ------------------------------------------------------------------ clipboard

    def copy_selection(self):
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        if not selected_nodes: return
        
        selected_node_set = set(selected_nodes)
        self.clipboard['nodes'] = [{
            'old_id': n.node_id, 'highlight_id': n.highlight_id, 'quote': n.quote, 'note_text': n.note,
            'color': n.color, 'is_custom': n.is_custom, 'pdf_path': n.pdf_path, 'page_num': n.page_num,
            'manual_font_size': n.manual_font_size, 'width': n.base_width, 'height': n.base_height,
            'x': n.pos().x(), 'y': n.pos().y(), "tags": n.get_tag_names() if hasattr(n, "get_tag_names") else [],
        } for n in selected_nodes]
        
        self.clipboard['edges'] = [{
            'source_old_id': e.source_node.node_id, 'dest_old_id': e.dest_node.node_id,
            'label': e.label_text, 'color': e.base_color.name(), 'weight': e.weight,
        } for e in self.edges if e.source_node in selected_node_set and e.dest_node in selected_node_set]

    def cut_selection(self):
        self.copy_selection()
        if self.clipboard['nodes']: self.delete_selected_nodes()

    def paste_selection(self):
        clipboard_text = QApplication.clipboard().text()
        if "<workspace_graph>" in clipboard_text and "</workspace_graph>" in clipboard_text:
            # Removed parsing logic here since AI parsing is offloaded, but left hook if needed
            return
            
        if not self.clipboard['nodes']: return
        self.save_state_for_undo()
        
        offset = 20
        id_mapping = {}
        new_nodes = []
        
        for data in self.clipboard['nodes']:
            new_id = f"custom_{uuid.uuid4()}"
            id_mapping[data['old_id']] = new_id
            node = Node(
                new_id, data['quote'], data['note_text'], color=data['color'], is_custom=data['is_custom'],
                width=data['width'], height=data['height'], pdf_path=data['pdf_path'],
                page_num=data['page_num'], manual_font_size=data['manual_font_size'], highlight_id=data['highlight_id']
            )
            node.setPos(data['x'] + offset, data['y'] + offset)
            self.scene_obj.addItem(node)
            self.nodes[new_id] = node
            new_nodes.append(node)
            self._queue_background_embedding(node)

        for edata in self.clipboard['edges']:
            src_id = id_mapping.get(edata['source_old_id'])
            tgt_id = id_mapping.get(edata['dest_old_id'])
            if src_id and tgt_id and src_id in self.nodes and tgt_id in self.nodes:
                edge = Edge(self.nodes[src_id], self.nodes[tgt_id], edata['label'], str(uuid.uuid4()), edata['color'], edata['weight'])
                self.scene_obj.addItem(edge)
                self.edges.append(edge)

        self.scene_obj.clearSelection()
        for node in new_nodes: node.setSelected(True)

        self._similarity_signature = None
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()
        self.update_scene_bounds()

    def keyPressEvent(self, event):
        if self.scene_obj.focusItem() is not None:
            super().keyPressEvent(event)
            return

        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Z:
                self.undo()
                return
            elif event.key() == Qt.Key.Key_Y:
                self.redo()
                return

        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected_nodes()
            event.accept()
            return
            
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier):
            if event.angleDelta().y() > 0: self.zoom_in()
            else: self.zoom_out()
        else:
            super().wheelEvent(event)
            
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'ai_overlay') and self.ai_overlay.isVisible(): self.ai_overlay._reposition()
        if hasattr(self, 'loading_overlay') and not self.loading_overlay.isHidden(): self.loading_overlay.resize(self.viewport().size())
        if hasattr(self, 'toolbar_frame'): self.toolbar_frame.move(15, 15)

    def zoom_in(self): self.scale(1.15, 1.15)
    def zoom_out(self): self.scale(1 / 1.15, 1 / 1.15)

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
                self.connecting_node.setPen(QPen(QColor("#ffffff" if self.connecting_node.isSelected() else "#555555"), 4 if self.connecting_node.isSelected() else 2))
                self.connecting_node = None
                event.accept()
                return

        if event.button() == Qt.MouseButton.LeftButton and event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            item = self.itemAt(event.pos())
            while item and not isinstance(item, (Node, Edge)): item = item.parentItem()
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
        self.update_scene_bounds()

    def delete_edge(self, edge):
        if edge in edge.source_node.edges: edge.source_node.edges.remove(edge)
        if edge in edge.dest_node.edges: edge.dest_node.edges.remove(edge)
        self.scene_obj.removeItem(edge)
        edge.source_node = None
        edge.dest_node = None
        if edge in self.edges: self.edges.remove(edge)
        if hasattr(edge, 'deleteLater'): edge.deleteLater()
        else:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: edge.setParentItem(None))
        self._mark_workspace_dirty(autosave=True)

    def delete_selected_nodes(self):
        nodes_to_delete = [item for item in list(self.scene_obj.selectedItems()) if isinstance(item, Node)]
        if not nodes_to_delete: return
        self.save_state_for_undo()

        for node in nodes_to_delete:
            for edge in list(node.edges): self.delete_edge(edge)
            self.scene_obj.removeItem(node)
            node.edges = []
            if node.node_id in self.nodes: del self.nodes[node.node_id]
            if hasattr(node, 'deleteLater'): node.deleteLater()

        self._similarity_signature = None
        self.update_ghost_connections()
        self._mark_workspace_dirty(autosave=True)

    def delete_node(self, node, delete_highlight=False):
        if delete_highlight and node.highlight_id:
            self._delete_highlight_permanently(node.highlight_id)
            return

        for edge in list(node.edges): self.delete_edge(edge)
        self.scene_obj.removeItem(node)
        node.edges = []
        if node.node_id in self.nodes: del self.nodes[node.node_id]
        if hasattr(node, 'deleteLater'): node.deleteLater()

        self._similarity_signature = None
        self.update_ghost_connections()
        self._mark_workspace_dirty(autosave=True)

    def _delete_highlight_permanently(self, highlight_id):
        nodes_to_remove = [node for node in list(self.nodes.values()) if node.highlight_id == highlight_id or node.node_id == highlight_id]
        pm = getattr(self.main_window, 'project_manager', None)
        highlight_record = pm.get_highlight(highlight_id) if pm else None

        for node in nodes_to_remove:
            for edge in list(node.edges): self.delete_edge(edge)
            self.scene_obj.removeItem(node)
            node.edges = []
            self.nodes.pop(node.node_id, None)
            if hasattr(node, 'deleteLater'): node.deleteLater()

        pdf_path, page_num = None, None
        if highlight_record:
            pdf_path, page_num = highlight_record.get("doc_id"), highlight_record.get("page_num")
        elif nodes_to_remove:
            pdf_path, page_num = nodes_to_remove[0].pdf_path, nodes_to_remove[0].page_num

        if pm and pdf_path is not None and page_num is not None:
            try:
                doc = pm.get_doc(pdf_path)
                if doc:
                    page = doc.load_page(page_num)
                    for annot in page.annots():
                        if annot.info and annot.info.get("title") == highlight_id:
                            page.delete_annot(annot)
                            break
                    pm.mark_dirty(pdf_path)
                    if pdf_path == self.main_window.current_file_path and hasattr(self.main_window, 'viewer'):
                        self.main_window.viewer.reload_page(page_num)
            except Exception as e:
                print(f"Error removing physical annotation: {e}")

            if hasattr(self.main_window, 'notes_docks'):
                for n_dock in self.main_window.notes_docks: n_dock.refresh_notes()

            pm.delete_highlight_record(highlight_id)

        self._similarity_signature = None
        self.update_ghost_connections()
        self._mark_workspace_dirty(autosave=True)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        while item and not isinstance(item, (Node, Edge)): item = item.parentItem()
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]

        if len(selected_nodes) > 1 and (item is None or (isinstance(item, Node) and item in selected_nodes)):
            menu = QMenu(self)
            remove_action = menu.addAction("🗑️ Remove Selected from Workspace")
            delete_action = menu.addAction("🔥 Delete Selected Highlights Permanently")
            color_action = menu.addAction("🎨 Change Color for Selected Nodes")
            manage_tags_action = menu.addAction("🏷️ Manage Tags for Selected Nodes")
            declutter_action = menu.addAction("🧹 Declutter Selected Nodes")
            
            remove_action.triggered.connect(self.delete_selected_nodes)
            menu.addSeparator()
            menu.addMenu(self.create_ai_menu(menu))
            
            action = menu.exec(event.globalPos())
            if action == delete_action:
                self.save_state_for_undo()
                for highlight_id in {n.highlight_id for n in selected_nodes if n.highlight_id}:
                    self._delete_highlight_permanently(highlight_id)
            elif action == color_action: self._change_color_for_nodes(selected_nodes)
            elif action == manage_tags_action: self._manage_tags_for_nodes(selected_nodes)
            elif action == declutter_action: self.trigger_declutter()
            return
            
        if isinstance(item, Node):
            prior_selected = [n for n in selected_nodes]
            if item not in selected_nodes:
                self.scene_obj.clearSelection()
                item.setSelected(True)
                selected_nodes = [item]

            connect_source = prior_selected[0] if (len(prior_selected) == 1 and prior_selected[0] is not item) else None

            menu = QMenu(self)
            edit_action = menu.addAction("✏️ Edit Note Text")
            color_action = menu.addAction("🎨 Change Color")
            manage_tags_action = menu.addAction("🏷️ Manage Tags")
            cite_action = menu.addAction("📋 Copy In-Text Citation")
            connect_action = menu.addAction("🔗 Connect to Selected Node") if connect_source else None
            remove_action = menu.addAction("🗑️ Remove Selected from Workspace")
            delete_highlight_action = menu.addAction("🔥 Delete Highlight Permanently") if item.highlight_id else None
            declutter_action = menu.addAction("🧹 Declutter Selected Node")
            
            remove_action.triggered.connect(self.delete_selected_nodes)
            menu.addSeparator()
            menu.addMenu(self.create_ai_menu(menu))
            
            action = menu.exec(event.globalPos())
            if action == edit_action: item.trigger_edit()
            elif action == color_action: item.trigger_color_change()
            elif action == manage_tags_action:
                if TagAssignmentDialog(self.main_window.project_manager, item.node_id, "node", self).exec():
                    item.refresh_tag_badges()
                    self._refresh_tag_list()
                    self._apply_filter()
            elif connect_action and action == connect_action:
                self.save_state_for_undo()
                self.connecting_node = connect_source
                self.finish_connection(item)
            elif delete_highlight_action and action == delete_highlight_action:
                self.save_state_for_undo()
                self.delete_node(item, delete_highlight=True)
            elif action == declutter_action: self.trigger_declutter()
            elif action == cite_action:
                if item.pdf_path is not None:
                    citation_text = self.main_window.citation_manager.format_in_text(item.pdf_path, item.page_num)
                    QApplication.clipboard().setText(citation_text)
                    self.main_window.statusBar().showMessage(f"Copied citation: {citation_text}", 3000)
                else:
                    QMessageBox.warning(self, "No Citation", "This is a custom node, not a PDF highlight.")
            return
            
        if isinstance(item, Edge):
            menu = QMenu(self)
            edit_action = menu.addAction("✏️ Edit Connection Text")
            color_action = menu.addAction("🎨 Change Line Color")
            weight_action = menu.addAction("📏 Change Line Weight")
            del_action = menu.addAction("🗑️ Delete Connection")
            
            menu.addSeparator()
            menu.addMenu(self.create_ai_menu(menu))
            
            action = menu.exec(event.globalPos())
            if action == edit_action: item.trigger_edit()
            elif action == color_action: item.trigger_color_change()
            elif action == weight_action: item.trigger_weight_change()
            elif action == del_action:
                self.save_state_for_undo()
                self.delete_edge(item)
            return

        if item is None:
            menu = QMenu(self)
            declutter_action = menu.addAction("🧹 Declutter All Notes")
            analysis_menu = menu.addMenu("Related to Tag")
            
            pm = getattr(self.main_window, 'project_manager', None)
            current_tags = pm.get_all_tags() if pm else []
            if current_tags:
                for tag in current_tags:
                    tag_name = tag.get("name")
                    if tag_name:
                        tag_sub = analysis_menu.addMenu(f"'{tag_name}'")
                        tag_sub.addAction("🔍 Find Relatives").triggered.connect(lambda checked, t=tag_name: self.trigger_find_tag_relatives(t))
                        tag_sub.addAction("⚖️ Find Opposing Views").triggered.connect(lambda checked, t=tag_name: self.trigger_tag_opposing_views(t))
            else:
                analysis_menu.addAction("No tags created yet").setEnabled(False)
            
            menu.addSeparator()
            menu.addMenu(self.create_ai_menu(menu))
            
            if menu.exec(event.globalPos()) == declutter_action:
                self.trigger_declutter()
            return

        super().contextMenuEvent(event)

    def _change_color_for_nodes(self, nodes):
        if not nodes: return
        initial_color = QColor(nodes[0].color)
        color = QColorDialog.getColor(initial_color, self, "Select Color for Selected Nodes")
        
        if color.isValid():
            self.save_state_for_undo()
            color_name = color.name()
            for node in nodes:
                node.color = color_name
                node.setBrush(QBrush(QColor(color_name)))
                node.refresh_layout()
                if not getattr(node, 'is_custom', False) and getattr(node, 'pdf_path', None) is not None:
                    annot_id = getattr(node, 'highlight_id', None) or getattr(node, 'node_id', None)
                    if hasattr(self.main_window, 'notes_docks'):
                        for notes_dock in self.main_window.notes_docks:
                            notes_dock._modify_note(node.pdf_path, node.page_num, annot_id, action="edit_content", content=getattr(node, 'note', ''), refresh=False)
            self._mark_workspace_dirty(autosave=True)

    def trigger_find_tag_relatives(self, tag_name):
        pm, llm = getattr(self.main_window, 'project_manager', None), getattr(self.main_window, 'shared_llm_manager', None)
        if not pm or not llm: return
        
        if not llm.collection and pm.project_filepath: llm.set_project_database(pm.project_filepath)
        if not llm.collection or llm.collection.count() == 0:
            QMessageBox.warning(self, "No Database", "Search index is empty. Please build it first.")
            return

        target_nodes = [n for n in self.nodes.values() if tag_name in (n.get_tag_names() if hasattr(n, "get_tag_names") else [])]
        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", f"No nodes found with the tag '{tag_name}'.")
            return

        node_ids = [n.node_id for n in target_nodes]
        vectors = list(pm.get_node_embeddings_batch(node_ids).values())
        if len(vectors) < len(target_nodes):
            try: vectors = llm.get_batch_embeddings([f"{n.quote} {n.note}".strip() for n in target_nodes])
            except Exception: pass
                
        if not vectors:
            QMessageBox.warning(self, "Error", "Could not generate embeddings. Ensure AI is running.")
            return

        centroid_vector = [sum(col) / len(vectors) for col in zip(*vectors)]
        results = llm.query_by_raw_embedding(centroid_vector, n_results=5, allowed_docs=self.get_allowed_docs())

        if not results or not results.get('documents') or not results['documents'][0]:
            QMessageBox.information(self, "No Results", "Could not find related chunks.")
            return
            
        matches = [{"text": doc_text.strip(), "doc_name": meta.get('doc_name', 'Unknown Document'), "page": meta.get('page', 0)} for doc_text, meta in zip(results['documents'][0], results['metadatas'][0])]
        AIResultsDialog(f"Related to '{tag_name}'", matches, self.main_window, self).exec()

    def trigger_tag_opposing_views(self, tag_name):
        pm, llm = getattr(self.main_window, 'project_manager', None), getattr(self.main_window, 'shared_llm_manager', None)
        if not pm or not llm: return

        if not llm.collection and pm.project_filepath: llm.set_project_database(pm.project_filepath)
        if not llm.collection or llm.collection.count() == 0:
            QMessageBox.warning(self, "No Database", "Search index is empty. Please build it first.")
            return

        target_nodes = [n for n in self.nodes.values() if tag_name in (n.get_tag_names() if hasattr(n, "get_tag_names") else [])]
        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", f"No nodes found with the tag '{tag_name}'.")
            return

        target_statement = f"The core arguments of the tag '{tag_name}': {' '.join([f'{n.quote} {getattr(n, "note", "")}'.strip() for n in target_nodes])[:1000]}"
        vectors = list(pm.get_node_embeddings_batch([n.node_id for n in target_nodes]).values())
        if len(vectors) < len(target_nodes):
            try: vectors = llm.get_batch_embeddings([f"{n.quote} {getattr(n, 'note', '')}".strip() for n in target_nodes])
            except Exception: pass

        if not vectors:
            QMessageBox.warning(self, "Error", "Could not generate embeddings. Ensure AI is running.")
            return

        try:
            results = llm.query_by_raw_embedding([sum(col) / len(vectors) for col in zip(*vectors)], n_results=30, allowed_docs=self.get_allowed_docs())
            if not results or not results.get('documents') or not results['documents'][0]:
                QMessageBox.information(self, "No Results", "Could not find related chunks to analyze.")
                return
            documents, metadatas = results['documents'][0], results['metadatas'][0]
        except Exception as e:
            QMessageBox.critical(self, "Database Error", str(e))
            return

        active_model = self.main_window.chat_docks[0].model_combo.currentText() if hasattr(self.main_window, 'chat_docks') and self.main_window.chat_docks else None
        
        from PySide6.QtWidgets import QProgressDialog
        self.loading_dialog = QProgressDialog("Initializing AI...", "Cancel", 0, 0, self.main_window)
        self.loading_dialog.setWindowTitle(f"Finding Opposing Views for '{tag_name}'")
        self.loading_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.loading_dialog.setStyleSheet("QProgressDialog { background-color: #2b2b2b; color: white; }")
        self.loading_dialog.show()

        from core.ai_opposing_views_worker import AIOpposingViewsWorker
        self.opposing_worker = AIOpposingViewsWorker(llm.api_base, llm.embedding_model, active_model, target_statement, documents, metadatas, search_mode="opposing", audit_logger=llm.audit_logger, parent=self.main_window)
        self.opposing_worker.progress.connect(self.loading_dialog.setLabelText)

        def on_finished(matches, error):
            self.loading_dialog.close()
            self.loading_dialog.deleteLater()
            try:
                if error: QMessageBox.warning(self.main_window, "Error", error)
                elif not matches: QMessageBox.information(self.main_window, "No Opposing Views", f"The AI could not find any strongly opposing arguments to the tag '{tag_name}'.")
                else: AIResultsDialog(f"⚖️ Opposing Views for '{tag_name}'", matches, self.main_window, self.main_window).exec()
            except Exception as e:
                QMessageBox.critical(self.main_window, "UI Error", f"Failed to open results: {str(e)}")

        self.opposing_worker.finished.connect(on_finished)
        self.loading_dialog.canceled.connect(self.opposing_worker.terminate)
        self.opposing_worker.start()

    def _organize_selection_ai(self): self._run_workspace_ai_tool(DefaultBlueprints.get_workspace_organize_blueprint(), require_selection=True)
    def _find_connections_ai(self): self._run_workspace_ai_tool(DefaultBlueprints.get_workspace_connections_blueprint(), require_selection=True)
    def _generate_outline_ai(self): self._run_workspace_ai_tool(DefaultBlueprints.get_workspace_outline_blueprint(), require_selection=True)
    def _weakpoints_ai(self): self._run_workspace_ai_tool(DefaultBlueprints.get_workspace_weakpoints_blueprint(), require_selection=True)
    def _fill_graph_ai(self): self._run_workspace_ai_tool(DefaultBlueprints.get_workspace_fill_blueprint(), require_selection=True)
    def _consolidate_nodes_ai(self): self._run_workspace_ai_tool(DefaultBlueprints.get_workspace_consolidate_blueprint(), require_selection=True)

    def _manage_tags_for_nodes(self, selected_nodes):
        if not selected_nodes: return
        pm = getattr(self.main_window, 'project_manager', None)
        if not pm: return
        
        if TagAssignmentDialog(pm, selected_nodes[0].node_id, "node", self).exec() != QDialog.DialogCode.Accepted: return

        template_tag_ids = {t.get("id") for t in pm.get_tags_for_node(selected_nodes[0].node_id)}
        for node in selected_nodes[1:]:
            node_tag_ids = {t.get("id") for t in pm.get_tags_for_node(node.node_id)}
            for tag_id in template_tag_ids - node_tag_ids: pm.assign_tag_to_node(node.node_id, tag_id)
            for tag_id in node_tag_ids - template_tag_ids: pm.remove_tag_from_node(node.node_id, tag_id)

        for node in selected_nodes: node.refresh_tag_badges()
        self._refresh_tag_list()
        self._apply_filter()
        pm.mark_dirty("workspace")

    def _update_loading_label(self, text): self.loading_label.setText(text + "\nThis may take a moment.")

    def start_connection(self, node):
        self.connecting_node = node
        self.connecting_node.setPen(QPen(QColor("#00ff00"), 3, Qt.PenStyle.DashLine))

    def finish_connection(self, target_node):
        text, ok = QInputDialog.getText(self, "Connection Label", "Enter text for connection:")
        if ok:
            edge = Edge(self.connecting_node, target_node, text)
            self.scene_obj.addItem(edge)
            self.edges.append(edge)
            self._mark_workspace_dirty(autosave=True)
            
        self.connecting_node.setPen(QPen(QColor("#ffffff" if self.connecting_node.isSelected() else "#555555"), 4 if self.connecting_node.isSelected() else 2))
        self.connecting_node = None

    def add_custom_bubble(self):
        self.save_state_for_undo()
        node_id = f"custom_{uuid.uuid4()}"
        node = Node(node_id, quote="", note="", color="#005577", is_custom=True, width=180, height=80)
        node.setPos(self.mapToScene(self.viewport().rect().center()))
        self.scene_obj.addItem(node)
        self.nodes[node_id] = node
        self._similarity_signature = None
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()
        self.scene_obj.clearSelection()
        node.setSelected(True)
        node.is_hovered = True
        node.refresh_layout()
        node.trigger_edit()
        self._queue_background_embedding(node)

    def sync_with_project(self, workspace_model: WorkspaceModel, pdf_annotations, force_reload=False):
        selected_ids = [n_id for n_id, n in self.nodes.items() if n.isSelected()]
        h_scroll, v_scroll = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        self.scene_obj.clear()
        self.nodes.clear()
        self.edges.clear()

        annot_dict = pdf_annotations if isinstance(pdf_annotations, dict) else {a["id"]: a for a in pdf_annotations}

        for data in workspace_model.nodes:
            quote, note, highlight_id = data.quote, data.note, data.highlight_id
            if highlight_id and highlight_id in annot_dict: quote = annot_dict[highlight_id].get("text_content", quote) or quote
            elif data.id in annot_dict: quote = annot_dict[data.id].get("text_content", quote) or quote

            node = Node(
                data.id, quote, note, data.color, data.is_custom, data.width, data.height, 
                data.pdf_path or annot_dict.get(highlight_id or data.id, {}).get("doc_id"), 
                data.page_num if data.page_num is not None else annot_dict.get(highlight_id or data.id, {}).get("page_num"), 
                data.manual_font_size, highlight_id, data.node_origin, data.is_verified, data.original_text
            )
            node.setPos(data.x, data.y)
            self.scene_obj.addItem(node)
            self.nodes[data.id] = node

        pm = getattr(self.main_window, 'project_manager', None)
        should_initialize_nodes = (self.current_workspace_id == 1 and pm and pm.get_metadata("workspace_nodes_initialized", "0") != "1")
        if should_initialize_nodes:
            y_offset = 50
            for annot in annot_dict.values():
                if annot["id"] not in self.nodes:
                    actual_note, pdf_path, page_num = "", annot.get("doc_id"), annot.get("page_num")
                    if pdf_path and page_num is not None and pm:
                        doc = pm.get_doc(pdf_path)
                        if doc:
                            for pdf_annot in doc.load_page(page_num).annots():
                                if pdf_annot.info and pdf_annot.info.get("title") == annot["id"]:
                                    actual_note = pdf_annot.info.get("content", "")
                                    break
                    self.add_node_from_annotation({"id": annot["id"], "subject": annot.get("text_content", ""), "content": actual_note, "pdf_path": pdf_path, "page_num": page_num, "color": annot.get("color")}, persist=False, position=self.mapToScene(50, y_offset))
                    y_offset += 100
            pm.set_metadata("workspace_nodes_initialized", "1")

        for edge_data in workspace_model.edges:
            if edge_data.source in self.nodes and edge_data.target in self.nodes:
                src, tgt = self.nodes[edge_data.source], self.nodes[edge_data.target]
                edge = Edge(src, tgt, edge_data.label, edge_data.id, edge_data.color, edge_data.weight)
                self.scene_obj.addItem(edge)
                self.edges.append(edge)

        for n_id in selected_ids:
            if n_id in self.nodes: self.nodes[n_id].setSelected(True)
                
        self._refresh_pdf_list()
        self._refresh_tag_list()
        self._apply_filter()
        self._similarity_signature = None
        self.update_ghost_connections()
        self.update_scene_bounds()
        self.horizontalScrollBar().setValue(h_scroll)
        self.verticalScrollBar().setValue(v_scroll)

        if should_initialize_nodes:
            self._mark_workspace_dirty(autosave=True)

    def handle_highlight_created(self, highlight_data):
        if highlight_data.get("id") in self.nodes and self.current_workspace_id == 1: return
            
        if self.current_workspace_id == 1:
            self.add_node_from_annotation(highlight_data, persist=True, target_workspace_id=1)
        else:
            pm = getattr(self.main_window, 'project_manager', None)
            if pm:
                quote = highlight_data.get("subject") or highlight_data.get("text_content") or ""
                note = highlight_data.get("content") or highlight_data.get("note_text") or ""
                color = highlight_data.get("color") or "#2b2b2b"
                w = 200 if len(note + quote) < 50 else (250 if len(note + quote) < 150 else 300)
                h = 70 if len(note + quote) < 50 else (110 if len(note + quote) < 150 else 160)
                
                model = pm.get_workspace_data(1)
                model.nodes.append(NodeModel(
                    id=highlight_data["id"], highlight_id=highlight_data["id"], workspace_id=1,
                    quote=quote, note=note, color=color, is_custom=False,
                    pdf_path=highlight_data.get("pdf_path") or highlight_data.get("doc_id"),
                    page_num=highlight_data.get("page_num"), x=0.0, y=0.0, width=w, height=h
                ))
                pm.sync_workspace(model)