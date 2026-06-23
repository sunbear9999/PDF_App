from PySide6.QtWidgets import (QCheckBox, QDockWidget, QWidget, QHBoxLayout, QVBoxLayout,
                             QStackedWidget, QDialog, QPushButton, QLabel, QComboBox, QFrame, QButtonGroup, QMessageBox, QMenu, QTextEdit, QScrollArea, QLineEdit)
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QCursor, QAction
from gui.managers.dialog_manager import exec_as_modal, get_for_widget

from gui.docks.unified_research.components.context_filter_dialog import ContextFilterDialog
from gui.docks.unified_research.tabs.blueprint_editor_tab import BlueprintEditorTab
from gui.docks.unified_research.tabs.custom_tools_tab import CustomToolsTab
from .tabs.chat_tab import ChatTab
from .tabs.research_agent_tab import ResearchAgentTab
from .tabs.brainstorm_tab import BrainstormTab
from .tabs.search_tab import SearchTab
from .tabs.analysis_tab import AnalysisTab
from gui.components.process_monitor import ProcessMonitorWidget
import json
from gui.docks.unified_research.components.manifest_bubble import ProjectBriefDialog
from gui.docks.unified_research.components.history_renderer import ChatHistoryRenderer
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload
from core.events.domains.workspace_events import WorkspaceEvent, WorkspaceEventPayload
from core.events.domains.project_events import ProjectEvent
from gui.utils.document_helpers import active_pdf_names, active_pdf_paths, prune_doc_names


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

    def __init__(self, app_context, parent=None):
        super().__init__("🔬 Research Workspace", parent)
        self.setObjectName("UnifiedResearchDock")
        self.app_context = app_context
        self.project_manager = app_context.project_manager
        self.llm_manager = app_context.llm_manager
        self.bus = app_context.bus

        self.central_widget = QWidget()
        self.central_widget.setMinimumSize(320, 320)
        self.setWidget(self.central_widget)
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFixedWidth(1)
        line.setStyleSheet("background-color: #444;")
        self.main_layout.addWidget(line)

        self.active_docs, self.active_tags = [], []
        self.tag_logic = "AND"
        self.theme = None
        self._plugin_tab_widgets = []
        # plugin_id → [(btn, widget, tab_id)]
        self._plugin_tab_map: dict = {}
        self._injected_tab_ids: set = set()

        self._build_sidebar()
        self._build_core_area()
        self._inject_plugin_tabs()

        # --- Subscribe to Global Events ---
        self.bus.pdf_switched.connect(self._on_pdf_switched_event)
        self.bus.document_added.connect(self._on_document_list_changed)
        self.bus.pdf_removed.connect(self._on_document_list_changed)
        self.bus.pdf_renamed.connect(self._on_document_list_changed)
        self.bus.project_loaded.connect(self._on_project_loaded)
        self.bus.plugin_unloaded.connect(self.remove_plugin_tabs)
        self.bus.plugin_loaded.connect(self._on_plugin_loaded)
        # Auto-update theme whenever the app theme changes
        self.bus.theme_changed.connect(self._on_bus_theme_changed)
        # Track active model changes so app_context.active_model stays current
        self.bus.active_model_changed.connect(self._on_active_model_changed)

    def _on_active_model_changed(self, event, payload):
        if event == WorkspaceEvent.ACTIVE_MODEL_CHANGED and hasattr(payload, "model_name"):
            self.app_context.active_model = payload.model_name

    def _open_bias_lab(self):
        from gui.docks.unified_research.components.bias_detector_dialog import BiasDetectorDialog
        dm = get_for_widget(self)
        if dm:
            dm.show(
                BiasDetectorDialog,
                key="bias_lab",
                singleton=True,
                factory=lambda: BiasDetectorDialog(
                    self.app_context,
                    self.theme,
                    self
                ),
            )

    def _on_pdf_switched_event(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event == DocumentEvent.PDF_SWITCHED:
            self._on_pdf_switched(payload.path)

    def _on_document_list_changed(self, event, payload):
        if event in {DocumentEvent.DOCUMENT_ADDED, DocumentEvent.PDF_REMOVED, DocumentEvent.PDF_RENAMED}:
            self._prune_active_docs()
            self.check_index_status()

    def _on_project_loaded(self, event, payload):
        if event == ProjectEvent.LOADED:
            self._prune_active_docs()
            self.refresh_project_ui()

    def _prune_active_docs(self):
        self.active_docs = prune_doc_names(self.project_manager, self.active_docs)
        if self.project_manager:
            try:
                self.project_manager.set_metadata("active_rag_docs", json.dumps(self.active_docs))
            except Exception:
                pass
        if hasattr(self, "btn_filter"):
            self.btn_filter.setText(f"⚙️ Filter Context ({len(self.active_docs)} Docs, {len(self.active_tags)} Tags)")

    def _on_bus_theme_changed(self, event, theme):
        if isinstance(theme, dict):
            self.update_theme(theme)

    def closeEvent(self, event):
        for widget in getattr(self, "_plugin_tab_widgets", []):
            if hasattr(widget, "_stop_loader"):
                widget._stop_loader()
            elif hasattr(widget, "_stop_worker"):
                widget._stop_worker()
        super().closeEvent(event)

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
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(3)

        # Row 1: index status + model selector
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.lbl_status = QLabel("🔴 Unindexed")
        row1.addWidget(self.lbl_status)

        self.model_combo = QComboBox()
        available_models = self.llm_manager.get_available_models() or ["llama3"]
        self.model_combo.addItems(available_models)
        active_model = self.app_context.get_active_ai_model()
        if active_model in available_models:
            self.model_combo.setCurrentText(active_model)
        row1.addWidget(self.model_combo, 1)

        self.btn_index = QPushButton("🔄 Index")
        self.btn_index.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_index.clicked.connect(self.start_indexing)
        row1.addWidget(self.btn_index)
        header_layout.addLayout(row1)

        def _on_model_changed(txt):
            self.app_context.active_model = txt
            self.bus.active_model_changed.emit(
                WorkspaceEvent.ACTIVE_MODEL_CHANGED,
                WorkspaceEventPayload(model_name=txt),
            )

        self.model_combo.currentTextChanged.connect(_on_model_changed)
        # Fire once on startup so background services cache the initial state
        QTimer.singleShot(
            100,
            lambda: self.bus.active_model_changed.emit(
                WorkspaceEvent.ACTIVE_MODEL_CHANGED,
                WorkspaceEventPayload(model_name=self.model_combo.currentText()),
            ),
        )

        # Row 2: action buttons
        row2 = QHBoxLayout()
        row2.setSpacing(4)

        self.btn_brief = QPushButton("📝 Manifest")
        self.btn_brief.clicked.connect(self._open_project_brief)
        row2.addWidget(self.btn_brief)

        self.btn_bias = QPushButton("⚖️ Bias Lab")
        self.btn_bias.clicked.connect(self._open_bias_lab)
        row2.addWidget(self.btn_bias)

        self.btn_filter = QPushButton("⚙️ Filter Context")
        self.btn_filter.clicked.connect(self._open_filter_dialog)
        row2.addWidget(self.btn_filter, 1)
        header_layout.addLayout(row2)

        core_layout.addWidget(self.header)

        self.stacked_widget = QStackedWidget()
        self.tab_chat = ChatTab(self.app_context, parent=self)
        self.tab_agent = ResearchAgentTab(self.app_context, parent=self)
        self.tab_plan = BrainstormTab(self.app_context, parent=self)
        self.tab_search = SearchTab(self.app_context, parent=self)
        self.tab_analysis = AnalysisTab(self.app_context, parent=self)
        self.tab_custom = CustomToolsTab(self.app_context, parent=self)
        self.tab_editor = BlueprintEditorTab(self.app_context, parent=self)

        for tab in [self.tab_chat, self.tab_agent, self.tab_plan, self.tab_search, self.tab_analysis, self.tab_custom, self.tab_editor]:
            self.stacked_widget.addWidget(tab)
        core_layout.addWidget(self.stacked_widget)

        # Wrap core_widget + no-AI overlay in a QStackedWidget for graceful degradation
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(core_widget)       # index 0 — normal view
        self._content_stack.addWidget(self._build_no_ai_overlay())  # index 1 — setup prompt

        # Show the correct page based on current AI availability
        ai_available = getattr(self.llm_manager, "ai_enabled", True)
        self._content_stack.setCurrentIndex(0 if ai_available else 1)

        self.bus.ai_availability_changed.connect(self._on_ai_availability_changed)
        self.main_layout.addWidget(self._content_stack)

    def _build_no_ai_overlay(self) -> QWidget:
        """Placeholder shown when no AI backend is installed."""
        overlay = QWidget()
        lay = QVBoxLayout(overlay)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(12)

        icon = QLabel("🤖")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 48px;")

        title = QLabel("No AI Backend Installed")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: 700;")

        body = QLabel(
            "AI features require a local language model backend.\n"
            "Install Ollama or llama.cpp to get started."
        )
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        body.setStyleSheet("font-size: 13px; color: #888; max-width: 320px;")

        btn = QPushButton("Set Up AI Features")
        btn.setFixedHeight(36)
        btn.setFixedWidth(200)
        btn.setStyleSheet(
            "background: #0078D7; color: #fff; border: none; "
            "border-radius: 6px; font-size: 13px; font-weight: 600;"
        )
        btn.clicked.connect(self._open_ai_setup)

        lay.addStretch()
        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addWidget(body)
        lay.addSpacing(8)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addStretch()
        return overlay

    def _open_ai_setup(self) -> None:
        from gui.components.dialogs.settings_dialog import GlobalSettingsDialog
        dm = getattr(self.app_context, "dialog_manager", None)
        if dm:
            dm.show(
                GlobalSettingsDialog,
                key="settings",
                singleton=True,
                factory=lambda: GlobalSettingsDialog(
                    self.app_context,
                    parent=self.parent(),
                ),
            )

    def _on_ai_availability_changed(self, available: bool) -> None:
        self._content_stack.setCurrentIndex(0 if available else 1)
        if available:
            # Refresh model combo when backend comes online
            try:
                models = self.llm_manager.get_available_models() or []
                self.model_combo.clear()
                self.model_combo.addItems(models)
            except Exception:
                pass

    def _inject_plugin_tabs(self) -> None:
        """Inject research tabs contributed by plugins via PluginExtensionRegistry."""
        registry = getattr(self.app_context, "plugin_extension_registry", None)
        if not registry:
            return
        sidebar_layout = self.sidebar.layout()
        for spec in registry.get_research_tabs():
            if spec.tab_id in self._injected_tab_ids:
                continue  # Already injected (e.g. after plugin re-enable)
            try:
                widget = spec.factory(self.app_context) if spec.factory else None
                if widget is None:
                    print(f"[PluginTab] Warning: factory for '{spec.tab_id}' returned None — tab skipped")
                    continue
                self.stacked_widget.addWidget(widget)
                self._plugin_tab_widgets.append(widget)
                self._injected_tab_ids.add(spec.tab_id)
                if self.theme and hasattr(widget, "update_theme"):
                    widget.update_theme(self.theme)

                # Sidebar nav button — use setCurrentWidget so indices don't matter
                icon_text = spec.icon if spec.icon else spec.label.split()[0]
                btn = QPushButton(icon_text)
                btn.setCheckable(True)
                btn.setFixedSize(40, 60)
                btn.setToolTip(spec.label)
                btn.setStyleSheet(
                    "QPushButton { border: none; background: transparent; font-size: 10px;"
                    " font-weight: bold; color: #888; }"
                    " QPushButton:checked { color: #b366ff; }"
                )
                plugin_id = getattr(spec, "plugin_id", "")
                if plugin_id:
                    btn.setProperty("papyrus_plugin_id", plugin_id)
                btn.clicked.connect(lambda checked, w=widget: self.stacked_widget.setCurrentWidget(w))
                self.nav_group.addButton(btn)
                # Insert before the stretch at the bottom of the sidebar
                sidebar_layout.insertWidget(sidebar_layout.count() - 1, btn)

                # Track for later removal
                if plugin_id:
                    self._plugin_tab_map.setdefault(plugin_id, []).append(
                        (btn, widget, spec.tab_id)
                    )

                # Register with AI output router if this tab has a target_id
                if spec.target_id and getattr(self.app_context, "ui_router", None):
                    self.app_context.ui_router.register_target(spec.target_id, widget)

            except Exception as exc:
                print(f"[PluginTab] Failed to inject tab '{spec.tab_id}': {exc}")

    def remove_plugin_tabs(self, plugin_id: str) -> None:
        """Remove all sidebar tabs and stacked widgets belonging to plugin_id."""
        entries = self._plugin_tab_map.pop(plugin_id, [])
        sidebar_layout = self.sidebar.layout()
        for btn, widget, tab_id in entries:
            try:
                # Switch away if currently showing this tab
                if self.stacked_widget.currentWidget() is widget:
                    self.stacked_widget.setCurrentIndex(0)
                    self.nav_group.button(0).setChecked(True)
                self.nav_group.removeButton(btn)
                sidebar_layout.removeWidget(btn)
                btn.setVisible(False)
                btn.deleteLater()
                self.stacked_widget.removeWidget(widget)
                if hasattr(widget, "_stop_loader"):
                    widget._stop_loader()
                elif hasattr(widget, "_stop_worker"):
                    widget._stop_worker()
                widget.close()
                widget.deleteLater()
                self._injected_tab_ids.discard(tab_id)
                if widget in self._plugin_tab_widgets:
                    self._plugin_tab_widgets.remove(widget)
            except RuntimeError:
                pass

    def _on_plugin_loaded(self, plugin_id: str) -> None:
        """Re-inject tabs for a plugin that was just enabled."""
        self._inject_plugin_tabs()

    def load_tab_history(self, tab_widget, tab_id):
        """Universally rebuilds chat UI for any tab from the SQLite history."""
        if not self.project_manager: return

        history = self.project_manager.get_chat_history(tab_id)
        if hasattr(tab_widget, "render_chat_history"):
            tab_widget.render_chat_history(history, clear_existing=True)
        else:
            ChatHistoryRenderer(tab_widget, theme=self.theme).render(history)

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
        self.btn_bias.setStyleSheet(widget_style)
        self.btn_filter.setStyleSheet(widget_style)
        self.btn_index.setStyleSheet(widget_style)

        # Pass theme safely to child tabs
        if hasattr(self, 'tab_chat'): self.tab_chat.update_theme(theme)
        if hasattr(self, 'tab_agent'): self.tab_agent.update_theme(theme)
        if hasattr(self, 'tab_plan'): self.tab_plan.update_theme(theme)
        if hasattr(self, 'tab_search'): self.tab_search.update_theme(theme)
        if hasattr(self, 'tab_analysis'): self.tab_analysis.update_theme(theme)
        if hasattr(self, 'tab_editor'): self.tab_editor.update_theme(theme)
        if hasattr(self, 'tab_custom'): self.tab_custom.update_theme(theme)
        for widget in getattr(self, "_plugin_tab_widgets", []):
            if hasattr(widget, "update_theme"):
                widget.update_theme(theme)
            elif hasattr(widget, "apply_theme"):
                widget.apply_theme(theme)

        self.check_index_status()

        # Loop over the existing chat widgets and update their colors without destroying history
        for tab in [self.tab_chat, self.tab_agent, self.tab_plan, self.tab_custom]:
            if hasattr(tab, 'chat_layout'):
                for i in range(tab.chat_layout.count()):
                    widget = tab.chat_layout.itemAt(i).widget()
                    if hasattr(widget, 'update_theme'):
                        widget.update_theme(theme)

    def _open_project_brief(self):
        dm = get_for_widget(self)
        if dm:
            dm.show(
                ProjectBriefDialog,
                key="project_brief",
                singleton=True,
                factory=lambda: ProjectBriefDialog(self.project_manager, self.theme, self),
            )
        else:
            exec_as_modal(ProjectBriefDialog(self.project_manager, self.theme, self))

    def _open_filter_dialog(self):
        self._prune_active_docs()
        if not self.active_docs and active_pdf_paths(self.project_manager):
            self.active_docs = active_pdf_names(self.project_manager)

        # Collect plugin RAG sources from the extension registry
        plugin_rag_sources = []
        ext_registry = getattr(self.app_context, "plugin_extension_registry", None)
        if ext_registry and hasattr(ext_registry, "get_rag_source_filters"):
            for spec in ext_registry.get_rag_source_filters():
                raw = self.project_manager.get_metadata(
                    f"rag_source_enabled:{spec.source_id}", "true"
                )
                plugin_rag_sources.append({
                    "id": spec.source_id,
                    "label": spec.label,
                    "description": spec.description,
                    "enabled": raw != "false",
                })

        dialog = ContextFilterDialog(
            self.project_manager,
            self.active_docs,
            self.active_tags,
            self.tag_logic,
            self.theme,
            self,
            plugin_rag_sources=plugin_rag_sources,
        )

        def _on_accepted():
            try:
                self.active_docs, self.active_tags, self.tag_logic = dialog.get_results()
                self.project_manager.set_metadata("active_rag_docs", json.dumps(self.active_docs))
                self.project_manager.set_metadata("active_rag_tags", json.dumps(self.active_tags))
                self.project_manager.set_metadata("active_rag_tag_logic", self.tag_logic)
                doc_count = len(self.active_docs)
                tag_count = len(self.active_tags)
                self.btn_filter.setText(f"⚙️ Filter Context ({doc_count} Docs, {tag_count} Tags)")
            except RuntimeError:
                pass

        dialog.accepted.connect(_on_accepted)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dialog)
        else:
            if exec_as_modal(dialog):
                _on_accepted()

    def check_index_status(self):
        """Silently checks if the database is loaded and indexed."""
        proj_path = self.project_manager.project_filepath if self.project_manager else None
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
        paths_to_index = active_pdf_paths(self.project_manager)
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
                for pdf_path in active_pdf_paths(pm):
                    pm._sync_doc_tags_for_llm(pdf_path)

            # Re-evaluate the status label
            self.check_index_status()
        else:
            self.lbl_status.setText("❌ Indexing Failed")
            color = self.theme['error'] if self.theme and 'error' in self.theme else "#ff4444"
            self.lbl_status.setStyleSheet(f"font-weight: bold; margin-right: 15px; color: {color};")
            print(f"Indexing Error: {error_msg}")
