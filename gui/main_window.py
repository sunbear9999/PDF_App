# gui/main_window.py
import os
import sys
import uuid
import fitz
import shutil
from PySide6.QtWidgets import (QMainWindow, QSizePolicy, QWidget, QHBoxLayout, QVBoxLayout,
                             QPushButton, QLabel, QStackedWidget,
                             QFileDialog, QFrame, QButtonGroup, QMessageBox, QComboBox, QMenu,
                             QApplication, QDockWidget, QListWidget, QListWidgetItem, QTextEdit,QInputDialog) # <-- Added Dock, List, and TextEdit
from PySide6.QtGui import QColor, QShortcut, QKeySequence
from PySide6.QtCore import Qt, QSettings, QTimer, QThread, QEvent

from core.engine.ui_router import BlueprintUIRouter
from core.project_manager import ProjectManager
from gui.components.dialogs.extract_pages_dialog import ExtractPagesDialog
from gui.components.pdf_viewer import PDFViewer
from gui.components.process_monitor import ProcessMonitorWidget
from gui.docks.ocr_dock import OCRTab
from gui.docks.tts_dock import TTSTab
from gui.docks.notes_dock import NotesTab
from gui.theme.theme import ThemeManager
from gui.components.help_dialog import HelpDialog
from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
from gui.components.dialogs.tag_manager_dialog import TagManagerDialog, TagAssignmentDialog
from core.prompt_manager import PromptManager
from core.dictionary_manager import DictionaryManager
from core.citation_manager import CitationManager
from gui.docks.citation_dock import CitationDock
from gui.docks.essay_dock import EssayTab
from gui.components.workspace_view import WorkspaceView
from core.engine.process_manager import ProcessRegistry
from gui.components.universal_overlay import UniversalInternalOverlay
from core.engine.master_runner import MasterActionRunner
from core.engine.blueprint_manager import BlueprintManager
from core.engine.step_manager import StepManager
from gui.managers.layout_manager import LayoutManager
from core.events.event_bus import EventBus
from gui.components.main_toolbar import MainToolbar
from core.events.domains.document_events import AnnotationIntent, AnnotationPayload, DocumentIntent, DocumentPayload
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload, ProjectIntent, ProjectPayload

class MainWindow(QMainWindow):
    # ADD `core` to the signature
    def __init__(self, core):
        super().__init__()

        self.core = core # Save the reference
        self.setObjectName("PapyrusMainWindow")
        self.setWindowTitle("Papyrus - Ethical, Offline Research Assistant")
        self._apply_smart_window_size()
        self.setMinimumSize(800, 600)
        self.settings = QSettings("PDFMultitool", "Workspace")
        self.theme_manager = ThemeManager()
        self.current_file_path = None

        # 1. Map pointers from PapyrusCore (Replaces all instantiation!)
        self.bus = core.bus
        self.process_registry = core.process_registry
        self.project_manager = core.project_manager
        self.shared_llm_manager = core.llm_manager
        self.prompt_manager = core.prompt_manager
        self.step_manager = core.step_manager
        self.blueprint_registry = core.blueprint_registry
        self.blueprint_manager = core.blueprint_manager
        self.workflow_node_type_registry = core.workflow_node_type_registry
        self.dictionary_manager = core.dictionary_manager
        self.citation_manager = core.citation_manager

        self.workspace_ai_tools_registry = core.workspace_ai_tools_registry
        self.workspace_node_type_registry = core.workspace_node_type_registry
        self.workspace_service = core.workspace_service
        self.workspace_graph_service = core.workspace_graph_service

        # --- IMPORTANT LEGACY HOOK ---
        # Keep this for now so the ProjectManager can still warn the Viewer before saving
        self.project_manager.main_window = self

        # 2. Setup the GUI Managers
        from gui.managers.dock_manager import DockManager
        self.dock_manager = DockManager(self)
        self.layout_manager = LayoutManager(self)
        from gui.managers.dock_registry import register_default_docks
        register_default_docks(self.dock_manager, self)

        # 3. Initialize GUI-dependent services locally (We will decouple these later)
        from core.services.workspace_services import WorkspaceAnnotationService, WorkspaceAIService
        self.workspace_annotation_service = WorkspaceAnnotationService(self, self.bus)
        self.workspace_ai_service = WorkspaceAIService(
            self, self.workspace_service, self.workspace_graph_service,
            self.workspace_annotation_service, self.workspace_ai_tools_registry, self.bus, self
        )
        self.workspace_ai_service.error.connect(lambda msg: QMessageBox.warning(self, "AI Error", msg))

        from core.services.ai_bootstrap_service import AIBootstrapService
        self.ai_bootstrap_service = AIBootstrapService(self)

        # 4. Connect to global events
        self.bus.project_loaded.connect(self._handle_project_event_ui_update)
        # 1. INITIALIZE VIEWER EXPLICITLY ONCE
        def get_resource_path(relative_path):
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller execution: use the temporary extracted folder
                return os.path.join(sys._MEIPASS, relative_path)
            # Normal script execution: use the project root
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            return os.path.join(root_dir, relative_path)
        self.viewer = PDFViewer()





        # 3. CONFIGURE DOCKS
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks |
            QMainWindow.DockOption.AnimatedDocks |
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.GroupedDragging
        )
        self.setDockNestingEnabled(True)
        self.blueprint_manager = BlueprintManager(self.blueprint_registry)
        # 4. SET CENTRAL WIDGET PROPERLY
        self.central_wrapper = QWidget()
        self.central_layout = QVBoxLayout(self.central_wrapper)
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.central_layout.setSpacing(0)
        self.setCentralWidget(self.central_wrapper)


        # 6. BUILD UI
        self.process_monitor = ProcessMonitorWidget(self.process_registry, self.theme_manager.get_theme())
        self.top_toolbar = MainToolbar(self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.top_toolbar)
        self._build_ocr_banner()
        self._build_workspace()
        self._setup_shortcuts()

        # Connect Theme Manager
        self.theme_manager.theme_changed.connect(self.update_theme)
        self.update_theme(self.theme_manager.get_theme())

        # Timers
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(lambda: self.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload()))
        self.autosave_timer.start(5 * 60 * 1000)


        if self.settings.value("show_help_on_startup", True, type=bool):
            QTimer.singleShot(500, self.show_help_window)

        # DELAY THE STARTUP: Wait 50ms for the 0x0 window to physically draw on screen
        # before attempting to calculate tabbed dock layouts.
        QTimer.singleShot(50, self._run_startup_sequence)
        self.universal_overlay = UniversalInternalOverlay(self, self.theme_manager.get_theme())
        self.ui_router = BlueprintUIRouter(self)
        self.ui_router.register_target("floating", self.universal_overlay)
        from core.services.research_agent_service import ResearchAgentService
        self.research_agent_service = ResearchAgentService(
            self.project_manager,
            self.prompt_manager,
            self.blueprint_registry,
            workflow_executor=lambda blueprint, state: self.prepare_ai_runner(blueprint, state),
            runner_starter=self.enqueue_ai_runner,
            model_provider=self._get_active_ai_model,
            parent=self,
        )
        self.bus.project_loaded.connect(self._refresh_research_agent_from_project_event)
        self.bus.document_action_requested.connect(self._handle_doc_ui_requests)
        self.bus.annotation_action_requested.connect(self._handle_annot_ui_requests)
        self.bus.project_action_requested.connect(self._handle_project_ui_requests)
        self.bus.project_action_requested.connect(self._handle_project_intents)

    def _handle_project_intents(self, intent: ProjectIntent, payload: ProjectPayload):
        if intent == ProjectIntent.FLUSH_UI_STATES:
            # 1. Ask LayoutManager to save the physical dock arrangement
            if hasattr(self, 'layout_manager'):
                self.layout_manager.save_current_session()

            # 2. Save pure Window layout bytes
            try:
                state_bytes = self.saveState().toBase64().data().decode('utf-8')
                counts = self.layout_manager.get_current_dock_counts()
                import json
                self.project_manager.set_metadata("window_layout_state", state_bytes)
                self.project_manager.set_metadata("open_docks_count", json.dumps(counts))
            except Exception: pass
        elif intent == ProjectIntent.SAVE_COMPLETED:
            self._show_save_indicator()

    def _handle_project_ui_requests(self, action: ProjectIntent, payload: ProjectPayload):
        if action == ProjectIntent.EXPORT_LOG_RESULT:
            from PySide6.QtWidgets import QMessageBox
            success = payload.get("success")
            if success:
                QMessageBox.information(self, "Success", f"LLM Log successfully exported to:\n{payload.get('path')}")
            else:
                QMessageBox.warning(self, "Error", payload.get("msg", "Failed to generate the report."))

    def _handle_doc_ui_requests(self, intent: DocumentIntent, payload: DocumentPayload):
        if intent == DocumentIntent.SHOW_OCR_BANNER:
            if hasattr(self, 'ocr_banner'):
                self.ocr_banner.show()
    def _handle_annot_ui_requests(self, action: AnnotationIntent, payload: AnnotationPayload):
        if action == AnnotationIntent.EDIT_POPUP:
            annot_id = payload.get("annot_id")
            page_num = payload.get("page_num")
            pdf_path = payload.get("pdf_path")
            target_annot = payload.get("target_annot")

            # Close existing popup if one is open
            if hasattr(self, 'quick_note_popup') and self.quick_note_popup:
                try: self.quick_note_popup.close()
                except RuntimeError: pass

            from gui.components.dialogs.quick_note_dialog import QuickNoteDialog
            self.quick_note_popup = QuickNoteDialog(
                target_annot, annot_id, page_num, pdf_path,
                self.project_manager, self.bus, self.theme_manager.get_theme(), self
            )
            self.quick_note_popup.show()
        elif action == AnnotationIntent.JUMP_TO_PAGE:
            page_num = payload.get("page_num")
            if page_num is not None and hasattr(self, "viewer"):
                self.viewer.jump_to_page(page_num)
        elif action == AnnotationIntent.FORCE_REDRAW:
            page_num = payload.get("page_num")
            pdf_path = payload.get("pdf_path")
            if (
                page_num is not None
                and hasattr(self, "viewer")
                and (not pdf_path or pdf_path == getattr(self, "current_file_path", None))
            ):
                self.viewer.reload_page(page_num)


    def execute_ai_blueprint(self, blueprint, initial_state, is_express=False):
        """
        The singular entry point for ALL AI operations in the app.
        Tabs just call this, and the UI Router handles the rest.
        """
        runner = self.prepare_ai_runner(blueprint, initial_state, is_express=is_express)
        self.enqueue_ai_runner(runner, is_express=is_express)
        return runner

    def prepare_ai_runner(self, blueprint, initial_state, is_express=False):
        if self.shared_llm_manager.collection is None:
            self.project_manager._mount_project_database()

        runner = MasterActionRunner(self, blueprint, initial_state)
        self.ui_router.attach_runner(runner)
        return runner

    def enqueue_ai_runner(self, runner, is_express=False):
        blueprint = getattr(runner, "blueprint", None)

        job_name = getattr(blueprint, 'name', 'AI Action')

        # Determine job type based on express flag for the UI Monitor
        job_type = "Express Tool" if is_express else "Agent"

        self.process_registry.enqueue_runner(runner, job_name, job_type, is_express=is_express)
        return runner

    def _get_active_ai_model(self):
        dock = getattr(self, "unified_dock", None)
        combo = getattr(dock, "model_combo", None)
        if combo:
            return combo.currentText()
        models = self.shared_llm_manager.get_available_models() or []
        return models[0] if models else ""

    def _run_startup_sequence(self):
        settings = QSettings("PDFMultitool", "Workspace")
        last_project_path = settings.value("last_project_path", "")
        import os
        if last_project_path and os.path.exists(last_project_path):
            # 🔥 FIX: Use the background service to load the project!
            self.bus.project_action_requested.emit(ProjectIntent.LOAD, ProjectPayload(path=last_project_path))
        else:
            if hasattr(self, 'layout_manager'):
                self.layout_manager.restore_last_session()

    def _handle_project_event_ui_update(self, event: ProjectEvent, payload: ProjectEventPayload):
        if event == ProjectEvent.LOADED:
            self._on_project_loaded_ui_update()

    def _refresh_research_agent_from_project_event(self, event: ProjectEvent, payload: ProjectEventPayload):
        if event == ProjectEvent.LOADED:
            self.research_agent_service.refresh_from_project()

    def _on_project_loaded_ui_update(self):
        """Called blindly when the background service finishes loading a project DB."""
        self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")

        if hasattr(self, 'doc_explorer'):
            self.doc_explorer.refresh_list()
            self.doc_explorer.refresh_tag_filter()

        # --- UI Restoration (Moved from the deleted _load_project) ---
        self.setUpdatesEnabled(False)
        try:
            state_str = self.project_manager.get_metadata("window_layout_state")
            dock_info = self.project_manager.get_metadata("open_docks_count")

            if state_str and dock_info:
                import json
                try: self.layout_manager._apply_state(state_str, json.loads(dock_info))
                except Exception: pass
            else:
                self.layout_manager.restore_last_session()

            # Restore Scratchpads
            text_data = self.project_manager.get_metadata("scratchpad_texts")
            if text_data:
                import json
                try:
                    saved_texts = json.loads(text_data)
                    for i, editor in enumerate(self.dock_manager.get_inner_widgets("scratchpads")):
                        if i < len(saved_texts): editor.setPlainText(saved_texts[i])
                except Exception: pass

            # Refresh Research Docks
            for r in self.dock_manager.get_instances("research"):
                if hasattr(r, 'refresh_project_ui'): r.refresh_project_ui()

            # 🔥 FIX: Tell the bus to open the first PDF instead of calling switch_to_pdf!
            if self.project_manager.pdfs:
                self.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=self.project_manager.pdfs[0]))
        finally:
            self.setUpdatesEnabled(True)
    def show_help_window(self,initial_tab_index=0):
        # We keep a reference to it so it doesn't get garbage collected
        self.help_dialog = HelpDialog(self,initial_tab_index=initial_tab_index)
        self.help_dialog.show()

    def _apply_smart_window_size(self):
        screen = QApplication.primaryScreen()
        if not screen:
            self.setMinimumSize(800, 600)
            self.resize(1200, 800)
            return

        available = screen.availableGeometry()
        min_width = max(780, int(available.width() * 0.6))
        min_height = max(560, int(available.height() * 0.65))
        self.setMinimumSize(min_width, min_height)

        width = max(min_width, int(available.width() * 0.9))
        height = max(min_height, int(available.height() * 0.9))
        width = min(width, available.width())
        height = min(height, available.height())

        x = available.x() + (available.width() - width) // 2
        y = available.y() + (available.height() - height) // 2
        self.setGeometry(x, y, width, height)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(lambda: self.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload()))
        QShortcut(QKeySequence("F11"), self).activated.connect(self.toggle_full_screen)


    def toggle_full_screen(self):
        from PySide6.QtCore import Qt

        if self.isFullScreen():
            # 1. Forcefully strip ALL window states (including maximized and fullscreen)
            self.setWindowState(Qt.WindowState.WindowNoState)

            # 2. Tell the OS to restore the window
            self.showNormal()

            # 3. THE X11 WAKEUP HACK:
            # Because the Chromium OpenGL context makes XFCE stubborn, we force a
            # microscopic resize. This forces the OS window manager to redraw the borders.
            self.resize(self.width() - 1, self.height())
            self.resize(self.width() + 1, self.height())
        else:
            self.setWindowState(Qt.WindowState.WindowFullScreen)
            self.showFullScreen()

        # 4. Safely update the UI Button
        if hasattr(self, 'btn_fullscreen'):
            now_full = self.isFullScreen()
            icon = "🗗" if now_full else "⛶"
            label = "Exit Full Screen" if now_full else "Full Screen"
            self.btn_fullscreen.setProperty("compact_icon", icon)
            self.btn_fullscreen.setProperty("expanded_text", f"{icon} {label}")
            self.top_toolbar._set_button_hover_state(self.btn_fullscreen, self.btn_fullscreen.property("hover_expanded"))


    def _open_prompt_editor(self):
        dialog = PromptEditorDialog(self.prompt_manager, self)
        dialog.exec()

    def _open_tag_manager(self):
        dialog = TagManagerDialog(self)
        dialog.exec()

        # 1. Update the Document Explorer's dropdown
        if hasattr(self, 'doc_explorer'):
            self.doc_explorer.refresh_tag_filter()

        # 2. Tell the unified research dock to refresh its filters via the registry
        for r in self.dock_manager.get_instances("research"):
            if hasattr(r, 'refresh_project_ui'):
                r.refresh_project_ui()

    def _on_theme_changed(self, theme_name):
        if theme_name == "Custom":
            self.theme_manager.edit_custom_theme(self)

        self.settings.setValue("theme", theme_name)
        self.theme_manager.set_theme(theme_name)

    def update_theme(self, theme):
        self.top_toolbar.setStyleSheet(f"background-color: {theme['bg_panel']}; border-bottom: 1px solid {theme['border']};")
        self.ocr_banner.setStyleSheet(f"background-color: {theme['warning']}; border-bottom: 1px solid {theme['border']};")
        self.lbl_ocr_banner.setStyleSheet(f"font-weight: bold; color: #1e1e1e; border: none;")

        # Iterate over registry instances safely
        for dock_id, inst_list in self.dock_manager.instances.items():
            alive_docks = []
            for dock in inst_list:
                try:
                    _ = dock.objectName() # Touch to check if C++ object is alive
                    if dock_id == "scratchpads" and dock.widget():
                        dock.widget().setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: none;")
                    else:
                        inner = dock.widget()
                        if inner and hasattr(inner, 'update_theme'):
                            inner.update_theme(theme)
                        elif hasattr(dock, 'update_theme'):
                            dock.update_theme(theme)
                    alive_docks.append(dock)
                except RuntimeError:
                    pass
            self.dock_manager.instances[dock_id] = alive_docks

        if hasattr(self.viewer, "update_theme"):
            try: self.viewer.update_theme(theme)
            except RuntimeError: pass

        if hasattr(self, 'process_monitor'):
            self.process_monitor.set_theme(theme)

        from gui.theme.global_styles import get_global_stylesheet
        self.setStyleSheet(get_global_stylesheet(theme))

    def broadcast_note_added(self):
        self._mark_current_dirty()
        for notes_view in self.dock_manager.get_inner_widgets("notes"):
            notes_view.refresh_notes()
        for ws_view in self.dock_manager.get_inner_widgets("workspaces"):
            ws_view.save_workspace_state()

    def _show_save_indicator(self):
        """Displays visual confirmation that the master database successfully committed."""
        self.statusBar().showMessage("💾 Project saved successfully.", 3000)
        if hasattr(self, 'btn_save'):
            original_text = self.btn_save.text()
            self.btn_save.setText("✅ Saved!")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.btn_save.setText(original_text))

    def _mark_current_dirty(self):
        if self.current_file_path:
            self.project_manager.mark_dirty(self.current_file_path)

    def _build_ocr_banner(self):
        # 🔥 FIX: Parent it directly to the PDF Viewer so it acts as a floating overlay!
        self.ocr_banner = QFrame(self.viewer)
        self.ocr_banner.setFixedHeight(45)

        # Give it a slight drop shadow and rounded edges
        self.ocr_banner.setObjectName("OCRBanner")

        banner_layout = QHBoxLayout(self.ocr_banner)
        banner_layout.setContentsMargins(15, 0, 15, 0)

        self.lbl_ocr_banner = QLabel("⚠️ Scanned document detected. Run OCR?")
        banner_layout.addWidget(self.lbl_ocr_banner)
        banner_layout.addStretch()

        btn_run = QPushButton("Run OCR")
        btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_run.clicked.connect(self._trigger_auto_ocr)
        banner_layout.addWidget(btn_run)

        btn_dismiss = QPushButton("✖")
        btn_dismiss.setFixedSize(24, 24)
        btn_dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_dismiss.clicked.connect(self.ocr_banner.hide)
        banner_layout.addWidget(btn_dismiss)

        self.ocr_banner.hide()

        # Create a dynamic hook so it stays centered at the top of the PDF Viewer when resized
        original_resize = self.viewer.resizeEvent
        def dynamic_resize(event):
            original_resize(event)
            banner_width = min(500, self.viewer.width() - 40)
            self.ocr_banner.setGeometry(
                (self.viewer.width() - banner_width) // 2,
                15, # 15px from the top of the viewer
                banner_width,
                45
            )
        self.viewer.resizeEvent = dynamic_resize
    def _build_workspace(self):
        # 1. Anchor: NEW Modular Document Explorer Dock
        self.doc_dock = QDockWidget("📁 Document Explorer", self)
        self.doc_dock.setObjectName("DocExplorerDock")
        self.doc_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)

        from gui.components.document_explorer import DocumentExplorer
        self.doc_explorer = DocumentExplorer(self)

        # We still need the Add PDF button at the bottom
        from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
        doc_container = QWidget()
        doc_layout = QVBoxLayout(doc_container)
        doc_layout.setContentsMargins(0, 0, 0, 0)
        doc_layout.setSpacing(0)
        doc_layout.addWidget(self.doc_explorer)

        self.btn_add_pdf_dock = QPushButton("➕ Add PDF to Project")
        # --- FIX: Stop calling self._add_pdf directly ---
        self.btn_add_pdf_dock.clicked.connect(
            lambda: self.bus.document_action_requested.emit(
                DocumentIntent.ADD_FILES,
                DocumentPayload(paths=QFileDialog.getOpenFileNames(self, "Add PDFs", "", "PDF Files (*.pdf)")[0])
            )
        )
        self.btn_add_pdf_dock.setStyleSheet("padding: 10px; font-weight: bold; border: none; border-top: 1px solid #444;")
        doc_layout.addWidget(self.btn_add_pdf_dock)

        self.doc_dock.setWidget(doc_container)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.doc_dock)

        # 2. Anchor: PDF Viewer Dock
        self.pdf_dock = QDockWidget("📄 PDF Viewer", self)
        self.pdf_dock.setObjectName("PDFViewerDock")
        self.pdf_dock.setWidget(self.viewer)
        self.pdf_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        title_bar = QWidget()
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)

        lbl_title = QLabel("📄 PDF Viewer")
        lbl_title.setStyleSheet("font-weight: bold; background: transparent;")
        title_layout.addWidget(lbl_title)

        title_layout.addStretch()

        btn_zoom_out = QPushButton("➖")
        btn_zoom_reset = QPushButton("Fit Width")
        btn_zoom_in = QPushButton("➕")
        btn_focus = QPushButton("🎯 Focus")
        btn_rotate = QPushButton("🔃 Rotate") # <-- NEW BUTTON

        btn_zoom_out.clicked.connect(self.viewer.zoom_out)
        btn_zoom_reset.clicked.connect(self.viewer.zoom_reset)
        btn_zoom_in.clicked.connect(self.viewer.zoom_in)
        btn_focus.clicked.connect(self.viewer.sharpen_focus)
        btn_rotate.clicked.connect(self.viewer.rotate_view) # <-- CONNECT NEW BUTTON

        btn_zoom_out.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_zoom_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_zoom_in.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_focus.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_rotate.setCursor(Qt.CursorShape.PointingHandCursor)

        header_btn_style = """
            QPushButton { background: transparent; border: none; padding: 4px 8px; }
            QPushButton:hover { background: rgba(128, 128, 128, 0.3); border-radius: 4px; }
        """
        for btn in [btn_zoom_out, btn_zoom_reset, btn_zoom_in, btn_focus, btn_rotate]:
            btn.setStyleSheet(header_btn_style)
            title_layout.addWidget(btn)

        title_layout.addStretch()
        self.pdf_dock.setTitleBarWidget(title_bar)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.pdf_dock)

        self.splitDockWidget(self.doc_dock, self.pdf_dock, Qt.Orientation.Horizontal)

    def closeEvent(self, event):
        """Intercepts the window closing to check for unsaved changes, save session state, and clean up threads."""
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import QSettings

        # 1. Check if there is anything actually waiting to be saved
        has_unsaved_changes = hasattr(self, 'project_manager') and bool(self.project_manager.dirty_docs)

        if has_unsaved_changes:
            # Pop up the native OS warning dialog
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes in your project. Do you want to save before exiting?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save # Default button
            )

            if reply == QMessageBox.StandardButton.Save:
                # Attempt to save the project
                if hasattr(self, 'bus'):
                    self.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload())
                    if hasattr(self, 'project_manager') and self.project_manager.dirty_docs:
                        self.project_manager.save_all_docs()
            elif reply == QMessageBox.StandardButton.Cancel:
                # User hit Cancel, abort the close sequence entirely!
                event.ignore()
                return
            # If Discard, we just skip the project save and proceed to shutdown.

        # 2. Explicitly save the last project path to the OS Registry so it re-opens on boot
        if hasattr(self, 'project_manager') and getattr(self.project_manager, 'project_filepath', None):
            settings = QSettings("PDFMultitool", "Workspace")
            settings.setValue("last_project_path", self.project_manager.project_filepath)
            settings.sync()

        # 3. Save the global UI layout session
        if hasattr(self, 'layout_manager'):
            self.layout_manager.save_current_session()

        # 4. Clean up background workers so the app doesn't leave ghost processes running in Task Manager
        if hasattr(self, 'autosave_timer') and self.autosave_timer.isActive():
            self.autosave_timer.stop()

        if hasattr(self, 'viewer') and hasattr(self.viewer, 'worker') and self.viewer.worker:
            self.viewer.worker._is_running = False
            self.viewer.worker.wait()

        if hasattr(self, 'quick_note_popup') and self.quick_note_popup:
            try:
                self.quick_note_popup.close()
            except RuntimeError:
                pass

        event.accept()
    def _trigger_auto_ocr(self):
        self.ocr_banner.hide()
        self.dock_manager.spawn("ocrs")
    def toggle_tool_panel(self, tool_name, checked):
        """Dynamically toggles tools without hardcoded mappings!"""
        # (Optional) Handle your left-side document list manually if it's not a registered plugin
        if tool_name == "Documents" and hasattr(self, 'doc_dock'):
            self.doc_dock.setVisible(checked)
            if checked: self.doc_dock.raise_()
            return

        # Handle all registered plugin docks
        for dock_id, defn in self.dock_manager.registry.items():
            if defn.menu_name == tool_name or defn.id == tool_name:
                if checked:
                    self.dock_manager.spawn(dock_id)
                else:
                    for dock in self.dock_manager.get_instances(dock_id):
                        dock.close()
                return
