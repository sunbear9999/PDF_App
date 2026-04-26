# gui/components/workspace_view.py
import os
import uuid
import json
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QMenu, QMessageBox,
                             QInputDialog, QFrame, QLabel, QVBoxLayout,
                             QHBoxLayout, QComboBox, QPushButton, QDialog,
                             QScrollArea, QWidget, QFormLayout, QDialogButtonBox,
                             QColorDialog, QFileDialog, QTextEdit, QCheckBox, QSlider,
                             QGraphicsLineItem, QGraphicsTextItem, QListWidget,
                             QListWidgetItem,QSizePolicy)
from PySide6.QtCore import Qt, QRectF, QRunnable, QThreadPool, Slot
from PySide6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QImage, QStandardItemModel, QStandardItem, QCursor, QPainterPath, QPainterPathStroker, QShortcut, QKeySequence
from gui.components.workspace_items import Node, Edge
from core.ai_organize_worker import AIOrganizeWorker
from core.ai_connections_worker import AIFindConnectionsWorker
from core.ai_outline_worker import AIOutlineWorker
from core.ai_weakpoints_worker import AIWeakpointsWorker
from core.ai_fill_graph_worker import AIFillGraphWorker
from core.ai_consolidate_worker import AIConsolidateWorker
from core.layout_engine import calculate_force_directed_layout
from core.text_utils import get_semantic_similarity_matrix
from gui.components.dialogs.workspace_dialogs import ColorOrganizerDialog, DeclutterSettingsDialog, OutlineDialog, WeakpointsDialog, ContextFilterDialog
from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
from gui.components.dialogs.tag_relatives_dialog import AIResultsDialog


class GhostLineItem(QGraphicsLineItem):
    """A dashed similarity line that can be right-clicked to convert into a real Edge."""

    def __init__(self, x1, y1, x2, y2, source_id, target_id, sim_score, workspace_view):
        super().__init__(x1, y1, x2, y2)
        self.source_id = source_id
        self.target_id = target_id
        self.sim_score = sim_score
        self.workspace_view = workspace_view
        self.setAcceptedMouseButtons(Qt.MouseButton.RightButton)

    def shape(self):
        # Widen hit area to 12px so right-clicks land easily on thin lines
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
            self.workspace_view._convert_ghost_to_edge(
                self.source_id, self.target_id, self.sim_score
            )
        event.accept()


class CollapsingButton(QPushButton):
    """Icon-only button that expands to its full label while the mouse is over it."""
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
        # sizeHint reflects new text immediately; add horizontal padding to match style
        self.setFixedWidth(self.sizeHint().width() + 8)
        self._sync_toolbar()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setText(self._icon_text)
        self.setFixedWidth(self._COLLAPSED_WIDTH)
        self._sync_toolbar()
        super().leaveEvent(event)


class CollapsingSection(QFrame):
    """Shows only an icon label; reveals a content widget while the mouse is inside."""

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
        # leaveEvent fires when mouse enters a child widget too; only collapse when
        # the cursor has genuinely left this section's bounding rect.
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

        all_item = self.model().item(0) # 'All PDFs' is always at index 0

        # Block signals so we can update multiple checkboxes silently without firing the filter repeatedly.
        self.model().blockSignals(True)

        if clicked_data == "ALL":
            # If clicking ALL, force it to checked and uncheck everything else.
            item.setCheckState(Qt.CheckState.Checked)
            for i in range(1, self.model().rowCount()):
                self.model().item(i).setCheckState(Qt.CheckState.Unchecked)
        else:
            if current_state == Qt.CheckState.Checked:
                # If transitioning from "ALL" to a specific filter, clear everything else for a fresh start.
                if all_item and all_item.checkState() == Qt.CheckState.Checked:
                    all_item.setCheckState(Qt.CheckState.Unchecked)
                    for i in range(1, self.model().rowCount()):
                        other_item = self.model().item(i)
                        if other_item != item:
                            other_item.setCheckState(Qt.CheckState.Unchecked)
            else:
                # If no specific items are checked, auto-fallback to ALL.
                any_checked = False
                for i in range(1, self.model().rowCount()):
                    if self.model().item(i).checkState() == Qt.CheckState.Checked:
                        any_checked = True
                        break
                if not any_checked and all_item:
                    all_item.setCheckState(Qt.CheckState.Checked)

        self._changed = True
        self.model().blockSignals(False)

        # Emit dataChanged once for the whole list to trigger filter/UI updates.
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


class UnusedHighlightsDialog(QDialog):
    def __init__(self, highlights, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unused Highlights")
        self.resize(520, 380)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Highlights in the database that are not in this workspace (Ctrl+click or Shift+click to select multiple):"))

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
    """
    Background worker that fetches embeddings from Ollama without freezing the UI.
    Inheriting from QRunnable ensures it safely hands off to C++ QThreadPool.
    """
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
                # Use the new threadsafe method!
                self.project_manager.save_node_embedding_threadsafe(self.node_id, vector)
        except Exception as e:
            pass




class WorkspaceView(QGraphicsView):
    def add_node_from_annotation(self, annot, persist=False, position=None, target_workspace_id=None):
        n_id = annot["id"]
        effective_ws_id = target_workspace_id if target_workspace_id is not None else self.current_workspace_id
        origin = "ai" if n_id.startswith("AINote") else "human"
        # If the highlight targets a different workspace, persist directly to DB without touching the canvas
        if persist and effective_ws_id != self.current_workspace_id:
            pm = self.main_window.project_manager if self.main_window and hasattr(self.main_window, "project_manager") else None
            if pm and pm.project_filepath:
                quote = annot.get("subject") or annot.get("text_content") or ""
                note = annot.get("content") or annot.get("note_text") or ""
                color = annot.get("color") or "#2b2b2b"
                w = 200 if len(note + quote) < 50 else (250 if len(note + quote) < 150 else 300)
                h = 70 if len(note + quote) < 50 else (110 if len(note + quote) < 150 else 160)
                pm.upsert_node_record({
                    "id": n_id, "highlight_id": n_id,
                    "quote": quote, "note": note, "color": color,
                    "is_custom": False,
                    "pdf_path": annot.get("pdf_path") or annot.get("doc_id"),
                    "page_num": annot.get("page_num"),
                    "manual_font_size": None,
                    "x": 0.0, "y": 0.0, "width": w, "height": h,
                    "origin": origin,
                }, effective_ws_id)
            return None

        if n_id in self.nodes:
            return self.nodes[n_id]

        quote = annot.get("subject") or annot.get("text_content") or ""
        note = annot.get("content") or annot.get("note_text") or ""
        color = annot.get("color") or ("#2d2238" if n_id.startswith("AINote") else "#2b2b2b")
        w = 200 if len(note + quote) < 50 else (250 if len(note + quote) < 150 else 300)
        h = 70 if len(note + quote) < 50 else (110 if len(note + quote) < 150 else 160)
        node = Node(
            n_id,
            quote,
            note,
            color=color,
            is_custom=False,
            width=w,
            height=h,
            pdf_path=annot.get("pdf_path") or annot.get("doc_id"),
            page_num=annot.get("page_num"),
            highlight_id=n_id,
            node_origin=origin,
            is_verified=0,
            original_text=note
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

        # Keep as instance vars so Qt/Python don't GC them.
        # Use explicit key sequences (not StandardKey enums) to avoid multi-binding ambiguity.
        # Connect activatedAmbiguously as well so the slot fires even when another widget
        # in the same window has a conflicting shortcut registered.
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
        # 🔥 FIX 1: Strict Vertical Policy prevents the horizontal stretching glitch!
        self.toolbar_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        main_tb_layout = QVBoxLayout(self.toolbar_frame)
        main_tb_layout.setContentsMargins(4, 4, 4, 4)
        main_tb_layout.setSpacing(2) # Ultra-tight vertical spacing

        # 🔥 FIX 2: CSS to force emojis to the direct center and compress padding
        compact_btn_style = """
            QPushButton {
                padding: 4px 6px;
                border-radius: 4px;
                font-weight: bold;
                text-align: center;
            }
        """

        # ==========================================
        # ROW 1: Primary Tools & Navigation
        # ==========================================
        self.row1_widget = QWidget()
        row1_layout = QHBoxLayout(self.row1_widget)
        row1_layout.setContentsMargins(0, 0, 0, 0)
        row1_layout.setSpacing(4) # Ultra-tight horizontal spacing

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

        # Visual Divider
        line1 = QFrame()
        line1.setFrameShape(QFrame.Shape.VLine)
        row1_layout.addWidget(line1)

        self.btn_ai_tools = QPushButton("🤖 AI")
        self.btn_ai_tools.setStyleSheet(compact_btn_style)
        self.ai_menu = self.create_ai_menu(self.btn_ai_tools) if hasattr(self, 'create_ai_menu') else QMenu()
        self.btn_ai_tools.setMenu(self.ai_menu)
        row1_layout.addWidget(self.btn_ai_tools)

        self.btn_add_main_idea = QPushButton("💡 Idea")
        self.btn_add_main_idea.setStyleSheet(compact_btn_style)
        self.btn_add_main_idea.clicked.connect(self.add_custom_bubble)
        row1_layout.addWidget(self.btn_add_main_idea)

        # Shrink Undo/Redo to pure square icons
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

        # This pushes everything cleanly to the left, preventing the weird spacing
        row1_layout.addStretch()

        self.btn_toggle_row2 = QPushButton("🔽")
        self.btn_toggle_row2.setFixedWidth(28)
        self.btn_toggle_row2.setStyleSheet("padding: 2px; font-weight: bold;")
        self.btn_toggle_row2.setCursor(Qt.CursorShape.PointingHandCursor)
        row1_layout.addWidget(self.btn_toggle_row2)

        main_tb_layout.addWidget(self.row1_widget)

        # ==========================================
        # ROW 2: Filters & Secondary Tools
        # ==========================================
        self.row2_widget = QWidget()
        row2_layout = QHBoxLayout(self.row2_widget)
        row2_layout.setContentsMargins(0, 4, 0, 0)
        row2_layout.setSpacing(6)

        # 🔥 FIX: Removed text labels, widened the boxes slightly, and added hover Tooltips!
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

        # 🔥 FIX: Renamed to "Inbox" so it doesn't cut off, and explicitly explained it in the hover box.
        self.btn_unused_highlights = QPushButton("📥 Inbox")
        self.btn_unused_highlights.setStyleSheet(compact_btn_style)
        self.btn_unused_highlights.setToolTip("View all highlights you've made in your PDFs that haven't been added to this board yet.")
        self.btn_unused_highlights.clicked.connect(self.open_unused_highlights_dialog)
        row2_layout.addWidget(self.btn_unused_highlights)

        row2_layout.addStretch()

        ghost_inner = QHBoxLayout()
        ghost_inner.setSpacing(4)
        
        # 🔥 FIX: Renamed "Ghosts" to "AI Links" for clarity, and added clear tooltips
        self.chk_show_ghost_links = QCheckBox("👻")
        self.chk_show_ghost_links.setChecked(False)
        self.chk_show_ghost_links.setToolTip("Show semantic connections between notes with similar meanings.")
        ghost_inner.addWidget(self.chk_show_ghost_links)
        
        self.slider_ghost_threshold = QSlider(Qt.Orientation.Horizontal)
        self.slider_ghost_threshold.setRange(60, 95)
        self.slider_ghost_threshold.setValue(75)
        self.slider_ghost_threshold.setFixedWidth(60)
        self.slider_ghost_threshold.setToolTip("Adjust how strict the AI is when suggesting links (Higher = Stricter matching).")
        ghost_inner.addWidget(self.slider_ghost_threshold)
        
        row2_layout.addLayout(ghost_inner)

        main_tb_layout.addWidget(self.row2_widget)
        self.row2_widget.hide()

        # 🔥 FIX 3: Removed adjustSize() so the Dock stays perfectly locked in place
        def toggle_secondary_tools():
            if self.row2_widget.isVisible():
                self.row2_widget.hide()
                self.btn_toggle_row2.setText("🔽")
            else:
                self.row2_widget.show()
                self.btn_toggle_row2.setText("🔼")
                
            # 1. Force the layout to instantly register the hidden/shown widgets
            main_tb_layout.invalidate()
            main_tb_layout.activate()
            
            # 2. Extract exactly how tall it *needs* to be, and lock the QFrame to that height
            target_height = main_tb_layout.sizeHint().height()
            self.toolbar_frame.setFixedHeight(target_height)

        self.btn_toggle_row2.clicked.connect(toggle_secondary_tools)

        # 3. Trigger this exact logic once on startup so it perfectly wraps Row 1
        main_tb_layout.activate()
        self.toolbar_frame.setFixedHeight(main_tb_layout.sizeHint().height())

        # Connect Signals
        self.chk_show_ghost_links.toggled.connect(self.update_ghost_connections)
        self.slider_ghost_threshold.valueChanged.connect(self.update_ghost_connections)
        self.scene_obj.selectionChanged.connect(self.update_ghost_connections)
        self.scene_obj.changed.connect(self._on_scene_changed)

        self.update_scene_bounds()
    def get_active_ai_model(self):
        """Safely fetches the selected model from the LLM Chat dock, with 'gemma' as the fallback."""
        if hasattr(self.main_window, 'chat_docks') and self.main_window.chat_docks:
            try:
                # Attempt to read the model combo box in the first LLM dock
                combo = self.main_window.chat_docks[0].model_combo
                selected = combo.currentText().strip()
                
                # Make sure it's a real model name and not a UI placeholder
                if selected and "Error" not in selected and "Select" not in selected and "running" not in selected:
                    return selected
            except AttributeError:
                pass # Dock might be initializing or missing the combo attribute
                
        # 🔥 The new global default fallback!
        return "gemma4:e2b"
    def recenter_view(self):
        """Finds the bounding box of all nodes and smoothly centers the camera on them."""
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            # Add 50px of padding around the edges so nodes don't touch the screen borders
            rect.adjust(-50, -50, 50, 50)
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
    def _queue_background_embedding(self, node):
        """Dispatches node text to the global thread pool for embedding."""
        try:
            llm_manager = self.main_window.shared_llm_manager
            pm = self.main_window.project_manager
            text = f"{node.quote} {node.note}".strip()
            
            task = NodeEmbeddingTask(node.node_id, text, llm_manager, pm)
            QThreadPool.globalInstance().start(task)
        except Exception:
            pass

    def update_scene_bounds(self):
        """Dynamically resizes the canvas to wrap around the nodes with a healthy padding."""
        if not self.nodes:
            self.scene_obj.setSceneRect(-1500, -1500, 3000, 3000)
            return
            
        rect = self.scene_obj.itemsBoundingRect()
        # Add a 1500px buffer in every direction so they can always pan around the edges
        buffer = 1500
        rect.adjust(-buffer, -buffer, buffer, buffer)
        self.scene_obj.setSceneRect(rect)

    def _get_workspace_id(self):
        return self.current_workspace_id

    def create_ai_menu(self, parent_widget):
        menu = QMenu("🤖 AI Tools", parent_widget)
        # CRITICAL FIX: Ensure the submenu actively identifies itself with the correct title when nested
        menu.setTitle("🤖 AI Tools") 
        ai_enabled = False
        try:
            ai_enabled = self.main_window.shared_llm_manager.ai_enabled
        except: 
            pass

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
                QComboBox QAbstractItemView::item {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; padding: 4px; }}
                QComboBox QAbstractItemView::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}
                QPushButton {{ background-color: {theme['accent']}; color: #ffffff; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
                QPushButton:hover {{ background-color: {theme['accent_hover']}; }}
                QPushButton::menu-indicator {{ image: none; }}
                QLabel#CollapsingIcon {{ background-color: {theme['accent']}; color: #ffffff; padding: 6px 12px; border-radius: 4px; font-weight: bold; }}
                QCheckBox {{ color: {theme['text_main']}; font-weight: bold; background: transparent; }}
                QCheckBox::indicator {{ width: 14px; height: 14px; }}
                QSlider::groove:horizontal {{ height: 4px; background: {theme['border']}; border-radius: 2px; }}
                QSlider::handle:horizontal {{ background: {theme['accent']}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
                QFrame {{ background: transparent; border: none; }}
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

        pm = self.main_window.project_manager if self.main_window and hasattr(self.main_window, "project_manager") else None
        all_tags = pm.get_all_tags() if pm else []
        for tag in all_tags:
            tag_name = tag.get("name")
            if tag_name:
                self.tag_filter_combo.addItem(tag_name, tag_name, checked=(tag_name in checked_data))

        self.tag_filter_combo.blockSignals(False)

    def apply_tag_filter(self, tag_name):
        if not tag_name:
            return
        self._refresh_tag_list(forced_checked=[tag_name])
        self._apply_filter()

    def reset_filters(self):
        self._refresh_pdf_list()
        self._refresh_tag_list(forced_checked=["ALL_TAGS"])

        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All PDFs", "ALL", checked=True)
        if self.main_window and hasattr(self.main_window, 'project_manager') and self.main_window.project_manager:
            for pdf in self.main_window.project_manager.pdfs:
                full_name = os.path.basename(pdf)
                display_name = (full_name[:16] + "\u2026") if len(full_name) > 18 else full_name
                self.filter_combo.addItem(display_name, pdf, checked=False)
        self.filter_combo.blockSignals(False)

        self._apply_filter()

    def get_allowed_docs(self):
        checked = self.filter_combo.get_checked_items()
        if "ALL" in checked or not checked:
            if self.main_window and hasattr(self.main_window, 'project_manager') and self.main_window.project_manager:
                return [os.path.basename(p) for p in self.main_window.project_manager.pdfs]
            return []
        return [os.path.basename(p) for p in checked if p != "ALL"]

    def _apply_filter(self):
        checked_pdfs = self.filter_combo.get_checked_items()
        show_all_pdfs = "ALL" in checked_pdfs or not checked_pdfs
        
        checked_tags = self.tag_filter_combo.get_checked_items() if hasattr(self, "tag_filter_combo") else ["ALL_TAGS"]
        show_all_tags = "ALL_TAGS" in checked_tags or not checked_tags
        
        # 🔥 FIX: Safely check if any specific filters are applied
        is_filtered = not show_all_pdfs or not show_all_tags
        if hasattr(self, 'btn_clear_filter'):
            self.btn_clear_filter.setVisible(is_filtered)
        
        # Filter Nodes
        for node in self.nodes.values():
            if show_all_pdfs:
                pdf_ok = True
            else:
                if node.pdf_path is None:
                    pdf_ok = True # Always show custom structural nodes
                elif node.pdf_path in checked_pdfs:
                    pdf_ok = True
                else:
                    pdf_ok = False

            if show_all_tags:
                tag_ok = True
            else:
                node_tag_names = set(node.get_tag_names()) if hasattr(node, "get_tag_names") else set()
                tag_ok = any(tag_name in node_tag_names for tag_name in checked_tags)

            node.setVisible(pdf_ok and tag_ok)
                    
        # Filter Edges (hide edge if either connected node is hidden)
        for edge in self.edges:
            if edge.source_node.isVisible() and edge.dest_node.isVisible():
                edge.show()
            else:
                edge.hide()

        self.update_ghost_connections()

    def _build_similarity_matrix_if_needed(self):
        node_items = sorted(self.nodes.values(), key=lambda n: n.node_id)
        signature = tuple((n.node_id, (n.quote or ""), (n.note or "")) for n in node_items)
        if signature == self._similarity_signature:
            return

        self._similarity_signature = signature
        self.similarity_matrix = {}

        if len(node_items) < 2:
            return

        llm_manager = None
        try:
            temp_manager = self.main_window.shared_llm_manager
            if temp_manager and temp_manager.ai_enabled:
                llm_manager = temp_manager
        except Exception:
            llm_manager = None

        if not llm_manager:
            return

        # Ensure we have the text strings
        node_ids = [n.node_id for n in node_items]
        texts_to_embed = [f"{n.quote} {n.note}".strip() for n in node_items]

        # NEW LOGIC: Pass the project_manager to the utility function
        pm = self.main_window.project_manager
        from core.text_utils import get_semantic_similarity_matrix
        self.similarity_matrix = get_semantic_similarity_matrix(node_ids, texts_to_embed, llm_manager, pm)

    def update_ghost_connections(self):
        if self._updating_ghost_links:
            return

        self._updating_ghost_links = True
        try:
            for line_item in self.ghost_lines:
                if line_item and line_item.scene() is self.scene_obj:
                    self.scene_obj.removeItem(line_item)
            self.ghost_lines.clear()

            if not hasattr(self, "chk_show_ghost_links") or not self.chk_show_ghost_links.isChecked():
                return

            self._build_similarity_matrix_if_needed()

            threshold = self.slider_ghost_threshold.value() / 100.0
            selected_ids = {n.node_id for n in self.scene_obj.selectedItems() if isinstance(n, Node)}
            node_list = [n for n in self.nodes.values() if n.isVisible()]

            seen_pairs = set()
            for i, node_a in enumerate(node_list):
                for node_b in node_list[i + 1:]:
                    pair_key = (min(node_a.node_id, node_b.node_id), max(node_a.node_id, node_b.node_id))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    sim_score = self.similarity_matrix.get(node_a.node_id, {}).get(node_b.node_id)
                    if sim_score is None:
                        sim_score = self.similarity_matrix.get(node_b.node_id, {}).get(node_a.node_id)
                    if sim_score is None or sim_score <= threshold:
                        continue

                    p1 = node_a.mapToScene(node_a.rect().center())
                    p2 = node_b.mapToScene(node_b.rect().center())

                    # Highlight lines that touch a currently selected node
                    if node_a.node_id in selected_ids or node_b.node_id in selected_ids:
                        pen = QPen(QColor("#e8e8ff"), 3, Qt.PenStyle.DashLine)
                    else:
                        pen = QPen(QColor("#9090d0"), 2, Qt.PenStyle.DashLine)

                    line_item = GhostLineItem(
                        p1.x(), p1.y(), p2.x(), p2.y(),
                        node_a.node_id, node_b.node_id, sim_score, self
                    )
                    line_item.setPen(pen)
                    line_item.setZValue(-1)
                    self.scene_obj.addItem(line_item)
                    self.ghost_lines.append(line_item)

                    # Similarity score label at the midpoint
                    mid_x = (p1.x() + p2.x()) / 2
                    mid_y = (p1.y() + p2.y()) / 2
                    text_item = self.scene_obj.addText(f"{int(sim_score * 100)}%")
                    lbl_font = QFont()
                    lbl_font.setPointSize(7)
                    text_item.setFont(lbl_font)
                    text_item.setDefaultTextColor(pen.color())
                    text_item.setPos(
                        mid_x - text_item.boundingRect().width() / 2,
                        mid_y - text_item.boundingRect().height() / 2
                    )
                    text_item.setZValue(-0.5)
                    self.ghost_lines.append(text_item)
        finally:
            self._updating_ghost_links = False

    def _on_scene_changed(self, *args):
        if self._updating_ghost_links:
            return
        if not hasattr(self, "chk_show_ghost_links") or not self.chk_show_ghost_links.isChecked():
            return
        self.update_ghost_connections()

    def _sync_workspace(self):
        """Loads the current workspace data from SQLite and populates the canvas."""
        pm = getattr(self.main_window, 'project_manager', None)
        if not pm or not pm.project_filepath:
            return
            
        try:
            ws_id = getattr(self, 'current_workspace_id', 1)
            workspace_data = pm.get_workspace_data(ws_id)
            all_annots = pm.get_highlights()
            
            self._populate_workspace_tabs()
            self.sync_with_project(workspace_data, all_annots)
        except Exception as e:
            print(f"Error syncing workspace: {e}")

    def _convert_ghost_to_edge(self, source_id, target_id, sim_score):
        """Convert a ghost similarity line into a persistent Edge."""
        if source_id not in self.nodes or target_id not in self.nodes:
            return
        src_node = self.nodes[source_id]
        tgt_node = self.nodes[target_id]

        # Skip if an edge already exists between these two nodes
        for existing in self.edges:
            if {existing.source_node, existing.dest_node} == {src_node, tgt_node}:
                return

        self.save_state_for_undo()
        label = f"~{int(sim_score * 100)}% similar"
        edge = Edge(src_node, tgt_node, label)
        self.scene_obj.addItem(edge)
        self.edges.append(edge)
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()

    def open_unused_highlights_dialog(self):
        workspace_id = self._get_workspace_id()
        unused_highlights = self.main_window.project_manager.get_unused_highlights(workspace_id)

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

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        highlight_ids = dialog.get_selected_highlight_ids()
        if not highlight_ids:
            return

        self.save_state_for_undo()
        view_center = self.mapToScene(self.viewport().rect().center())
        last_node = None
        offset = 0
        for highlight_id in highlight_ids:
            highlight = self.main_window.project_manager.get_highlight(highlight_id)
            if not highlight:
                continue
                
            # 🔥 NEW: Extract the actual note directly from the PDF since the DB doesn't store it!
            actual_note = ""
            pdf_path = highlight.get("doc_id")
            page_num = highlight.get("page_num")
            if pdf_path and page_num is not None:
                doc = self.main_window.project_manager.get_doc(pdf_path)
                if doc:
                    page = doc.load_page(page_num)
                    for annot in page.annots():
                        if annot.info and annot.info.get("title") == highlight["id"]:
                            actual_note = annot.info.get("content", "")
                            break
                            
            position = view_center.__class__(view_center.x(), view_center.y() + offset)
            node = self.add_node_from_annotation(
                {
                    "id": highlight["id"],
                    "subject": highlight.get("text_content", ""),
                    "content": actual_note, # <==== USE THE ACTUAL NOTE!
                    "pdf_path": pdf_path,
                    "page_num": page_num,
                    "color": highlight.get("color"),
                },
                persist=True,
                position=position,
            )
            if node:
                last_node = node
                offset += 120

        self.scene_obj.clearSelection()
        if last_node:
            last_node.setSelected(True)
        self.update_scene_bounds()

    # ------------------------------------------------------------------ workspace tabs

    def _populate_workspace_tabs(self):
        """Reload the workspace combo from the DB. Called when a project is opened."""
        if not hasattr(self, 'workspace_combo'):
            return
        pm = self.main_window.project_manager
        if not pm or not pm.project_filepath:
            return

        self.workspace_combo.blockSignals(True)
        try:
            self.workspace_combo.clear()
            workspaces = pm.get_workspaces()
            current_index = 0
            for i, ws in enumerate(workspaces):
                self.workspace_combo.addItem(ws["name"], ws["id"])
                if ws["id"] == self.current_workspace_id:
                    current_index = i
            self.workspace_combo.setCurrentIndex(current_index)
        finally:
            self.workspace_combo.blockSignals(False)

    def _on_tab_changed(self, index):
        """Save current workspace and load the one selected in the combo box."""
        new_ws_id = self.workspace_combo.itemData(index)
        if new_ws_id is None or new_ws_id == self.current_workspace_id:
            return

        # Persist the current workspace before switching
        self._mark_workspace_dirty(autosave=True)

        self.current_workspace_id = new_ws_id
        self._similarity_signature = None

        pm = self.main_window.project_manager
        if not pm or not pm.project_filepath:
            return

        workspace_data = pm.get_workspace_data(self.current_workspace_id)
        all_annots = pm.get_highlights()
        self.sync_with_project(workspace_data, all_annots)

    def _add_workspace(self):
        """Prompt for a name, create a new workspace in DB, and switch to it."""
        pm = self.main_window.project_manager
        if not pm or not pm.project_filepath:
            QMessageBox.information(self, "No Project", "Please open a project first.")
            return

        name, ok = QInputDialog.getText(self, "New Workspace", "Enter workspace name:")
        if not ok or not name.strip():
            return

        name = name.strip()
        new_id = pm.create_workspace(name)
        if new_id is None:
            QMessageBox.warning(self, "Error", "Could not create workspace.")
            return

        self.workspace_combo.blockSignals(True)
        self.workspace_combo.addItem(name, new_id)
        new_index = self.workspace_combo.count() - 1
        self.workspace_combo.blockSignals(False)
        self.workspace_combo.setCurrentIndex(new_index)  # fires _on_tab_changed

    def _open_color_by_pdf_dialog(self):
        pm = self.main_window.project_manager
        if not pm: return
        
        pdfs = pm.pdfs
        tags = pm.get_all_tags()
        
        if not pdfs and not tags:
            QMessageBox.information(self, "Nothing to Color", "There are no PDFs or Tags in this project.")
            return
            
        # 1. Collect current PDF colors
        current_pdf_colors = {}
        for node in self.nodes.values():
            if node.pdf_path and node.pdf_path not in current_pdf_colors:
                current_pdf_colors[node.pdf_path] = node.color
                
        for pdf in pdfs:
            if pdf not in current_pdf_colors:
                current_pdf_colors[pdf] = "#2b2b2b"
                
        # 2. Collect current Tag colors from the DB
        current_tag_colors = {t.get("name"): t.get("color", "#808080") for t in tags if t.get("name")}
                
        dialog = ColorOrganizerDialog(pdfs, tags, current_pdf_colors, current_tag_colors, self)
        
        # 3. Apply themes
        if hasattr(self.main_window, 'theme_manager'):
            theme = self.main_window.theme_manager.get_theme()
            dialog.setStyleSheet(f"background-color: {theme['bg_main']}; color: {theme['text_main']};")
            
            # Style the tabs to match the rest of your app natively
            tab_style = f"""
                QTabWidget::pane {{ border: 1px solid {theme['border']}; border-radius: 4px; }}
                QTabBar::tab {{ background: {theme['bg_panel']}; color: {theme['text_main']}; padding: 8px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
                QTabBar::tab:selected {{ background: {theme['accent']}; color: #ffffff; font-weight: bold; }}
            """
            dialog.tab_widget.setStyleSheet(tab_style)
            
        # 4. Handle Execution
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
                    # Apply color of the first matching tag found on the node
                    node_tags = node.get_tag_names()
                    for tag_name in node_tags:
                        if tag_name in new_colors:
                            node.color = new_colors[tag_name]
                            node.setBrush(QBrush(QColor(node.color)))
                            node.refresh_layout()
                            break 
                            
            self.main_window.project_manager.mark_dirty("workspace")
            self.save_workspace_state()

    def trigger_declutter(self):
        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", "Please add or select some nodes to declutter.")
            return

        # 1. Safely grab the LLM Manager (if AI is enabled)
        dialog = DeclutterSettingsDialog(self)
        if not dialog.exec():
            return # User hit cancel
            
        use_ai, semantic_strength = dialog.get_settings()

        # 2. Safely grab the LLM Manager
        llm_manager = None
        if use_ai:
            try:
                temp_manager = self.main_window.shared_llm_manager
                if temp_manager.ai_enabled:
                    llm_manager = temp_manager
                else:
                    QMessageBox.information(self, "AI Disabled", "Ollama is not running. Falling back to standard math declutter.")
            except Exception:
                pass

        # 3. Gather text and IDs 
        node_ids = []
        texts_to_embed = []
        for n in target_nodes:
            node_ids.append(n.node_id)
            texts_to_embed.append(f"{n.quote} {n.note}".strip())

        # 4. Fetch Semantic Similarity ONLY if AI is enabled and manager is ready
        similarity_matrix = {}
        if llm_manager:
            from core.text_utils import get_semantic_similarity_matrix
            pm = self.main_window.project_manager
            similarity_matrix = get_semantic_similarity_matrix(node_ids, texts_to_embed, llm_manager, pm)

        # 5. Build info dicts for the physics engine
        nodes_info = {n.node_id: {'width': n.base_width, 'height': n.base_height} for n in target_nodes}
        edges_info = [(e.source_node.node_id, e.dest_node.node_id) 
                      for e in self.edges 
                      if e.source_node in target_nodes and e.dest_node in target_nodes]
        
        avg_x = sum(n.pos().x() + n.base_width / 2 for n in target_nodes) / len(target_nodes)
        avg_y = sum(n.pos().y() + n.base_height / 2 for n in target_nodes) / len(target_nodes)

        # 6. Execute the Physics Layout Engine, passing the new strength multiplier
        self.save_state_for_undo()
        
        from core.layout_engine import calculate_force_directed_layout
        new_positions = calculate_force_directed_layout(
            nodes_info, 
            edges_info, 
            avg_x, 
            avg_y, 
            similarity_matrix=similarity_matrix,
            semantic_strength=semantic_strength  # <--- Pass the slider value!
        )

        if not new_positions:
            return

        # 7. Apply new positions
        for node in target_nodes:
            if node.node_id in new_positions:
                pos = new_positions[node.node_id]
                node.setPos(pos['x'], pos['y'])

        for edge in self.edges:
            if edge.source_node in target_nodes and edge.dest_node in target_nodes:
                edge.update_position()

        self.main_window.project_manager.mark_dirty("workspace")
        items_rect = self.scene_obj.itemsBoundingRect()
        self.update_scene_bounds()

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
        if hasattr(self, 'btn_undo'):
            self.btn_undo.setEnabled(len(self.undo_stack) > 0)
        if hasattr(self, 'btn_redo'):
            self.btn_redo.setEnabled(len(self.redo_stack) > 0)

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
                        data["width"], data["height"], data.get("pdf_path"), data.get("page_num"), data.get("manual_font_size"), data.get("highlight_id"))
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

    # ------------------------------------------------------------------ clipboard

    def copy_selection(self):
        selected_nodes = [n for n in self.scene_obj.selectedItems() if type(n).__name__ == 'Node']
        if not selected_nodes:
            return
        selected_node_set = set(selected_nodes)
        self.clipboard['nodes'] = [
            {
                'old_id': n.node_id,
                'highlight_id': n.highlight_id,
                'quote': n.quote,
                'note_text': n.note,
                'color': n.color,
                'is_custom': n.is_custom,
                'pdf_path': n.pdf_path,
                'page_num': n.page_num,
                'manual_font_size': n.manual_font_size,
                'width': n.base_width,
                'height': n.base_height,
                'x': n.pos().x(),
                'y': n.pos().y(),
                "tags": n.get_tag_names() if hasattr(n, "get_tag_names") else [],
            }
            for n in selected_nodes
        ]
        self.clipboard['edges'] = [
            {
                'source_old_id': e.source_node.node_id,
                'dest_old_id': e.dest_node.node_id,
                'label': e.label_text,
                'color': e.base_color.name(),
                'weight': e.weight,
            }
            for e in self.edges
            if e.source_node in selected_node_set and e.dest_node in selected_node_set
        ]

    def cut_selection(self):
        self.copy_selection()
        if self.clipboard['nodes']:
            self.delete_selected_nodes()

    def paste_selection(self):
        if not self.clipboard['nodes']:
            return

        self.save_state_for_undo()
        pm = self.main_window.project_manager
        ws_id = self.current_workspace_id
        offset = 20  # shift so pastes don't overlap when re-pasting in the same workspace

        id_mapping = {}
        new_nodes = []
        for data in self.clipboard['nodes']:
            new_id = f"custom_{uuid.uuid4()}"
            id_mapping[data['old_id']] = new_id
            node = Node(
                new_id,
                data['quote'],
                data['note_text'],
                color=data['color'],
                is_custom=data['is_custom'],
                width=data['width'],
                height=data['height'],
                pdf_path=data['pdf_path'],
                page_num=data['page_num'],
                manual_font_size=data['manual_font_size'],
                highlight_id=data['highlight_id'],
            )
            
            node.setPos(data['x'] + offset, data['y'] + offset)
            self.scene_obj.addItem(node)
            self.nodes[new_id] = node
            new_nodes.append(node)
            self._queue_background_embedding(node) # <--- ADD THIS
            pm.upsert_node_record({
                'id': new_id,
                'highlight_id': data['highlight_id'],
                'quote': data['quote'],
                'note': data['note_text'],
                'color': data['color'],
                'is_custom': data['is_custom'],
                'pdf_path': data['pdf_path'],
                'page_num': data['page_num'],
                'manual_font_size': data['manual_font_size'],
                'x': data['x'] + offset,
                'y': data['y'] + offset,
                'width': data['width'],
                'height': data['height'],
            }, ws_id)

        for edata in self.clipboard['edges']:
            src_id = id_mapping.get(edata['source_old_id'])
            tgt_id = id_mapping.get(edata['dest_old_id'])
            if src_id and tgt_id and src_id in self.nodes and tgt_id in self.nodes:
                edge_id = str(uuid.uuid4())
                edge = Edge(
                    self.nodes[src_id],
                    self.nodes[tgt_id],
                    edata['label'],
                    edge_id,
                    edata['color'],
                    edata['weight'],
                )
                self.scene_obj.addItem(edge)
                self.edges.append(edge)

        self.scene_obj.clearSelection()
        for node in new_nodes:
            node.setSelected(True)

        self._similarity_signature = None
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()
        self.update_scene_bounds()

   # gui/components/workspace_view.py -> WorkspaceView class
    def keyPressEvent(self, event):
        # 🔥 FIX 5: If the text editor has focus, let the editor handle Backspace/Delete!
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
            # Cut/Copy/Paste logic removed from here—your QShortcuts handle this natively now!

        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected_nodes()
            event.accept()
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
        self.update_scene_bounds()

    def _mark_workspace_dirty(self, autosave=False):
        pm = self.main_window.project_manager if self.main_window and hasattr(self.main_window, "project_manager") else None
        if not pm:
            return
        pm.mark_dirty("workspace")
        if autosave:
            try:
                pm.save_workspace_data(self.serialize_workspace(), self.current_workspace_id)
            except Exception as e:
                print(f"Workspace autosave failed: {e}")

    def delete_edge(self, edge):
        # Remove edge from source and destination node edge lists
        if edge in edge.source_node.edges:
            edge.source_node.edges.remove(edge)
        if edge in edge.dest_node.edges:
            edge.dest_node.edges.remove(edge)

        # Remove from scene
        self.scene_obj.removeItem(edge)

        # Explicitly break circular references
        edge.source_node = None
        edge.dest_node = None

        # Remove from internal edge list
        if edge in self.edges:
            self.edges.remove(edge)
        self.main_window.project_manager.delete_edge_record(edge.edge_id)

        # Schedule for deletion (deleteLater is available on QGraphicsItem)
        if hasattr(edge, 'deleteLater'):
            edge.deleteLater()
        else:
            # Fallback: schedule deletion via QTimer if needed
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: edge.setParentItem(None))

        self._mark_workspace_dirty(autosave=True)

    def delete_selected_nodes(self):
        nodes_to_delete = [item for item in list(self.scene_obj.selectedItems()) if type(item).__name__ == 'Node']
        if not nodes_to_delete:
            return

        self.save_state_for_undo()

        for node in nodes_to_delete:
            for edge in list(self.edges)[::-1]:
                if edge.source_node == node or edge.dest_node == node:
                    if edge.source_node and edge in edge.source_node.edges:
                        edge.source_node.edges.remove(edge)
                    if edge.dest_node and edge in edge.dest_node.edges:
                        edge.dest_node.edges.remove(edge)

                    self.scene_obj.removeItem(edge)
                    if edge in self.edges:
                        self.edges.remove(edge)
                    self.main_window.project_manager.delete_edge_record(edge.edge_id)

                    edge.source_node = None
                    edge.dest_node = None
                    if hasattr(edge, 'deleteLater'):
                        edge.deleteLater()

            self.scene_obj.removeItem(node)
            node.edges = []
            if node.node_id in self.nodes:
                del self.nodes[node.node_id]
            self.main_window.project_manager.delete_node_record(node.node_id)

            if hasattr(node, 'deleteLater'):
                node.deleteLater()

        self._similarity_signature = None
        self.update_ghost_connections()
        self.main_window.project_manager.mark_dirty("workspace")

    def delete_node(self, node, delete_highlight=False):
        if delete_highlight and node.highlight_id:
            self._delete_highlight_permanently(node.highlight_id)
            return

        # Delete all connected edges first (shallow copy)
        for edge in list(node.edges):
            self.delete_edge(edge)

        # Remove node from scene
        self.scene_obj.removeItem(node)

        # Explicitly break references and remove from internal structures
        node.edges = []
        if node.node_id in self.nodes:
            del self.nodes[node.node_id]
        self.main_window.project_manager.delete_node_record(node.node_id)

        # Schedule for deletion
        if hasattr(node, 'deleteLater'):
            node.deleteLater()
        else:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: node.setParentItem(None))

        self._similarity_signature = None
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()

    def _delete_highlight_permanently(self, highlight_id):
        nodes_to_remove = [node for node in list(self.nodes.values()) if node.highlight_id == highlight_id or node.node_id == highlight_id]
        highlight_record = self.main_window.project_manager.get_highlight(highlight_id)

        for node in nodes_to_remove:
            for edge in list(node.edges):
                self.delete_edge(edge)
            self.scene_obj.removeItem(node)
            node.edges = []
            self.nodes.pop(node.node_id, None)
            self.main_window.project_manager.delete_node_record(node.node_id)
            if hasattr(node, 'deleteLater'):
                node.deleteLater()

        pdf_path = None
        page_num = None
        if highlight_record:
            pdf_path = highlight_record.get("doc_id")
            page_num = highlight_record.get("page_num")
        elif nodes_to_remove:
            pdf_path = nodes_to_remove[0].pdf_path
            page_num = nodes_to_remove[0].page_num

        if pdf_path is not None and page_num is not None:
            try:
                doc = self.main_window.project_manager.get_doc(pdf_path)
                if doc:
                    page = doc.load_page(page_num)
                    for annot in page.annots():
                        if annot.info and annot.info.get("title") == highlight_id:
                            page.delete_annot(annot)
                            break
                    self.main_window.project_manager.mark_dirty(pdf_path)
                    
                    # Live update the viewer if they are looking at it right now
                    if pdf_path == self.main_window.current_file_path and hasattr(self.main_window, 'viewer'):
                        self.main_window.viewer.reload_page(page_num)
            except Exception as e:
                print(f"Error removing physical annotation: {e}")

            # Update the notes sidebars visually if they happen to be open
            if hasattr(self.main_window, 'notes_docks'):
                for n_dock in self.main_window.notes_docks:
                    n_dock.refresh_notes()

        self.main_window.project_manager.delete_highlight_record(highlight_id)
        self._similarity_signature = None
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        
        while item and not isinstance(item, (Node, Edge)):
            item = item.parentItem()

        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]

        if len(selected_nodes) > 1 and (item is None or (isinstance(item, Node) and item in selected_nodes)):
            menu = QMenu(self)
            remove_action = menu.addAction("🗑️ Remove Selected from Workspace")
            delete_action = menu.addAction("🔥 Delete Selected Highlights Permanently")
            color_action = menu.addAction("🎨 Change Color for Selected Nodes") # <-- ADDED
            manage_tags_action = menu.addAction("🏷️ Manage Tags for Selected Nodes")
            declutter_action = menu.addAction("🧹 Declutter Selected Nodes")
            remove_action.triggered.connect(self.delete_selected_nodes)
            
            menu.addSeparator()
            ai_menu = self.create_ai_menu(menu)
            menu.addMenu(ai_menu)
            
            action = menu.exec(event.globalPos())
            if action == delete_action:
                self.save_state_for_undo()
                for highlight_id in {n.highlight_id for n in selected_nodes if n.highlight_id}:
                    self._delete_highlight_permanently(highlight_id)
            elif action == color_action:                        # <-- ADDED
                self._change_color_for_nodes(selected_nodes)    # <-- ADDED
            elif action == manage_tags_action:
                self._manage_tags_for_nodes(selected_nodes)
            elif action == declutter_action:
                self.trigger_declutter()
            return
        
                
           
        if isinstance(item, Node):
            # Snapshot the selection BEFORE we potentially clear it below.
            # This lets us detect "node A was selected, right-clicked on node B → offer connect".
            prior_selected = [n for n in selected_nodes]

            if item not in selected_nodes:
                self.scene_obj.clearSelection()
                item.setSelected(True)
                selected_nodes = [item]

            # Connect action: exactly one node was already selected, and it's not the item
            connect_source = prior_selected[0] if (len(prior_selected) == 1 and prior_selected[0] is not item) else None

            menu = QMenu(self)
            edit_action = menu.addAction("✏️ Edit Note Text")
            color_action = menu.addAction("🎨 Change Color")
            manage_tags_action = menu.addAction("🏷️ Manage Tags")

            connect_action = None
            if connect_source:
                connect_action = menu.addAction("🔗 Connect to Selected Node")
                
            remove_action = menu.addAction("🗑️ Remove Selected from Workspace")
            delete_highlight_action = None
            if item.highlight_id:
                delete_highlight_action = menu.addAction("🔥 Delete Highlight Permanently")
            declutter_action = menu.addAction("🧹 Declutter Selected Node")
            remove_action.triggered.connect(self.delete_selected_nodes)
            
    
            
            
            menu.addSeparator()
            ai_menu = self.create_ai_menu(menu)
            menu.addMenu(ai_menu)
            
            action = menu.exec(event.globalPos())
            if action == edit_action:
                item.trigger_edit()
            elif action == color_action:
                item.trigger_color_change()
            elif action == manage_tags_action:
                dlg = TagAssignmentDialog(self.main_window.project_manager, item.node_id, "node", self)
                if dlg.exec():
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

           
            analysis_menu = menu.addMenu("Related to Tag")  # <--- New Submenu
            pm = self.main_window.project_manager
            current_tags = pm.get_all_tags() if pm else []
            
            if current_tags:
                for tag in current_tags:
                    tag_name = tag.get("name")
                    if tag_name:
                        # Create a sub-menu for each individual tag
                        tag_sub = analysis_menu.addMenu(f"'{tag_name}'")
                        
                        # Add Relatives Action
                        rel_action = tag_sub.addAction("🔍 Find Relatives")
                        rel_action.triggered.connect(lambda checked, t=tag_name: self.trigger_find_tag_relatives(t))
                        
                        # Add Opposing Views Action
                        opp_action = tag_sub.addAction("⚖️ Find Opposing Views")
                        opp_action.triggered.connect(lambda checked, t=tag_name: self.trigger_tag_opposing_views(t))
            else:
                analysis_menu.addAction("No tags created yet").setEnabled(False)
            # ---------------------------
            
            menu.addSeparator()
            ai_menu = self.create_ai_menu(menu)
            menu.addMenu(ai_menu)
            
            action = menu.exec(event.globalPos())
            if action == declutter_action:
                self.trigger_declutter()
            return

        super().contextMenuEvent(event)
    def _change_color_for_nodes(self, nodes):
        if not nodes: return
        
        # Use the first node's color as the initial dialog color
        initial_color = QColor(nodes[0].color)
        color = QColorDialog.getColor(initial_color, self, "Select Color for Selected Nodes")
        
        if color.isValid():
            self.save_state_for_undo()
            color_name = color.name()
            
            for node in nodes:
                node.color = color_name
                node.setBrush(QBrush(QColor(color_name)))
                node.refresh_layout()
                
                # Sync the UI if it's a PDF note and the notes dock is open
                if not getattr(node, 'is_custom', False) and getattr(node, 'pdf_path', None) is not None:
                    annot_id = getattr(node, 'highlight_id', None) or getattr(node, 'node_id', None)
                    if hasattr(self.main_window, 'notes_docks'):
                        for notes_dock in self.main_window.notes_docks:
                            notes_dock._modify_note(
                                node.pdf_path, 
                                node.page_num, 
                                annot_id, 
                                action="edit_content", 
                                content=getattr(node, 'note', ''), 
                                refresh=False
                            )
                            
            self._mark_workspace_dirty(autosave=True)

    def trigger_find_tag_relatives(self, tag_name):
        pm = self.main_window.project_manager
        llm = self.main_window.shared_llm_manager
        
        # We don't need the dock open, but we DO need to ensure the database is mounted
        if not llm.collection and pm.project_filepath:
            llm.set_project_database(pm.project_filepath)
            
        if not llm.collection or llm.collection.count() == 0:
            QMessageBox.warning(self, "No Database", "Search index is empty. Please build it first.")
            return

        # 1. Gather all nodes on the board that have this tag
        target_nodes = [n for n in self.nodes.values() if tag_name in (n.get_tag_names() if hasattr(n, "get_tag_names") else [])]
        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", f"No nodes found with the tag '{tag_name}'.")
            return

        # 2. Fetch their pre-calculated embeddings from SQLite
        node_ids = [n.node_id for n in target_nodes]
        embeddings_dict = pm.get_node_embeddings_batch(node_ids)
        vectors = list(embeddings_dict.values())
        
        # CRITICAL FIX: If vectors didn't save properly, generate them right now!
        if len(vectors) < len(target_nodes):
            texts_to_embed = [f"{n.quote} {n.note}".strip() for n in target_nodes]
            try:
                vectors = llm.get_batch_embeddings(texts_to_embed)
            except Exception as e:
                pass
                
        if not vectors:
            QMessageBox.warning(self, "Error", "Could not generate embeddings. Ensure AI is running.")
            return

        # 3. Calculate the Centroid (Average Embedding)
        centroid_vector = [sum(col) / len(vectors) for col in zip(*vectors)]

        # 4. Query ChromaDB with the average vector
        allowed_docs = self.get_allowed_docs()
        results = llm.query_by_raw_embedding(centroid_vector, n_results=5, allowed_docs=allowed_docs)

        if not results or not results.get('documents') or not results['documents'][0]:
            QMessageBox.information(self, "No Results", "Could not find related chunks.")
            return
            
        # 5. Format and present the results in the new UI
        documents = results['documents'][0]
        metadatas = results['metadatas'][0]
        
        matches = []
        for doc_text, meta in zip(documents, metadatas):
            matches.append({
                "text": doc_text.strip(),
                "doc_name": meta.get('doc_name', 'Unknown Document'),
                "page": meta.get('page', 0)
            })
            
        
        dialog = AIResultsDialog(f"Related to '{tag_name}'", matches, self.main_window, self)
        dialog.exec()
    def trigger_tag_opposing_views(self, tag_name):
        pm = self.main_window.project_manager
        llm = self.main_window.shared_llm_manager

        if not llm.collection and pm.project_filepath:
            llm.set_project_database(pm.project_filepath)

        if not llm.collection or llm.collection.count() == 0:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "No Database", "Search index is empty. Please build it first.")
            return

        target_nodes = [n for n in self.nodes.values() if tag_name in (n.get_tag_names() if hasattr(n, "get_tag_names") else [])]
        if not target_nodes:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "No Nodes", f"No nodes found with the tag '{tag_name}'.")
            return

        # --- PHASE 1: DATABASE SEARCH (Main Thread) ---
        import os
        from PySide6.QtWidgets import QApplication

        # 1. Synthesize the tag's arguments to use as the target statement for the LLM
        combined_text = " ".join([f"{n.quote} {getattr(n, 'note', '')}".strip() for n in target_nodes])
        target_statement = f"The core arguments of the tag '{tag_name}': {combined_text[:1000]}" # Cap at 1000 chars for speed

        # 2. Get embeddings and calculate centroid
        node_ids = [n.node_id for n in target_nodes]
        embeddings_dict = pm.get_node_embeddings_batch(node_ids)
        vectors = list(embeddings_dict.values())

        if len(vectors) < len(target_nodes):
            texts_to_embed = [f"{n.quote} {getattr(n, 'note', '')}".strip() for n in target_nodes]
            try:
                vectors = llm.get_batch_embeddings(texts_to_embed)
            except Exception:
                pass

        if not vectors:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Error", "Could not generate embeddings. Ensure AI is running.")
            return

        centroid_vector = [sum(col) / len(vectors) for col in zip(*vectors)]
        allowed_docs = self.get_allowed_docs()

        # 3. Pull 30 topically relevant chunks
        try:
            results = llm.query_by_raw_embedding(centroid_vector, n_results=30, allowed_docs=allowed_docs)

            if not results or not results.get('documents') or not results['documents'][0]:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self, "No Results", "Could not find related chunks to analyze.")
                return

            documents = results['documents'][0]
            metadatas = results['metadatas'][0]
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Database Error", str(e))
            return

        # --- PHASE 2: LLM SCORING (Background Thread) ---
        active_model = None
        if hasattr(self.main_window, 'chat_docks') and self.main_window.chat_docks:
            active_model = self.main_window.chat_docks[0].model_combo.currentText()

        from PySide6.QtWidgets import QProgressDialog, QMessageBox
        from PySide6.QtCore import Qt
        
        self.loading_dialog = QProgressDialog("Initializing AI...", "Cancel", 0, 0, self.main_window)
        self.loading_dialog.setWindowTitle(f"Finding Opposing Views for '{tag_name}'")
        self.loading_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.loading_dialog.setStyleSheet("QProgressDialog { background-color: #2b2b2b; color: white; }")
        self.loading_dialog.show()

        from core.ai_opposing_views_worker import AIOpposingViewsWorker
        self.opposing_worker = AIOpposingViewsWorker(
            llm.api_base,
            llm.embedding_model,
            active_model,
            target_statement,
            documents,
            metadatas,
            search_mode="opposing",
            audit_logger=llm.audit_logger,
            parent=self.main_window
        )

        self.opposing_worker.progress.connect(self.loading_dialog.setLabelText)

        def on_finished(matches, error):
            self.loading_dialog.close()
            self.loading_dialog.deleteLater()

            try:
                if error:
                    QMessageBox.warning(self.main_window, "Error", error)
                    return
                if not matches:
                    QMessageBox.information(self.main_window, "No Opposing Views", f"The AI could not find any strongly opposing arguments to the tag '{tag_name}'.")
                    return

                from gui.components.dialogs.tag_relatives_dialog import AIResultsDialog
                dlg = AIResultsDialog(f"⚖️ Opposing Views for '{tag_name}'", matches, self.main_window, self.main_window)
                dlg.exec()
            except Exception as e:
                print(f"[UI Error] Failed to load results dialog: {e}")
                QMessageBox.critical(self.main_window, "UI Error", f"Failed to open results: {str(e)}")

        self.opposing_worker.finished.connect(on_finished)
        self.loading_dialog.canceled.connect(self.opposing_worker.terminate)
        self.opposing_worker.start()
    def trigger_ai_organize(self, selected_nodes):
        if self.is_llm_busy:
            QMessageBox.warning(self, "AI Busy", "The AI is currently processing another request.")
            return
        if not self.loading_overlay.isHidden(): return

        model = self.get_active_ai_model()
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
        llm_manager = self.main_window.shared_llm_manager
        if llm_manager.collection is None:
            self._start_llm_manager()
        self.loading_label.setText("✨ AI is analyzing and organizing your notes...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()

        self.worker = AIOrganizeWorker(llm_manager, model, nodes_data, custom_instructions=instructions.strip(), parent=self)
        self.worker.finished.connect(self._on_ai_organize_finished)
        self.worker.start()

    def _manage_tags_for_nodes(self, selected_nodes):
        if not selected_nodes:
            return

        template_node = selected_nodes[0]
        pm = self.main_window.project_manager
        dlg = TagAssignmentDialog(pm, template_node.node_id, "node", self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        template_tag_ids = {t.get("id") for t in pm.get_tags_for_node(template_node.node_id)}

        for node in selected_nodes[1:]:
            node_tag_ids = {t.get("id") for t in pm.get_tags_for_node(node.node_id)}

            for tag_id in template_tag_ids - node_tag_ids:
                pm.assign_tag_to_node(node.node_id, tag_id)
            for tag_id in node_tag_ids - template_tag_ids:
                pm.remove_tag_from_node(node.node_id, tag_id)

        for node in selected_nodes:
            node.refresh_tag_badges()

        self._refresh_tag_list()
        self._apply_filter()
        pm.mark_dirty("workspace")
    def _start_llm_manager(self):
        try:
                # Attempt to get the project path. Adjust the property name if your 
                # project manager uses something other than 'project_filepath'.
                proj_path = getattr(self.main_window.project_manager, 'project_filepath', None)
                if not proj_path:
                    # Fallback in case the variable is named differently
                    proj_path = getattr(self.main_window.project_manager, 'current_project_path', None) 
                
                if proj_path:
                    self.main_window.shared_llm_manager.set_project_database(proj_path)
                else:
                    QMessageBox.warning(self, "Database Error", "Could not locate the project database path.")
                    return
        except Exception as e:
                QMessageBox.warning(self, "Database Error", f"Failed to mount search database: {e}")
                return
        
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
                cluster_node = Node(cluster_node_id, quote="", note=c_name, color="#0078D7", is_custom=True, width=180, height=60, node_origin="ai")
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
        if self.is_llm_busy:
            QMessageBox.warning(self, "AI Busy", "The AI is currently processing another request.")
            return
        if not self.loading_overlay.isHidden(): return

        model = self.get_active_ai_model()
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

        llm_manager = self.main_window.shared_llm_manager
        if llm_manager.collection is None:
            self._start_llm_manager()
        self.loading_label.setText("✨ AI is analyzing relationships and finding new connections...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()
        self.is_llm_busy = True
        self.conn_worker = AIFindConnectionsWorker(llm_manager, model, nodes_data, edges_data, parent=self)
        self.conn_worker.finished.connect(self._on_find_connections_finished)
        self.conn_worker.start()

    def _on_find_connections_finished(self, new_connections, error_msg):
        self.is_llm_busy = False
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
        if self.is_llm_busy:
            QMessageBox.warning(self, "AI Busy", "The AI is currently processing another request.")
            return
        if not self.loading_overlay.isHidden(): return

        model = self.get_active_ai_model()
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

        llm_manager = self.main_window.shared_llm_manager
        if llm_manager.collection is None:
            self._start_llm_manager()
        self.loading_label.setText("✨ AI is analyzing argument structure and drafting outline...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()
        self.is_llm_busy = True
        self.outline_worker = AIOutlineWorker(llm_manager, model, nodes_data, edges_data, parent=self)
        self.outline_worker.finished.connect(self._on_generate_outline_finished)
        self.outline_worker.start()

    def _on_generate_outline_finished(self, outline_text, error_msg):
        self.is_llm_busy = False
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
        if self.is_llm_busy:
            QMessageBox.warning(self, "AI Busy", "The AI is currently processing another request.")
            return
        if not self.loading_overlay.isHidden(): return

        model = self.get_active_ai_model()
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

        llm_manager = self.main_window.shared_llm_manager
        if llm_manager.collection is None:
            self._start_llm_manager()
        self.loading_label.setText("✨ AI is evaluating argument strength and identifying weak points...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()
        self.is_llm_busy = True
        self.weakpoints_worker = AIWeakpointsWorker(llm_manager, model, nodes_data, edges_data, parent=self)
        self.weakpoints_worker.finished.connect(self._on_identify_weakpoints_finished)
        self.weakpoints_worker.start()

    def _on_identify_weakpoints_finished(self, analysis_text, error_msg):
        self.is_llm_busy = False
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
        if self.is_llm_busy:
            QMessageBox.warning(self, "AI Busy", "The AI is currently processing another request.")
            return
        if not self.loading_overlay.isHidden(): return

        model = self.get_active_ai_model()
        if not model or "Error" in model or "running" in model:
            QMessageBox.warning(self, "No Model Selected", "Please select a valid AI model in the LLM Chat tab first.")
            return
        pm = self.main_window.project_manager
        llm_manager = self.main_window.shared_llm_manager
        if llm_manager.collection is None and pm.project_filepath:
            llm_manager.set_project_database(pm.project_filepath)
        
        
        # 1. ERROR HANDLING FIX: Explicitly check for an empty or missing index
        if llm_manager.collection is None or llm_manager.collection.count() == 0:
            QMessageBox.critical(self, "Search Index Missing", "The search index is empty. Please add PDFs and build the project index in the LLM Chat tab before using AI tools.")
            return

        selected_nodes = [n for n in self.scene_obj.selectedItems() if isinstance(n, Node)]
        target_nodes = selected_nodes if selected_nodes else [n for n in self.nodes.values() if n.isVisible()]

        if not target_nodes:
            QMessageBox.warning(self, "No Nodes", "Please add or select some nodes in the workspace first.")
            return

       
        enforce_tags = False
        node_tags_map = {}

        # 2. THRESHOLD INTERCEPTION: Force tagging on large projects
        if len(pm.pdfs) > 3:
            dialog = ContextFilterDialog(pm, target_nodes, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return # User cancelled the operation
            
            enforce_tags = True
            
            # Pre-fetch node tag mappings to safely pass to the background thread
            for n in target_nodes:
                tags = pm.get_tags_for_node(n.node_id)
                if tags:
                    # We extract the tag names since that's how ChromaDB metadata is keyed
                    node_tags_map[n.node_id] = [t.get("name") for t in tags if t.get("name")]

            if not node_tags_map:
                QMessageBox.warning(self, "No Tags Assigned", "You must tag at least one node to proceed. The AI needs this to filter context.")
                return

        nodes_data = []
        for n in target_nodes:
            # RULE ENFORCEMENT: Completely skip untagged nodes if threshold was met
            if enforce_tags and n.node_id not in node_tags_map:
                continue
                
            nodes_data.append({
                "id": n.node_id, 
                "type": "user_created" if n.is_custom else "pdf_note", 
                "text": f"{n.quote} \n {n.note}".strip()
            })

        if not nodes_data:
            QMessageBox.warning(self, "No Valid Nodes", "No tagged nodes were found. Please tag your nodes to proceed.")
            return

        edges_data = [{"source_id": e.source_node.node_id, "target_id": e.dest_node.node_id, "label": e.label_text} 
                    for e in self.edges if e.source_node in target_nodes and e.dest_node in target_nodes]

        allowed_docs = self.get_allowed_docs()

        self.loading_label.setText("✨ AI is analyzing graph to find missing evidence...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()
        self.is_llm_busy = True
        
        # 3. Pass the new tagging parameters to the worker
        self.fill_worker = AIFillGraphWorker(
            llm_manager, model, nodes_data, edges_data, allowed_docs, 
            enforce_tags=enforce_tags, node_tags_map=node_tags_map, parent=self
        )
        self.fill_worker.progress.connect(self._update_loading_label)
        self.fill_worker.finished.connect(self._on_fill_graph_finished)
        self.fill_worker.start()
    def _update_loading_label(self, text):
        self.loading_label.setText(text + "\nThis may take a moment.")

    def _on_fill_graph_finished(self, evidence_items, error_msg):
        self.is_llm_busy = False
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
            # --- NEW: Manually spawn the nodes onto the CURRENT canvas first ---
            for item, (new_annot_id, target_node_id) in zip(evidence_items, new_annot_mappings):
                hl_record = self.main_window.project_manager.get_highlight(new_annot_id)
                if hl_record:
                    node = self.add_node_from_annotation({
                        "id": hl_record["id"],
                        "subject": item["quote"],
                        "content": item["note"],
                        "pdf_path": hl_record.get("doc_id"),
                        "page_num": hl_record.get("page_num"),
                        "color": hl_record.get("color", "#9c27b0")
                    }, persist=True, target_workspace_id=self.current_workspace_id)
                    
                    # Plop it visually to the right of the node it supports
                    if node and target_node_id in self.nodes:
                        tgt = self.nodes[target_node_id]
                        node.setPos(tgt.pos().x() + 320, tgt.pos().y())
            # -------------------------------------------------------------------

            workspace_data = self.serialize_workspace()
            
            for new_annot_id, target_node_id in new_annot_mappings:
                # Double check the nodes actually exist before wiring the edge!
                if target_node_id in self.nodes and new_annot_id in self.nodes:
                    workspace_data["edges"].append({
                        "id": str(uuid.uuid4()),
                        "source": target_node_id,
                        "target": new_annot_id,
                        "label": "AI Evidence",
                        "color": "#9c27b0",
                        "weight": 3
                    })
            
            self.main_window.project_manager.save_workspace_data(workspace_data, self.current_workspace_id)
            self.main_window.project_manager.mark_dirty("workspace")

            all_annots = self.main_window.project_manager.get_highlights()
            self.sync_with_project(workspace_data, all_annots)
            
            self.main_window.viewer.annot_manager.note_added.emit()
            
            QMessageBox.information(self, "Graph Filled", f"Successfully found and connected {added_count} piece(s) of evidence!")
        else:
            QMessageBox.information(self, "Graph Filled", "Searched for evidence but could not successfully highlight valid quotes in the documents.")
    def trigger_consolidate_notes(self):
        if self.is_llm_busy:
            QMessageBox.warning(self, "AI Busy", "The AI is currently processing another request.")
            return
        if not self.loading_overlay.isHidden(): return

        model = self.get_active_ai_model()
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

        llm_manager = self.main_window.shared_llm_manager
        if llm_manager.collection is None:
            self._start_llm_manager()
        self.loading_label.setText("✨ AI is restructuring and streamlining your argument...\nThis may take a moment.")
        self.loading_overlay.resize(self.viewport().size())
        self.loading_overlay.show()
        self.is_llm_busy = True
        self.consolidate_worker = AIConsolidateWorker(llm_manager, model, nodes_data, edges_data, parent=self)
        self.consolidate_worker.progress.connect(self._update_loading_label)
        self.consolidate_worker.finished.connect(self._on_consolidate_finished)
        self.consolidate_worker.start()
    def save_workspace_state(self):
        """Standalone save method for independent dock instances."""
        data = self.serialize_workspace()
        self.main_window.project_manager.save_workspace_data(data, self.current_workspace_id)
        self._mark_workspace_dirty(autosave=True)

    # gui/components/workspace_view.py -> WorkspaceView class
    def handle_highlight_created(self, highlight_data):
        """Auto-spawns a bubble STRICTLY on the Main Board (Workspace 1) when a highlight is made in the PDF."""
        if highlight_data.get("id") in self.nodes and self.current_workspace_id == 1: 
            return
            
        # 🔥 FIX 1: Ensure new notes ALWAYS pop up in the main workspace, never bleeding into others.
        if self.current_workspace_id == 1:
            self.add_node_from_annotation(highlight_data, persist=True, target_workspace_id=1)
        else:
            # Save silently to Workspace 1's database without rendering it on the current canvas
            pm = self.main_window.project_manager
            if pm:
                quote = highlight_data.get("subject") or highlight_data.get("text_content") or ""
                note = highlight_data.get("content") or highlight_data.get("note_text") or ""
                color = highlight_data.get("color") or "#2b2b2b"
                w = 200 if len(note + quote) < 50 else (250 if len(note + quote) < 150 else 300)
                h = 70 if len(note + quote) < 50 else (110 if len(note + quote) < 150 else 160)
                pm.upsert_node_record({
                    "id": highlight_data["id"], "highlight_id": highlight_data["id"],
                    "quote": quote, "note": note, "color": color,
                    "is_custom": False,
                    "pdf_path": highlight_data.get("pdf_path") or highlight_data.get("doc_id"),
                    "page_num": highlight_data.get("page_num"),
                    "manual_font_size": None,
                    "x": 0.0, "y": 0.0, "width": w, "height": h,
                }, 1) # <--- Force route to Workspace 1
    def _on_consolidate_finished(self, result_dict, error_msg):
        self.is_llm_busy = False
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
            self._mark_workspace_dirty(autosave=True)
            
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
        self._similarity_signature = None
        self._mark_workspace_dirty(autosave=True)
        self.update_ghost_connections()
        
        # Select the newly created node visually
        self.scene_obj.clearSelection()
        node.setSelected(True)
        
        # Trigger hover properties and editor
        node.is_hovered = True
        node.refresh_layout()
        node.trigger_edit()
        self._queue_background_embedding(node)

    def sync_with_project(self, workspace_data, pdf_annotations, force_reload=False):
        # Always reload workspace when called (revert force_reload logic)
        selected_ids = [n_id for n_id, n in self.nodes.items() if n.isSelected()]
        h_scroll = self.horizontalScrollBar().value()
        v_scroll = self.verticalScrollBar().value()
        self.scene_obj.clear()
        self.nodes.clear()
        self.edges.clear()

        if isinstance(pdf_annotations, dict):
            annot_dict = pdf_annotations
        else:
            annot_dict = {a["id"]: a for a in pdf_annotations}

        saved_nodes = workspace_data.get("nodes", {})
        for n_id, data in saved_nodes.items():
            quote = data.get("quote", "")
            note = data.get("note", "")
            highlight_id = data.get("highlight_id")

            if highlight_id and highlight_id in annot_dict:
                quote = annot_dict[highlight_id].get("text_content", quote) or quote
            elif n_id in annot_dict:
                quote = annot_dict[n_id].get("text_content", quote) or quote

            node = Node(n_id, quote, note, data["color"], data["is_custom"], 
                        data["width"], data["height"], 
                        data.get("pdf_path") or annot_dict.get(highlight_id or n_id, {}).get("doc_id"), 
                        data.get("page_num") if data.get("page_num") is not None else annot_dict.get(highlight_id or n_id, {}).get("page_num"), 
                        data.get("manual_font_size"), highlight_id,
                        data.get("node_origin", "human"),    # <--- LOAD SAVED STATE
                        data.get("is_verified", 0),
                        data.get("original_text", note))
            node.setPos(data["x"], data["y"])
            self.scene_obj.addItem(node)
            self.nodes[n_id] = node

        should_initialize_nodes = (
            self.current_workspace_id == 1
            and self.main_window.project_manager.get_metadata("workspace_nodes_initialized", "0") != "1"
        )
        if should_initialize_nodes:
            y_offset = 50
            for annot in annot_dict.values():
                if annot["id"] not in self.nodes:
                    # 🔥 NEW: Fetch note from PDF for first-time node initialization
                    actual_note = ""
                    pdf_path = annot.get("doc_id")
                    page_num = annot.get("page_num")
                    if pdf_path and page_num is not None:
                        doc = self.main_window.project_manager.get_doc(pdf_path)
                        if doc:
                            page = doc.load_page(page_num)
                            for pdf_annot in page.annots():
                                if pdf_annot.info and pdf_annot.info.get("title") == annot["id"]:
                                    actual_note = pdf_annot.info.get("content", "")
                                    break
                                    
                    self.add_node_from_annotation(
                        {
                            "id": annot["id"],
                            "subject": annot.get("text_content", ""),
                            "content": actual_note, # <==== USE THE ACTUAL NOTE!
                            "pdf_path": pdf_path,
                            "page_num": page_num,
                            "color": annot.get("color"),
                        },
                        persist=False,
                        position=self.mapToScene(50, y_offset),
                    )
                    y_offset += 100
            self.main_window.project_manager.set_metadata("workspace_nodes_initialized", "1")

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
        self._refresh_tag_list()
        self._apply_filter()
        self._similarity_signature = None
        self.update_ghost_connections()
        self.update_scene_bounds()
        self.horizontalScrollBar().setValue(h_scroll)
        self.verticalScrollBar().setValue(v_scroll)

        if should_initialize_nodes:
            self._mark_workspace_dirty(autosave=True)

        

    def serialize_workspace(self):
        data = {"nodes": {}, "edges": []}
        for n_id, node in self.nodes.items():
            data["nodes"][n_id] = {
                "highlight_id": node.highlight_id,
                "workspace_id": self.current_workspace_id,
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
                "height": node.base_height,
                "node_origin": getattr(node, "node_origin", "human"), # <--- ADD THIS
                "is_verified": int(getattr(node, "is_verified", 0)),
                "original_text": getattr(node, "original_text", getattr(node, "note", ""))
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