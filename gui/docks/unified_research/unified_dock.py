from PySide6.QtWidgets import (QCheckBox, QDockWidget, QWidget, QHBoxLayout, QVBoxLayout,
                             QStackedWidget, QDialog,QPushButton, QLabel, QComboBox, QFrame, QButtonGroup,QMessageBox, QMenu,QTextEdit,QScrollArea, QLineEdit)
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QCursor, QAction

from gui.docks.unified_research.components.context_filter_dialog import ContextFilterDialog
from gui.docks.unified_research.tabs.blueprint_editor_tab import BlueprintEditorTab
from gui.docks.unified_research.tabs.custom_tools_tab import CustomToolsTab
from .tabs.chat_tab import ChatTab
from .tabs.research_agent_tab import ResearchAgentTab
from .tabs.brainstorm_tab import BrainstormTab
from .tabs.search_tab import SearchTab
from .tabs.anaylsis_tab import AnalysisTab
from .components.note_bubble import NoteBubbleWidget
from gui.components.process_monitor import ProcessMonitorWidget
import json
from gui.docks.unified_research.components.manifest_bubble import ProjectBriefDialog
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload
class IndexWorker(QThread):
    progress = Signal(str)
    finished_indexing = Signal(bool, str)

    def __init__(self, llm, filepaths, parent=None):
        super().__init__(parent)
        self.llm = llm
        self.filepaths = filepaths

    def run(self):
        try:
            self.llm.index_documents(self.filepaths, progress_callback=lambda msg: self.progress.emit(msg))
            self.finished_indexing.emit(True, "")
        except Exception as e:
            self.finished_indexing.emit(False, str(e))

class UnifiedResearchDock(QDockWidget):
    global_context_changed = Signal(list, list)

    def __init__(self, main_window, project_manager, llm_manager, parent=None):
        super().__init__("🔬 Research Workspace", parent)
        self.setObjectName("UnifiedResearchDock")
        self.main_window = main_window
        self.project_manager = project_manager
        self.llm_manager = llm_manager



        self.central_widget = QWidget()
        self.setWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #444;")
        self.main_layout.addWidget(line)

        # 3. Add the Process Monitor at the bottom (fixed height so it doesn't take over)

        self.active_docs, self.active_tags = [], []
        self.tag_logic = "AND"
        self.theme = None

        self._build_sidebar()
        self._build_core_area()

        # --- NEW: Subscribe to Global Events ---
        self.bus = EventBus.get_instance()
        self.bus.pdf_switched.connect(self._on_pdf_switched_event)
    def _on_pdf_switched_event(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event == DocumentEvent.PDF_SWITCHED:
            self._on_pdf_switched(payload.path)

    def _on_pdf_switched(self, pdf_path=None):
        """Safe wrapper to handle global event signals."""
        self.refresh_project_ui()
    def _build_sidebar(self):
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(50)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(4, 10, 4, 10)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        # --- ADD THE NEW TAB ICON HERE ---
        nav_items = [("💬", 0), ("🧭", 1), ("💡", 2), ("🔍", 3), ("📊", 4), ("🧰", 5), ("🛠️", 6)]

        for icon, index in nav_items:
            btn = QPushButton(f"{icon}")
            btn.setCheckable(True)
            btn.setFixedSize(40, 60)
            btn.setStyleSheet("QPushButton { border: none; background: transparent; font-size: 10px; font-weight: bold; color: #888; } QPushButton:checked { color: #b366ff; }")
            btn.clicked.connect(lambda checked, idx=index: self.stacked_widget.setCurrentIndex(idx))
            self.nav_group.addButton(btn, index)
            sidebar_layout.addWidget(btn)
        sidebar_layout.addStretch()
        self.nav_group.button(0).setChecked(True)
        self.main_layout.addWidget(self.sidebar)

    def _build_core_area(self):
        core_widget = QWidget()
        core_layout = QVBoxLayout(core_widget)
        core_layout.setContentsMargins(0, 0, 0, 0)

        self.header = QFrame()
        from PySide6.QtWidgets import QGridLayout
        header_layout = QGridLayout(self.header)
        header_layout.setContentsMargins(10, 5, 10, 5)

        self.lbl_status = QLabel("🔴 Unindexed")
        header_layout.addWidget(self.lbl_status, 0, 0)

        self.model_combo = QComboBox()
        self.model_combo.addItems(self.llm_manager.get_available_models() or ["llama3"])
        header_layout.addWidget(self.model_combo, 0, 1)

        self.model_combo.currentTextChanged.connect(
            lambda txt: self.bus.active_model_changed.emit(
                WorkspaceEvent.ACTIVE_MODEL_CHANGED,
                WorkspaceEventPayload(model_name=txt),
            )
        )
        # Fire it once on startup so background services cache the initial state
        QTimer.singleShot(
            100,
            lambda: self.bus.active_model_changed.emit(
                WorkspaceEvent.ACTIVE_MODEL_CHANGED,
                WorkspaceEventPayload(model_name=self.model_combo.currentText()),
            ),
        )

        self.btn_brief = QPushButton("📝 Project Manifest")
        self.btn_brief.clicked.connect(lambda: ProjectBriefDialog(self.project_manager, self.theme, self).exec())
        header_layout.addWidget(self.btn_brief, 0, 2)

        self.btn_filter = QPushButton("⚙️ Filter Context")
        self.btn_filter.clicked.connect(self._open_filter_dialog)
        header_layout.addWidget(self.btn_filter, 0, 3)

        self.btn_index = QPushButton("🔄 Index")
        self.btn_index.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_index.clicked.connect(self.start_indexing)
        header_layout.addWidget(self.btn_index, 0, 4)
        core_layout.addWidget(self.header)

        self.stacked_widget = QStackedWidget()
        self.tab_chat = ChatTab(self.main_window, parent=self)
        self.tab_agent = ResearchAgentTab(self.main_window, parent=self)
        self.tab_plan = BrainstormTab(self.main_window, parent=self)
        self.tab_search = SearchTab(self.main_window,parent=self)
        self.tab_analysis = AnalysisTab(self.main_window, parent=self)
        self.tab_custom = CustomToolsTab(self.main_window, parent=self)
        self.tab_editor = BlueprintEditorTab(self.main_window, parent=self)

        for tab in [self.tab_chat, self.tab_agent, self.tab_plan, self.tab_search, self.tab_analysis, self.tab_custom, self.tab_editor]:
            self.stacked_widget.addWidget(tab)
        core_layout.addWidget(self.stacked_widget)
        self.main_layout.addWidget(core_widget)
    def load_tab_history(self, tab_widget, tab_id):
        """Universally rebuilds chat UI for any tab from the SQLite history."""
        if not self.project_manager: return

        # 1. Clear existing UI (leaving the bottom stretch)


        # 2. Fetch and build
        history = self.project_manager.get_chat_history(tab_id)
        for msg in history:
            is_user = (msg["role"] == "user")
            name = "You" if is_user else "AI Agent"

            if msg["ui_format"] == "chat_widgets":
                try:
                    items = json.loads(msg["content"])
                    if isinstance(items, dict):
                        for val in items.values():
                            if isinstance(val, list): items = val; break
                        if isinstance(items, dict): items = [items]

                    widget = ChatMessageWidget(name, theme=self.theme, is_user=is_user)
                    for item in items:
                        if isinstance(item, dict):
                            widget.add_bubble(
                                doc_name=item.get("doc_name", "Unknown Document"),
                                quote=item.get("quote", item.get("text", "")),
                                note=item.get("note", item.get("reason", ""))
                            )
                    if hasattr(tab_widget, 'add_message_widget'): tab_widget.add_message_widget(widget)
                except Exception as e:
                    print(f"Failed to rebuild chat widget: {e}")
            else:
                widget = ChatMessageWidget(name, theme=self.theme, is_user=is_user)
                widget.append_chunk(msg["content"])
                if hasattr(tab_widget, 'add_message_widget'): tab_widget.add_message_widget(widget)

        # 3. Universal Scroll to Bottom
        if hasattr(tab_widget, 'scroll_area'):
            scrollbar = tab_widget.scroll_area.verticalScrollBar()
            QTimer.singleShot(50, lambda: scrollbar.setValue(scrollbar.maximum()))

    def clear_tab_history(self, tab_widget, tab_id):
        """Universally wipes SQLite history and clears the UI for a specific tab."""
        if not self.project_manager: return

        self.project_manager.clear_chat_history(tab_id)

        if hasattr(tab_widget, 'chat_layout'):
            while tab_widget.chat_layout.count() > 1:
                item = tab_widget.chat_layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
    def refresh_project_ui(self):
        """Initializes or reloads the UI state from the active project database."""
        self.check_index_status()
        self.load_tab_history(self.tab_chat, "chat_dock")
        self.load_tab_history(self.tab_plan, "brainstorm_dock")
        self.load_tab_history(self.tab_custom, "custom_tools_tab")
    # --- THE UNIVERSAL "SEND TO" MENU ATTACHER ---
    def attach_send_to_menu(self, text_browser_widget):
        """Attaches a custom context menu to any text widget to send text between tabs."""
        text_browser_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        text_browser_widget.customContextMenuRequested.connect(lambda pos: self._show_send_menu(text_browser_widget, pos))

    def _show_send_menu(self, widget, pos):
        menu = widget.createStandardContextMenu()
        selected_text = widget.textCursor().selectedText().strip()
        if selected_text:
            menu.addSeparator()
            send_menu = QMenu("Send to Tab...", menu)

            chat_action = QAction("💬 Chat", menu)
            chat_action.triggered.connect(lambda: self.route_text_to_tab(selected_text, 0))
            send_menu.addAction(chat_action)

            plan_action = QAction("💡 Plan", menu)
            plan_action.triggered.connect(lambda: self.route_text_to_tab(selected_text, 2))
            send_menu.addAction(plan_action)

            search_action = QAction("🔍 Search", menu)
            search_action.triggered.connect(lambda: self.route_text_to_tab(selected_text, 3))
            send_menu.addAction(search_action)

            menu.addMenu(send_menu)
        menu.exec_(widget.mapToGlobal(pos))

    def route_text_to_tab(self, text, tab_idx):
        self.nav_group.button(tab_idx).setChecked(True)
        self.stacked_widget.setCurrentIndex(tab_idx)
        target = self.stacked_widget.widget(tab_idx)
        if hasattr(target, 'inject_text'): target.inject_text(text)

    def update_theme(self, theme):
        self.theme = theme
        self.sidebar.setStyleSheet(f"background-color: {theme.get('bg_panel', '#222')}; border-right: 1px solid {theme.get('border', '#444')};")
        self.header.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border-bottom: 1px solid {theme.get('border', '#444')};")
        self.central_widget.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")

        # Apply standard styling to the remaining top header widgets
        widget_style = f"background-color: {theme.get('bg_panel', '#333')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px 8px; border-radius: 4px;"

        self.model_combo.setStyleSheet(widget_style)
        self.btn_brief.setStyleSheet(widget_style)
        self.btn_filter.setStyleSheet(widget_style)
        self.btn_index.setStyleSheet(widget_style)

        # Pass theme safely to child tabs
        if hasattr(self, 'tab_chat'): self.tab_chat.update_theme(theme)
        if hasattr(self, 'tab_agent'): self.tab_agent.update_theme(theme)
        if hasattr(self, 'tab_plan'): self.tab_plan.update_theme(theme)
        if hasattr(self, 'tab_search'): self.tab_search.update_theme(theme)
        if hasattr(self, 'tab_analysis'): self.tab_analysis.update_theme(theme)
        if hasattr(self, 'tab_editor'): self.tab_editor.update_theme(theme)
        if hasattr(self, 'tab_custom'): self.tab_custom.update_theme(theme) # <-- FIX: Was missing!

        self.check_index_status()

        # --- THE FIX: Stop wiping/rebuilding the SQLite history UI! ---
        # Instead of calling load_tab_history(), we just loop over the existing
        # chat widgets currently visible in the layout and update their colors.
        for tab in [self.tab_chat, self.tab_agent, self.tab_plan, self.tab_custom]:
            if hasattr(tab, 'chat_layout'):
                for i in range(tab.chat_layout.count()):
                    widget = tab.chat_layout.itemAt(i).widget()
                    if hasattr(widget, 'update_theme'):
                        widget.update_theme(theme)

    def _open_filter_dialog(self):
        if not self.active_docs and self.project_manager.pdfs:
            import os
            self.active_docs = [os.path.basename(p) for p in self.project_manager.pdfs]

        dialog = ContextFilterDialog(
            self.project_manager,
            self.active_docs,
            self.active_tags,
            self.tag_logic,
            self.theme,
            self
        )
        if dialog.exec():
            self.active_docs, self.active_tags, self.tag_logic = dialog.get_results()
            self.project_manager.set_metadata("active_rag_docs", json.dumps(self.active_docs))
            self.project_manager.set_metadata("active_rag_tags", json.dumps(self.active_tags))
            self.project_manager.set_metadata("active_rag_tag_logic", self.tag_logic)
            doc_count = len(self.active_docs)
            tag_count = len(self.active_tags)
            self.btn_filter.setText(f"⚙️ Filter Context ({doc_count} Docs, {tag_count} Tags)")
    def check_index_status(self):
        """Silently checks if the database is loaded and indexed."""
        proj_path = self.project_manager.project_filepath
        if proj_path and self.llm_manager:
            # Ensure the Chroma client is pointed at the current project
            if self.llm_manager.collection is None:
                self.llm_manager.set_project_database(proj_path)

            try:
                if self.llm_manager.collection and self.llm_manager.collection.count() > 0:
                    self.lbl_status.setText("🟢 Indexed")
                    color = self.theme['success'] if self.theme and 'success' in self.theme else "#00cc66"
                    self.lbl_status.setStyleSheet(f"font-weight: bold; margin-right: 15px; color: {color};")
                else:
                    self.lbl_status.setText("🔴 Unindexed")
                    color = self.theme['warning'] if self.theme and 'warning' in self.theme else "#ffaa00"
                    self.lbl_status.setStyleSheet(f"font-weight: bold; margin-right: 15px; color: {color};")
            except Exception:
                self.lbl_status.setText("🔴 Unindexed")

    def start_indexing(self):
        """Starts the background embedding thread."""
        paths_to_index = self.project_manager.pdfs
        if not paths_to_index:
            QMessageBox.warning(self, "Error", "No PDFs available in project to index.")
            return

        self.btn_index.setEnabled(False)
        self.idx_worker = IndexWorker(self.llm_manager, paths_to_index, parent=self)
        self.idx_worker.progress.connect(self._update_index_progress)
        self.idx_worker.finished_indexing.connect(self._on_index_complete)
        self.idx_worker.start()

    def _update_index_progress(self, msg):
        self.lbl_status.setText(f"🟡 {msg}")
        self.lbl_status.setStyleSheet("font-weight: bold; margin-right: 15px; color: #ffcc00;")

    def _on_index_complete(self, success, error_msg):
        """Called when the background thread finishes."""
        self.btn_index.setEnabled(True)
        if success:
            # Sync any new tags to ChromaDB
            pm = self.project_manager
            if hasattr(pm, '_sync_doc_tags_for_llm'):
                for pdf_path in pm.pdfs:
                    pm._sync_doc_tags_for_llm(pdf_path)

            # Re-evaluate the status label
            self.check_index_status()
        else:
            self.lbl_status.setText("❌ Indexing Failed")
            color = self.theme['error'] if self.theme and 'error' in self.theme else "#ff4444"
            self.lbl_status.setStyleSheet(f"font-weight: bold; margin-right: 15px; color: {color};")
            print(f"Indexing Error: {error_msg}")
