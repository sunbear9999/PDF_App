# gui/components/workspace_view.py
import os
import uuid
import json
import dataclasses
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QMessageBox,
                             QInputDialog, QFrame, QLabel, QVBoxLayout,
                             QHBoxLayout, QComboBox, QPushButton, QDialog,
                             QWidget,
                             QColorDialog, QFileDialog, QCheckBox, QSlider,
                             QSizePolicy,QApplication)
from PySide6.QtCore import QPointF, Qt, QRectF, QRunnable, QThreadPool, Slot, QTimer
from PySide6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QImage, QShortcut, QKeySequence

from gui.components.workspace_items import Node, Edge
from core.layout_engine import calculate_force_directed_layout
from core.utils.text_utils import get_semantic_similarity_matrix
from core.utils.workspace_utils import (
    edge_model_from_edge,
    node_model_from_node,
)
from gui.components.dialogs.workspace_dialogs import ColorOrganizerDialog, DeclutterSettingsDialog, OutlineDialog, WeakpointsDialog, WorkspaceProcessOverlay
from gui.utils.dialog_helpers import style_dialog_with_theme
from gui.utils.workspace_view_helpers import (
    build_ai_menu,
    build_selected_nodes_context_menu,
    build_node_context_menu,
    build_edge_context_menu,
    build_canvas_context_menu,
    populate_pdf_filter_combo,
    populate_tag_filter_combo,
    workspace_toolbar_stylesheet,
)
from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
from gui.components.dialogs.tag_relatives_dialog import AIResultsDialog

# --- NEW IMPORTS ---
from core.models.workspace_models import WorkspaceModel
from gui.components.workspace_widgets import GhostLineItem, CheckableComboBox, UnusedHighlightsDialog
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload, WorkspaceIntent, WorkspacePayload
from core.services.workspace_registries import (
    build_default_workspace_action_registry,
    build_default_workspace_ai_tool_registry,
    build_default_workspace_node_type_registry,
)
from core.services.workspace_services import (
    WorkspaceAIService,
    WorkspaceAnnotationService,
    WorkspaceGraphService,
    WorkspaceLayoutService,
    WorkspaceService,
    WorkspaceStateService,
)




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
        self.bus = EventBus.get_instance()
        self.workspace_actions = build_default_workspace_action_registry()
        self.workspace_node_types = getattr(self.main_window, "workspace_node_type_registry", None) or build_default_workspace_node_type_registry()

        self.nodes = {}
        self.edges = []
        self.ghost_lines = []
        self.similarity_matrix = {}
        self._similarity_signature = None
        self._updating_ghost_links = False
        self._rendering_workspace = False
        self.connecting_node = None
        self.worker = None
        self.is_llm_busy = False
        self.is_dialog_open = False

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
        # --- Subscribe to Global Events ---

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
        self.scene_obj.selectionChanged.connect(self._emit_selection_changed)
        self.scene_obj.changed.connect(self._on_scene_changed)

        self.update_scene_bounds()
        self.bus.highlight_created.connect(self._handle_document_event)
        self.bus.highlight_updated.connect(self._handle_document_event)
        self.bus.highlight_deleted.connect(self._handle_document_event)
        self.bus.pdf_renamed.connect(self._handle_document_event)
        self.bus.pdf_removed.connect(self._handle_document_event)
        self.bus.project_loaded.connect(self._handle_project_event)
        QTimer.singleShot(0, self._sync_workspace)
        self.workspace_actions = build_default_workspace_action_registry()
        self.bus.workspace_action_requested.connect(self.handle_incoming_intent)
        self.workspace_state_service = WorkspaceStateService(self.bus, self)
        self.bus.workspace_state_restored.connect(self._handle_workspace_state_event)
        self.workspace_service = self.main_window.workspace_service
        self.workspace_graph_service = self.main_window.workspace_graph_service
        self.workspace_annotation_service = self.main_window.workspace_annotation_service
        self.workspace_ai_service = self.main_window.workspace_ai_service
        # --------------------------------
    def _handle_project_event(self, event: ProjectEvent, payload: ProjectEventPayload):
        if event == ProjectEvent.LOADED:
            self._sync_workspace()

    def _handle_document_event(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event == DocumentEvent.HIGHLIGHT_CREATED:
            self.handle_highlight_created(payload.highlight_data)
        elif event == DocumentEvent.HIGHLIGHT_UPDATED:
            self.handle_highlight_updated(payload.annot_id, payload.changes)
        elif event == DocumentEvent.HIGHLIGHT_DELETED:
            self.handle_highlight_deleted(payload.annot_id)
        elif event == DocumentEvent.PDF_RENAMED:
            self._on_pdf_renamed(payload.old_path, payload.new_path)
        elif event == DocumentEvent.PDF_REMOVED:
            self._on_pdf_removed(payload.path)

    def _handle_workspace_state_event(self, event: WorkspaceEvent, payload: WorkspaceEventPayload):
        if event == WorkspaceEvent.STATE_RESTORED:
            self._handle_state_restored(payload.model)

    def _handle_state_restored(self, restored_model):
        """Triggered entirely by the backend when an undo/redo completes."""
        pm = self._pm()
        all_annots = self.workspace_annotation_service.get_annotation_index() if pm else {}
        self.sync_with_project(restored_model, all_annots)
        self._mark_workspace_dirty(autosave=True)
    def handle_incoming_intent(self, intent_name: WorkspaceIntent, payload: WorkspacePayload):
        """Central event routing block for Phase 1 decoupling."""
        if intent_name == WorkspaceIntent.NODE_PRESSED:
            node_id = payload.get("node_id")
            # Logic hooks safely check environment if connecting modes are enabled
            if self.connecting_node and self.connecting_node.node_id != node_id:
                target_node = self.nodes.get(node_id)
                if target_node:
                    self.finish_connection(target_node)
            else:
                self.save_state_for_undo()

        elif intent_name == WorkspaceIntent.NODE_TEXT_COMMITTED:
            node = self.nodes.get(payload.get("node_id"))
            if node:
                self._commit_node_text(node, payload.get("text", ""))

        elif intent_name == WorkspaceIntent.NODE_COLOR_REQUEST:
            node_items = [self.nodes[nid] for nid in payload.get("node_ids", []) if nid in self.nodes]
            if node_items:
                self._change_color_for_nodes(node_items)

        elif intent_name == WorkspaceIntent.NODE_FONT_REQUEST:
            node = self.nodes.get(payload.get("node_id"))
            if node:
                self._change_font_size_for_node(node)

        elif intent_name == WorkspaceIntent.NODE_VERIFY_TOGGLE:
            node = self.nodes.get(payload.get("node_id"))
            if node:
                self._toggle_node_verification(node)

        elif intent_name == WorkspaceIntent.NODE_JUMP_REQUEST:
            node = self.nodes.get(payload.get("node_id"))
            if node:
                self._jump_to_node_source(node)

        elif intent_name == WorkspaceIntent.NODE_CITATION_COPY:
            node = self.nodes.get(payload.get("node_id"))
            if node:
                self._copy_node_citation(node)

        elif intent_name == WorkspaceIntent.NODE_CONNECT_START:
            node = self.nodes.get(payload.get("node_id"))
            if node:
                self.start_connection(node)

        elif intent_name == WorkspaceIntent.TAG_FILTER_APPLY:
            self.apply_tag_filter(payload.get("tag_name"))

        elif intent_name == WorkspaceIntent.UNDO_CHECKPOINT_REQUESTED:
            self.save_state_for_undo()

        elif intent_name == WorkspaceIntent.EDGE_TEXT_COMMITTED:
            # Locates edge record by searching local item collections
            edge_id = payload.get("edge_id")
            edge = next((e for e in self.edges if e.edge_id == edge_id), None)
            if edge:
                self._commit_edge_text(edge, payload.get("text", ""))

        elif intent_name == WorkspaceIntent.EDGE_COLOR_REQUEST:
            edge_id = payload.get("edge_id")
            edge = next((e for e in self.edges if e.edge_id == edge_id), None)
            if edge:
                self._change_edge_color(edge)

        elif intent_name == WorkspaceIntent.EDGE_WEIGHT_REQUEST:
            edge_id = payload.get("edge_id")
            edge = next((e for e in self.edges if e.edge_id == edge_id), None)
            if edge:
                self._change_edge_weight(edge)
        elif intent_name == WorkspaceIntent.UPDATE_HISTORY_BUTTONS:
            if hasattr(self, 'btn_undo'): self.btn_undo.setEnabled(payload.get("can_undo", False))
            if hasattr(self, 'btn_redo'): self.btn_redo.setEnabled(payload.get("can_redo", False))
        elif intent_name == WorkspaceIntent.SYNC_TAGS_FROM_ANNOT:
            annot_id = payload.get("annot_id")
            for node in self.nodes.values():
                if annot_id in {getattr(node, "highlight_id", None), getattr(node, "node_id", None)}:
                    if hasattr(node, "refresh_tag_badges"):
                        node.refresh_tag_badges()
                    node.update()
        elif intent_name == WorkspaceIntent.BOARD_ADD:
            self._add_workspace()
        elif intent_name == WorkspaceIntent.WORKSPACE_EXPORT:
            self._export_workspace()
        elif intent_name == WorkspaceIntent.FILTERS_RESET:
            self.reset_filters()
        elif intent_name == WorkspaceIntent.VIEW_RECENTER:
            self.recenter_view()
        elif intent_name == WorkspaceIntent.DECLUTTER_TRIGGERED:
            self.trigger_declutter()
        elif intent_name == WorkspaceIntent.NODE_EDIT_START:
            node = self.nodes.get(payload.get("node_id"))
            if node:
                node.trigger_edit()
        elif intent_name == WorkspaceIntent.NODE_TAGS_MANAGE:
            self._manage_tags_for_node_ids(payload.get("node_ids", []))
        elif intent_name == WorkspaceIntent.EDGE_EDIT_START:
            edge_id = payload.get("edge_id")
            edge = next((e for e in self.edges if e.edge_id == edge_id), None)
            if edge:
                edge.trigger_edit()
        elif intent_name == WorkspaceIntent.EDGE_DELETE_REQUEST:
            edge_id = payload.get("edge_id")
            edge = next((e for e in self.edges if e.edge_id == edge_id), None)
            if edge:
                self.delete_edge(edge)
                self._mark_workspace_dirty(autosave=True)
        elif intent_name == WorkspaceIntent.SELECTION_DELETE:
            self.delete_selected_nodes()
        elif intent_name == WorkspaceIntent.SELECTION_COLOR_REQUEST:
            node_items = self._selected_nodes()
            if node_items:
                self._change_color_for_nodes(node_items)
        elif intent_name == WorkspaceIntent.SELECTION_TAGS_MANAGE:
            self._manage_tags_for_node_ids([node.node_id for node in self._selected_nodes()])

    def _manage_tags_for_node_ids(self, node_ids):
        node_ids = [node_id for node_id in node_ids or [] if node_id in self.nodes]
        if not node_ids:
            return
        dialog = TagAssignmentDialog(node_ids[0], "node", self)
        if dialog.exec():
            for node_id in node_ids:
                node = self.nodes.get(node_id)
                if node and hasattr(node, "refresh_tag_badges"):
                    node.refresh_tag_badges()
    def _on_pdf_renamed(self, old_path, new_path):
        for node in self.nodes.values():
            if getattr(node, 'pdf_path', None) == old_path:
                node.pdf_path = new_path
        self._refresh_pdf_list()

    def _on_pdf_removed(self, doc_path):
        nodes_to_delete = [n for n in self.nodes.values() if getattr(n, 'pdf_path', None) == doc_path]
        for node in nodes_to_delete:
            self.delete_node(node)
        self._refresh_pdf_list()
    def _pm(self):
        return getattr(self.main_window, 'project_manager', None)

    def _llm(self):
        return getattr(self.main_window, 'shared_llm_manager', None)

    def _theme(self):
        return getattr(self.main_window, 'theme_manager', None)

    def _selected_nodes(self):
        return [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]

    def _emit_selection_changed(self):
        selected_ids = [n.node_id for n in self._selected_nodes()]
        self.bus.workspace_selection_changed.emit(
            WorkspaceEvent.SELECTION_CHANGED,
            WorkspaceEventPayload(selected_ids=selected_ids),
        )

    def _emit_node_updated(self, node_id, changes):
        self.bus.workspace_node_updated.emit(
            WorkspaceEvent.NODE_UPDATED,
            WorkspaceEventPayload(node_id=node_id, changes=changes),
        )

    def _emit_edge_updated(self, edge_id, changes):
        self.bus.workspace_edge_updated.emit(
            WorkspaceEvent.EDGE_UPDATED,
            WorkspaceEventPayload(edge_id=edge_id, changes=changes),
        )

    def _emit_node_deleted(self, node_id):
        self.bus.workspace_node_deleted.emit(
            WorkspaceEvent.NODE_DELETED,
            WorkspaceEventPayload(node_id=node_id),
        )

    def _emit_edge_deleted(self, edge_id):
        self.bus.workspace_edge_deleted.emit(
            WorkspaceEvent.EDGE_DELETED,
            WorkspaceEventPayload(edge_id=edge_id),
        )

    def _emit_node_added(self, node_model):
        self.bus.workspace_node_added.emit(
            WorkspaceEvent.NODE_ADDED,
            WorkspaceEventPayload(node_model=node_model),
        )

    def _emit_edge_added(self, edge_model):
        self.bus.workspace_edge_added.emit(
            WorkspaceEvent.EDGE_ADDED,
            WorkspaceEventPayload(edge_model=edge_model),
        )

    def get_node_tag_badges(self, node_id):
        pm = self._pm()
        if not pm:
            return []
        try:
            return [
                {"name": t.get("name") or "", "color": t.get("color") or "#808080"}
                for t in pm.get_tags_for_node(node_id)
            ]
        except Exception:
            return []

    def handle_item_intent(self, intent, item, **payload):
        if self._rendering_workspace and intent in {
            "save_undo",
            "node_resized",
            "node_moved",
            "node_text_committed",
            "edge_text_committed",
        }:
            return
        if intent == "save_undo":
            self.save_state_for_undo()
        elif intent == "tag_filter":
            self.apply_tag_filter(payload.get("tag_name"))
        elif intent == "finish_connection":
            self.save_state_for_undo()
            self.finish_connection(item)
        elif intent == "node_connect":
            self.start_connection(item)
        elif intent == "node_edit_started":
            self.save_state_for_undo()
        elif intent == "node_text_committed":
            self._commit_node_text(item, payload.get("text", ""))
        elif intent == "node_color_requested":
            self._change_color_for_nodes([item])
        elif intent == "node_font_size_requested":
            self._change_font_size_for_node(item)
        elif intent == "node_verify_requested":
            self._toggle_node_verification(item)
        elif intent == "node_jump_requested":
            self._jump_to_node_source(item)
        elif intent == "node_resized":
            self._mark_workspace_dirty(autosave=True)
            self._emit_node_updated(item.node_id, {"width": item.base_width, "height": item.base_height})
        elif intent == "node_moved":
            self._mark_workspace_dirty(autosave=True)
            self._emit_node_updated(item.node_id, {"x": item.pos().x(), "y": item.pos().y()})
        elif intent == "edge_text_committed":
            self._commit_edge_text(item, payload.get("text", ""))
        elif intent == "edge_color_requested":
            self._change_edge_color(item)
        elif intent == "edge_weight_requested":
            self._change_edge_weight(item)

    def _commit_node_text(self, node, new_text):
        if not node or new_text == node.note:
            return
        current_orig = getattr(node, "original_text", node.note)
        if current_orig == node.note:
            node.original_text = node.note
        node.note = new_text
        node.refresh_layout()
        self._mark_workspace_dirty(autosave=True)
        self._emit_node_updated(node.node_id, {"note": new_text})

    def _commit_edge_text(self, edge, new_text):
        if not edge or new_text == edge.label_text:
            return
        self.save_state_for_undo()
        edge.label_text = new_text
        edge.text_item.setPlainText(new_text)
        edge.update_position()
        self._mark_workspace_dirty(autosave=True)
        self._emit_edge_updated(edge.edge_id, {"label": new_text})

    def _toggle_node_verification(self, node):
        if not node:
            return
        self.save_state_for_undo()
        node.is_verified = not node.is_verified
        if hasattr(node, "refresh_verify_button"):
            node.refresh_verify_button()
        pm = self._pm()
        if pm:
            pm.set_node_verification(node.node_id, node.is_verified)
        self._mark_workspace_dirty(autosave=True)
        self._emit_node_updated(node.node_id, {"is_verified": int(node.is_verified)})
        node.update()

    def _jump_to_node_source(self, node):
        if not node:
            return
        self.save_workspace_state()
        QTimer.singleShot(0, lambda: self.workspace_annotation_service.jump_to_node_source(node))

    def _copy_node_citation(self, node):
        if not node:
            return
        if node.pdf_path is not None:
            citation_text = self.main_window.citation_manager.format_in_text(node.pdf_path, node.page_num)
            QApplication.clipboard().setText(citation_text)
            self.main_window.statusBar().showMessage(f"Copied citation: {citation_text}", 3000)
        else:
            QMessageBox.warning(self, "No Citation", "This is a custom node, not a PDF highlight.")

    def _change_edge_color(self, edge):
        if not edge:
            return
        color = QColorDialog.getColor(edge.base_color, self, "Select Line Color")
        if color.isValid():
            self.save_state_for_undo()
            edge.base_color = color
            edge.setPen(QPen(edge.base_color, edge.weight + 2 if edge.isSelected() else edge.weight, Qt.PenStyle.SolidLine))
            self._mark_workspace_dirty(autosave=True)
            self._emit_edge_updated(edge.edge_id, {"color": color.name()})

    def _change_edge_weight(self, edge):
        if not edge:
            return
        weight, ok = QInputDialog.getInt(self, "Line Weight", "Enter line weight (1-10):", edge.weight, 1, 10)
        if ok:
            self.save_state_for_undo()
            edge.weight = weight
            edge.setPen(QPen(edge.base_color, edge.weight + 2 if edge.isSelected() else edge.weight, Qt.PenStyle.SolidLine))
            self._mark_workspace_dirty(autosave=True)
            self._emit_edge_updated(edge.edge_id, {"weight": weight})

    def _change_font_size_for_node(self, node):
        if not node:
            return
        current = node.manual_font_size if node.manual_font_size else 12
        val, ok = QInputDialog.getInt(self, "Font Size", "Enter static font size (8-72)\nCancel to Auto-Scale:", current, 8, 72)
        self.save_state_for_undo()
        node.manual_font_size = val if ok else None
        node.refresh_layout()
        self._mark_workspace_dirty(autosave=True)
        self._emit_node_updated(node.node_id, {"manual_font_size": node.manual_font_size})

    def get_active_ai_model(self):
        return self.workspace_ai_service.resolve_active_model()

    def recenter_view(self):
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            rect.adjust(-50, -50, 50, 50)
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _queue_background_embedding(self, node):
        text = f"{node.quote} {node.note}".strip()
        if not text:
            return

        self.bus.workspace_action_requested.emit(
            WorkspaceIntent.EMBED_NODE_TEXT,
            WorkspacePayload(node_id=node.node_id, text=text),
        )

    # =========================================================================
    # UNIVERSAL AI WORKSPACE API & MODELS
    # =========================================================================

    def serialize_workspace(self) -> WorkspaceModel:
        """Serializes the canvas directly into the strict Data Model."""
        model = WorkspaceModel(workspace_id=self.current_workspace_id)
        for node in self.nodes.values():
            model.nodes.append(node_model_from_node(node, self.current_workspace_id))
        for edge in self.edges:
            model.edges.append(edge_model_from_edge(edge))
        return model

    def save_workspace_state(self):
        """Standalone save method that pushes the Model to the DB."""
        model = self.serialize_workspace()
        self.workspace_service.sync_workspace(model)

    def _mark_workspace_dirty(self, autosave=False):
        if self._rendering_workspace:
            return
        self.workspace_service.mark_dirty(self.current_workspace_id, autosave=autosave, model=self.serialize_workspace() if autosave else None)

    def get_workspace_state_as_json(self, only_selected=False, filters=None):
        """Passes the model into the new API orchestrator."""
        model = self.serialize_workspace()

        if only_selected:
            selected_ids = {n.node_id for n in self.scene_obj.selectedItems() if isinstance(n, Node)}
            model = self.workspace_graph_service.selected_subset(model, selected_ids)

        return self.workspace_ai_service.build_context(model, filters)





    # =========================================================================
    # SCENE & COMPONENT MANAGEMENT
    # =========================================================================

    def add_node_from_annotation(self, annot, persist=False, position=None, target_workspace_id=None):
        n_id = annot["id"]
        effective_ws_id = target_workspace_id if target_workspace_id is not None else self.current_workspace_id

        if persist and effective_ws_id != self.current_workspace_id:
            self.workspace_annotation_service.add_annotation_to_workspace(annot, effective_ws_id)
            return None

        if n_id in self.nodes:
            return self.nodes[n_id]

        node_model = self.workspace_annotation_service.node_model_from_annotation(annot, effective_ws_id)

        node = Node(
            node_model.id, node_model.quote, node_model.note, color=node_model.color, is_custom=node_model.is_custom,
            width=node_model.width, height=node_model.height,
            pdf_path=node_model.pdf_path, page_num=node_model.page_num,
            highlight_id=node_model.highlight_id, node_origin=node_model.node_origin,
            is_verified=node_model.is_verified, original_text=node_model.original_text,
            node_type_id=node_model.node_type_id,
            node_type_registry=self.workspace_node_types,
            action_registry=self.workspace_actions,
        )

        if position is None:
            position = self.mapToScene(self.viewport().rect().center())
        node.setPos(position)
        self.scene_obj.addItem(node)
        self.nodes[n_id] = node
        self._similarity_signature = None
        self._emit_node_added(node_model_from_node(node, effective_ws_id))

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
        self._emit_node_added(node_model_from_node(node, self.current_workspace_id))
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()
        self._queue_background_embedding(node)

    def add_edge(self, source_node: Node, target_node: Node, label=""):
        edge = Edge(source_node, target_node, label)
        self.scene_obj.addItem(edge)
        self.edges.append(edge)
        self._emit_edge_added(edge_model_from_edge(edge))
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
        return build_ai_menu(self, parent_widget)

    def update_theme(self, theme):
        self.setBackgroundBrush(QBrush(QColor(theme['canvas'])))
        self.loading_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 180); border-radius: 8px;")
        self.loading_label.setStyleSheet(f"color: {theme['success']}; font-size: 26px; font-weight: bold; background: transparent;")

        if hasattr(self, 'toolbar_frame'):
            self.toolbar_frame.setStyleSheet(workspace_toolbar_stylesheet(theme))

    def _refresh_pdf_list(self):
        checked_data = self.filter_combo.get_checked_items()
        if not checked_data and self.filter_combo.count() == 0:
            checked_data = ["ALL"]

        self.filter_combo.blockSignals(True)
        populate_pdf_filter_combo(self.filter_combo, self._pm().pdfs if self._pm() else [], checked_data)
        self.filter_combo.blockSignals(False)

    def _refresh_tag_list(self, forced_checked=None):
        checked_data = list(forced_checked) if forced_checked is not None else self.tag_filter_combo.get_checked_items()
        if not checked_data and self.tag_filter_combo.count() == 0:
            checked_data = ["ALL_TAGS"]

        self.tag_filter_combo.blockSignals(True)
        populate_tag_filter_combo(self.tag_filter_combo, self._pm().get_all_tags() if self._pm() else [], checked_data)
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
        pm = self._pm()
        if pm:
            populate_pdf_filter_combo(self.filter_combo, pm.pdfs, ["ALL"])
        self.filter_combo.blockSignals(False)
        self._apply_filter()

    def get_allowed_docs(self):
        checked = self.filter_combo.get_checked_items()
        pm = self._pm()
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

        self.bus.workspace_filter_changed.emit(
            WorkspaceEvent.FILTER_CHANGED,
            WorkspaceEventPayload(filters={"pdfs": checked_pdfs, "tags": checked_tags}),
        )
        self.update_ghost_connections()

    def _build_similarity_matrix_if_needed(self):
        node_items = sorted(self.nodes.values(), key=lambda n: n.node_id)
        signature = tuple((n.node_id, (n.quote or ""), (n.note or "")) for n in node_items)
        if signature == self._similarity_signature: return

        self._similarity_signature = signature
        self.similarity_matrix = {}

        if len(node_items) < 2: return

        llm_manager = self._llm()
        if not llm_manager or not llm_manager.ai_enabled: return

        node_ids = [n.node_id for n in node_items]
        texts_to_embed = [f"{n.quote} {n.note}".strip() for n in node_items]
        pm = self._pm()

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
        pm = self._pm()
        if not pm or not pm.project_filepath: return
        try:
            # Call the global service from MainWindow
            workspace_model = self.main_window.workspace_service.load_workspace(self.current_workspace_id)
            all_annots = self.workspace_annotation_service.get_annotation_index()
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
        self._emit_edge_added(edge_model_from_edge(edge))
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()

    def open_unused_highlights_dialog(self):
        pm = self._pm()
        if not pm: return
        all_highlights = self.workspace_annotation_service.get_annotation_index()
        workspace_node_ids = {
            n.highlight_id or n.node_id
            for n in self.workspace_service.load_workspace(self.current_workspace_id).nodes
        }
        unused_highlights = [
            highlight for highlight_id, highlight in all_highlights.items()
            if highlight_id not in workspace_node_ids
        ]

        if not unused_highlights:
            QMessageBox.information(self, "Unused Highlights", "No unused highlights found.")
            return

        dialog = UnusedHighlightsDialog(unused_highlights, self)
        if hasattr(self.main_window, 'theme_manager'):
            theme = self._theme().get_theme() if self._theme() else None
            if theme:
                style_dialog_with_theme(dialog, theme)

        if dialog.exec() != QDialog.DialogCode.Accepted: return
        highlight_ids = dialog.get_selected_highlight_ids()
        if not highlight_ids: return

        self.save_state_for_undo()
        view_center = self.mapToScene(self.viewport().rect().center())
        last_node, offset = None, 0

        for highlight_id in highlight_ids:
            highlight = all_highlights.get(highlight_id) or pm.get_highlight(highlight_id)
            if not highlight: continue

            actual_note, pdf_path, page_num = highlight.get("content", ""), highlight.get("doc_id"), highlight.get("page_num")
            if pdf_path and page_num is not None:
                actual_note = self.workspace_annotation_service.get_pdf_annotation_note(highlight["id"], pdf_path, page_num) or actual_note

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
        pm = self._pm()
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

        pm = self._pm()
        if not pm or not pm.project_filepath: return

        self.sync_with_project(self.workspace_service.load_workspace(self.current_workspace_id), self.workspace_annotation_service.get_annotation_index())

    def _add_workspace(self):
        pm = self._pm()
        if not pm or not pm.project_filepath:
            QMessageBox.information(self, "No Project", "Please open a project first.")
            return

        name, ok = QInputDialog.getText(self, "New Workspace", "Enter workspace name:")
        if not ok or not name.strip(): return

        new_id = self.workspace_service.create_workspace(name.strip())
        if new_id is None:
            QMessageBox.warning(self, "Error", "Could not create workspace.")
            return

        self.workspace_combo.blockSignals(True)
        self.workspace_combo.addItem(name.strip(), new_id)
        self.workspace_combo.blockSignals(False)
        self.workspace_combo.setCurrentIndex(self.workspace_combo.count() - 1)

    def _open_color_by_pdf_dialog(self):
        pm = self._pm()
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

        if self._theme():
            theme = self._theme().get_theme()
            style_dialog_with_theme(dialog, theme)
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
                temp_manager = self._llm()
                if temp_manager and temp_manager.ai_enabled: llm_manager = temp_manager
                else: QMessageBox.information(self, "AI Disabled", "Ollama is not running. Falling back to standard math declutter.")
            except Exception: pass

        similarity_matrix = {}
        if llm_manager:
            node_ids = [n.node_id for n in target_nodes]
            texts_to_embed = [f"{n.quote} {n.note}".strip() for n in target_nodes]
            similarity_matrix = get_semantic_similarity_matrix(node_ids, texts_to_embed, llm_manager, self._pm())

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
            theme = self._theme().get_theme() if self._theme() else {'canvas': '#1a1a1a'}
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
        # We now package the model and throw it to the backend service
        model = self.serialize_workspace()
        self.bus.workspace_action_requested.emit(WorkspaceIntent.SAVE_UNDO_STATE, WorkspacePayload(model=model))

    def undo(self):
        # We must package current state for the redo stack before asking for the undo state
        self.bus.workspace_action_requested.emit(WorkspaceIntent.SAVE_REDO_STATE, WorkspacePayload(model=self.serialize_workspace()))
        self.bus.workspace_action_requested.emit(WorkspaceIntent.UNDO_TRIGGERED, WorkspacePayload())

    def redo(self):
        self.bus.workspace_action_requested.emit(WorkspaceIntent.SAVE_UNDO_STATE, WorkspacePayload(model=self.serialize_workspace()))
        self.bus.workspace_action_requested.emit(WorkspaceIntent.REDO_TRIGGERED, WorkspacePayload())

    def _update_buttons(self):
        if hasattr(self, 'btn_undo'): self.btn_undo.setEnabled(len(self.undo_stack) > 0)
        if hasattr(self, 'btn_redo'): self.btn_redo.setEnabled(len(self.redo_stack) > 0)



    # ------------------------------------------------------------------ clipboard

    def copy_selection(self):
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        if not selected_nodes: return
        self.clipboard = self.workspace_graph_service.copy_selection_payload(selected_nodes, self.edges)

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
                page_num=data['page_num'], manual_font_size=data['manual_font_size'], highlight_id=data['highlight_id'],
                node_type_id=data.get("node_type_id"),
                node_type_registry=self.workspace_node_types,
                action_registry=self.workspace_actions,
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
        self._emit_edge_deleted(getattr(edge, "edge_id", ""))

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
            self._emit_node_deleted(node.node_id)

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
        self._emit_node_deleted(node.node_id)

        self._similarity_signature = None
        self.update_ghost_connections()
        self._mark_workspace_dirty(autosave=True)

    def _delete_highlight_permanently(self, highlight_id):
        nodes_to_remove = [node for node in list(self.nodes.values()) if node.highlight_id == highlight_id or node.node_id == highlight_id]
        fallback_node = nodes_to_remove[0] if nodes_to_remove else None

        for node in nodes_to_remove:
            for edge in list(node.edges): self.delete_edge(edge)
            self.scene_obj.removeItem(node)
            node.edges = []
            self.nodes.pop(node.node_id, None)
            if hasattr(node, 'deleteLater'): node.deleteLater()
            self._emit_node_deleted(node.node_id)

        self.workspace_annotation_service.delete_highlight_permanently(highlight_id, fallback_node)

        self._similarity_signature = None
        self.update_ghost_connections()
        self._mark_workspace_dirty(autosave=True)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        while item and not isinstance(item, (Node, Edge)): item = item.parentItem()
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]

        if len(selected_nodes) > 1 and (item is None or (isinstance(item, Node) and item in selected_nodes)):
            menu, delete_action, color_action, manage_tags_action, declutter_action = build_selected_nodes_context_menu(self, self, selected_nodes)
            action = menu.exec(event.globalPos())
            if action == delete_action:
                self.save_state_for_undo()
                for highlight_id in {n.highlight_id for n in selected_nodes if n.highlight_id}:
                    self._delete_highlight_permanently(highlight_id)
            elif action == color_action:
                self._change_color_for_nodes(selected_nodes)
            elif action == manage_tags_action:
                self._manage_tags_for_nodes(selected_nodes)
            elif action == declutter_action:
                self.trigger_declutter()
            return

        if isinstance(item, Node):
            prior_selected = [n for n in selected_nodes]
            if item not in selected_nodes:
                self.scene_obj.clearSelection()
                item.setSelected(True)
                selected_nodes = [item]

            connect_source = prior_selected[0] if (len(prior_selected) == 1 and prior_selected[0] is not item) else None
            menu, edit_action, color_action, manage_tags_action, cite_action, connect_action, delete_highlight_action, declutter_action = build_node_context_menu(self, self, item, selected_nodes, connect_source)

            action = menu.exec(event.globalPos())
            if action == edit_action:
                item.trigger_edit()
            elif action == color_action:
                item.trigger_color_change()
            elif action == manage_tags_action:
                if TagAssignmentDialog(self._pm(), item.node_id, "node", self).exec():
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
            elif action == declutter_action:
                self.trigger_declutter()
            elif action == cite_action:
                self._copy_node_citation(item)
            return

        if isinstance(item, Edge):
            menu, edit_action, color_action, weight_action, del_action = build_edge_context_menu(self, self, item)
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

        if item is None:
            menu, declutter_action = build_canvas_context_menu(self, self)
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
                self.workspace_annotation_service.mirror_note_edit_to_notes(node)
                self._emit_node_updated(node.node_id, {"color": color_name})
            self._mark_workspace_dirty(autosave=True)

    def trigger_find_tag_relatives(self, tag_name):
        pm, llm = self._pm(), self._llm()
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
        pm, llm = self._pm(), self._llm()
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

        active_model = self.get_active_ai_model()

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



    def _manage_tags_for_nodes(self, selected_nodes):
        if not selected_nodes: return
        pm = self._pm()
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
            self._emit_edge_added(edge_model_from_edge(edge))
            self._mark_workspace_dirty(autosave=True)

        self.connecting_node.setPen(QPen(QColor("#ffffff" if self.connecting_node.isSelected() else "#555555"), 4 if self.connecting_node.isSelected() else 2))
        self.connecting_node = None

    def add_custom_bubble(self):
        self.save_state_for_undo()
        node_id = f"custom_{uuid.uuid4()}"
        node = Node(
            node_id, quote="", note="", color="#005577", is_custom=True, width=180, height=80,
            node_type_id="workspace.node.text",
            node_type_registry=self.workspace_node_types,
            action_registry=self.workspace_actions,
        )
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
        self._rendering_workspace = True
        should_initialize_nodes = False
        try:
            self.scene_obj.clear()
            self.nodes.clear()
            self.edges.clear()

            annot_dict = pdf_annotations if isinstance(pdf_annotations, dict) else {a["id"]: a for a in pdf_annotations}

            for data in workspace_model.nodes:
                quote, note, highlight_id = data.quote, data.note, data.highlight_id
                annot_record = annot_dict.get(highlight_id or data.id, {})
                if annot_record:
                    live_quote = annot_record.get("text_content") or annot_record.get("subject")
                    if live_quote:
                        quote = live_quote
                    if "content" in annot_record:
                        note = annot_record.get("content") or ""
                color = (annot_record.get("color") or data.color) if annot_record else data.color
                pdf_path = data.pdf_path or annot_record.get("pdf_path") or annot_record.get("doc_id")
                page_num = data.page_num if data.page_num is not None else annot_record.get("page_num")

                node = Node(
                    data.id, quote, note, color, data.is_custom, data.width, data.height,
                    pdf_path,
                    page_num,
                    data.manual_font_size, highlight_id, data.node_origin, data.is_verified, data.original_text,
                    data.node_type_id,
                    self.workspace_node_types,
                    self.workspace_actions,
                )
                self.scene_obj.addItem(node)
                self.nodes[data.id] = node
                node.setPos(data.x, data.y)

            pm = self._pm()
            should_initialize_nodes = (self.current_workspace_id == 1 and pm and pm.get_metadata("workspace_nodes_initialized", "0") != "1")
            if should_initialize_nodes:
                y_offset = 50
                for annot in annot_dict.values():
                    if annot["id"] not in self.nodes:
                        actual_note, pdf_path, page_num = annot.get("content", ""), annot.get("doc_id"), annot.get("page_num")
                        if pdf_path and page_num is not None and pm:
                            doc = pm.get_doc(pdf_path)
                            if doc:
                                for pdf_annot in doc.load_page(page_num).annots():
                                    if pdf_annot.info and pdf_annot.info.get("title") == annot["id"]:
                                        actual_note = pdf_annot.info.get("content", "") or actual_note
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
        finally:
            self._rendering_workspace = False

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
            self.workspace_annotation_service.add_annotation_to_workspace(highlight_data, 1)

    def handle_highlight_updated(self, highlight_id, changes):
        node = self.nodes.get(highlight_id)
        if not node:
            node = next((n for n in self.nodes.values() if n.highlight_id == highlight_id), None)
        if not node:
            return

        source = self.workspace_annotation_service.find_source_for_node(node)
        if source:
            live_quote = source.get("text_content") or source.get("subject")
            if live_quote:
                node.quote = live_quote
            node.pdf_path = source.get("pdf_path") or source.get("doc_id") or node.pdf_path
            if source.get("page_num") is not None:
                node.page_num = source.get("page_num")
            if source.get("id") and not node.highlight_id:
                node.highlight_id = source.get("id")

        if "note" in changes or "content" in changes:
            node.note = changes.get("note", changes.get("content", node.note))
        if changes.get("color"):
            node.color = changes["color"]
            node.setBrush(QBrush(QColor(node.color)))
        if changes.get("pdf_path"):
            node.pdf_path = changes["pdf_path"]
        if changes.get("page_num") is not None:
            node.page_num = changes["page_num"]

        node.refresh_layout()
        self._mark_workspace_dirty(autosave=True)

    def handle_highlight_deleted(self, highlight_id):
        node = self.nodes.get(highlight_id)
        if not node:
            node = next((n for n in self.nodes.values() if n.highlight_id == highlight_id), None)
        if node:
            self.delete_node(node, delete_highlight=False)
