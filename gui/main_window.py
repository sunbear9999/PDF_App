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
from gui.theme import ThemeManager
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
class ElidedLabel(QLabel):
    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        # ---> CRITICAL FIX: Drop this to 1 so it yields 100% of its space to the tag dots
        return QSize(1, super().minimumSizeHint().height())

    def sizeHint(self):
        from PySide6.QtCore import QSize
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + 10, super().sizeHint().height())

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter
        from PySide6.QtCore import Qt
        painter = QPainter(self)
        metrics = self.fontMetrics()
        elided = metrics.elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)

class PreloadWorker(QThread):
    def __init__(self, llm_manager, model, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.model = model

    def run(self):
        self.llm_manager.preload_model(self.model)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("PapyrusMainWindow")
        self.setWindowTitle("Papyrus - Ethical, Offline Research Assistant")
        self._apply_smart_window_size()
        self.setMinimumSize(800, 600)
        self.settings = QSettings("PDFMultitool", "Workspace")
        
        self.theme_manager = ThemeManager()
        self.project_manager = ProjectManager()
        self.project_manager.main_window = self
        self.current_file_path = None
        self.process_registry = ProcessRegistry()
        from core.llm_manager import LocalLLMManager
        self.shared_llm_manager = LocalLLMManager()
        self.shared_llm_manager.set_audit_logger(self.project_manager.log_ai_interaction_threadsafe)
        self.prompt_manager = PromptManager()
        
        # 1. INITIALIZE VIEWER EXPLICITLY ONCE
        def get_resource_path(relative_path):
            if hasattr(sys, '_MEIPASS'):
                # PyInstaller execution: use the temporary extracted folder
                return os.path.join(sys._MEIPASS, relative_path)
            # Normal script execution: use the project root
            root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            return os.path.join(root_dir, relative_path)
        self.viewer = PDFViewer()
        user_data_dir = os.path.join(os.path.expanduser("~"), ".papyrus_data")
        self.dictionary_manager = DictionaryManager(user_data_dir)
        if not self.dictionary_manager.get_available_dictionaries():
            default_dict_path = get_resource_path(os.path.join("assets", "default_english.json"))
            
            if os.path.exists(default_dict_path):
                print("[System] First launch detected. Building default dictionary...")
                self.dictionary_manager.import_json(default_dict_path, "Default English")
        # 2. CONNECT CRITICAL SAVING SIGNALS 
        # This guarantees the ProjectManager knows to save the file!
        self.citation_manager = CitationManager(self.project_manager)

        self.viewer.annotation_clicked.connect(self.broadcast_annotation_clicked)
        self.viewer.annot_manager.note_added.connect(self.broadcast_note_added)
        self.viewer.annot_manager.highlight_created.connect(self.broadcast_highlight_created)

        # 3. CONFIGURE DOCKS
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks | 
            QMainWindow.DockOption.AnimatedDocks | 
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.GroupedDragging
        )
        self.setDockNestingEnabled(True)
        self.blueprint_manager = BlueprintManager()
        # 4. SET CENTRAL WIDGET PROPERLY
        self.central_wrapper = QWidget()
        self.central_layout = QVBoxLayout(self.central_wrapper)
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.central_layout.setSpacing(0)
        self.setCentralWidget(self.central_wrapper)

        # 5. TRACK DOCKS
        self.workspace_docks = []
        self.notes_docks = []
        self.chat_docks = []
        self.scratchpad_docks = []
        self.ocr_docks = []      
        self.audio_docks = []
        self.research_docks = [] 
        self.dict_docks = []
        self.essay_docks = []
        self.citation_docks = [] 
        self.brainstorm_docks = []
        self.slideshow_docks = []
        # 6. BUILD UI
        self._build_top_menu()
        self._build_ocr_banner()
        self._build_workspace()
        self._setup_shortcuts()
        
        # Connect Theme Manager
        self.theme_manager.theme_changed.connect(self.update_theme)
        self.update_theme(self.theme_manager.get_theme()) 
        
        # Timers
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave_project)
        self.autosave_timer.start(5 * 60 * 1000) 
        
        QTimer.singleShot(1500, self._trigger_background_preload)
        if self.settings.value("show_help_on_startup", True, type=bool):
            QTimer.singleShot(500, self.show_help_window)
            
        # DELAY THE STARTUP: Wait 50ms for the 0x0 window to physically draw on screen
        # before attempting to calculate tabbed dock layouts.
        QTimer.singleShot(50, self._run_startup_sequence)
        self.universal_overlay = UniversalInternalOverlay(self, self.theme_manager.get_theme())
        self.ui_router = BlueprintUIRouter(self)   
    def execute_ai_blueprint(self, blueprint, initial_state):
        """
        The singular entry point for ALL AI operations in the app.
        Tabs just call this, and the UI Router handles the rest.
        """
        # Ensure database is mounted
        if self.shared_llm_manager.collection is None:
            self.project_manager._mount_project_database()

        runner = MasterActionRunner(self, blueprint, initial_state)
        self.ui_router.attach_runner(runner) # Router hooks into signals
        runner.start()
    def _run_startup_sequence(self):
        """Runs the project load/layout sequence safely after the UI is rendered."""
        last_project = self.settings.value("last_project", "")
        if last_project and os.path.exists(last_project):
            self._load_project(last_project)
        else:
            self._reset_default_layout()        
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

    
    def spawn_slideshow_dock(self):
        if not hasattr(self, 'slideshow_docks'):
            self.slideshow_docks = []

        idx = len(self.slideshow_docks) + 1
        dock = QDockWidget(f"📊 Slideshow Maker {idx}", self)
        dock.setObjectName(f"SlideshowDock_{idx}")

        # Depending on where SlideshowTab is placed. Assuming gui.docks.slideshow_dock
        from gui.docks.slideshow_dock import SlideshowTab
        view = SlideshowTab(self.project_manager, self)
        dock.setWidget(view)

        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.slideshow_docks.append(view)

        if hasattr(self, 'theme_manager'): 
            view.update_theme(self.theme_manager.get_theme())

        dock.show()
    def _sync_full_screen_button(self):
        if hasattr(self, "btn_fullscreen"):
            is_full = self.isFullScreen()
            icon = "🗗" if is_full else "⛶"
            label = "Exit Full Screen" if is_full else "Enter Full Screen"
            self.btn_fullscreen.setToolTip(label)
            self.btn_fullscreen.setProperty("compact_icon", icon)
            self.btn_fullscreen.setProperty("expanded_text", f"{icon} {label}")
            if not self.btn_fullscreen.property("hover_expanded"):
                self.btn_fullscreen.setText(icon)

    def _configure_hover_expand_button(self, button, icon, label, expanded_width=170, collapsed_width=44):
        button.setText(icon)
        button.setToolTip(label)
        button.setProperty("compact_icon", icon)
        button.setProperty("expanded_text", f"{icon} {label}")
        button.setProperty("collapsed_width", collapsed_width)
        button.setProperty("expanded_width", expanded_width)
        button.setProperty("hover_expanded", False)
        button.setMinimumWidth(collapsed_width)
        button.setMaximumWidth(collapsed_width)
        button.installEventFilter(self)

    def _set_button_hover_state(self, button, expanded):
        icon = button.property("compact_icon")
        expanded_text = button.property("expanded_text")
        collapsed_width = int(button.property("collapsed_width") or 44)
        expanded_width = int(button.property("expanded_width") or 170)

        if expanded:
            button.setText(expanded_text or icon)
            button.setMinimumWidth(expanded_width)
            button.setMaximumWidth(expanded_width)
            button.setProperty("hover_expanded", True)
        else:
            button.setText(icon)
            button.setMinimumWidth(collapsed_width)
            button.setMaximumWidth(collapsed_width)
            button.setProperty("hover_expanded", False)

    def eventFilter(self, watched, event):
        if isinstance(watched, QPushButton) and watched.property("compact_icon"):
            if event.type() == QEvent.Type.Enter:
                self._set_button_hover_state(watched, True)
            elif event.type() == QEvent.Type.Leave:
                self._set_button_hover_state(watched, False)
        return super().eventFilter(watched, event)

    def _trigger_background_preload(self):
        try:
            # --- THE PRE-WARM FIX ---
            # Boot the heavy graphics engine using a Page instead of a View.
            # This completely prevents the GUI flash without crashing the OS Compositor!
            from PySide6.QtWebEngineCore import QWebEnginePage
            self._dummy_browser = QWebEnginePage(self)

            if not hasattr(self, 'shared_llm_manager') or not self.shared_llm_manager.ai_enabled: 
                return
            
            default_model = self.chat_docks[0].model_combo.currentText() if self.chat_docks else "gemma4:e2b"
            self.preload_worker = PreloadWorker(self.shared_llm_manager, default_model, parent=self)
            self.preload_worker.start()
        except Exception as e:
            print(f"Could not trigger preload: {e}")

   

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_project)
        QShortcut(QKeySequence("F11"), self).activated.connect(self.toggle_full_screen)

    def _build_top_menu(self):
        from PySide6.QtWidgets import QToolBar, QWidget, QHBoxLayout, QSizePolicy, QMenu
        from PySide6.QtCore import Qt

        self.top_toolbar = QToolBar("Main Toolbar", self)
        self.top_toolbar.setObjectName("MainToolbar")
        self.top_toolbar.setMovable(False)
        self.top_toolbar.setFloatable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.top_toolbar)

        # 1. Feedback
        self.btn_feedback = QPushButton()
        self._configure_hover_expand_button(self.btn_feedback, "💬", "Feedback", expanded_width=110, collapsed_width=60)
        self.btn_feedback.clicked.connect(lambda: self._open_feedback_link())
        self.top_toolbar.addWidget(self.btn_feedback)

        # 2. Project Menu
        self.btn_project = QPushButton()
        self._configure_hover_expand_button(self.btn_project, "📁", "Project", expanded_width=100, collapsed_width=60)
        project_menu = QMenu(self)
        project_menu.addAction("New Project...", self._new_project)
        project_menu.addAction("Open Project...", self._open_project)
        project_menu.addAction("Save Project As...", self._save_project_as)
        project_menu.addSeparator()
        project_menu.addAction("Add PDF to Project...", self._add_pdf)
        export_action = project_menu.addAction("🛡️ Export LLM Log...")
        export_action.triggered.connect(self._export_llm_log)
        self.btn_project.setMenu(project_menu)
        self.top_toolbar.addWidget(self.btn_project)

        # 3. Save Button
        self.btn_save = QPushButton("💾")
        self.btn_save.clicked.connect(self.save_project)
        self.top_toolbar.addWidget(self.btn_save)

        # Spacer to push core tools to the center
        spacer1 = QWidget()
        spacer1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.top_toolbar.addWidget(spacer1)

        # 4. Core Spawners (Always visible)
        self.btn_spawn_ws = QPushButton("➕ Workspace")
        self.btn_spawn_ws.clicked.connect(self.spawn_workspace_dock)
        self.top_toolbar.addWidget(self.btn_spawn_ws)

       

        self.btn_spawn_research = QPushButton("🔬 Research Assistant")
        self.btn_spawn_research.clicked.connect(self.spawn_research_dock)
        self.top_toolbar.addWidget(self.btn_spawn_research)



        self.btn_spawn_essay = QPushButton("📝 Writing")
        self.btn_spawn_essay.clicked.connect(self.spawn_essay_dock)
        self.top_toolbar.addWidget(self.btn_spawn_essay)
        self.btn_spawn_slideshow = QPushButton("📊 Slideshow Maker")
        self.btn_spawn_slideshow.clicked.connect(self.spawn_slideshow_dock)
        self.top_toolbar.addWidget(self.btn_spawn_slideshow)
        # 5. Dropdown Spawner (Other Tools)
        self.btn_other_tools = QPushButton("➕ Other Tools")
        other_tools_menu = QMenu(self)
        
        action_notes = other_tools_menu.addAction("📝 Notes List")
        action_notes.triggered.connect(self.spawn_notes_dock)

        action_scratch = other_tools_menu.addAction("✍️ Scratchpad")
        action_scratch.triggered.connect(self.spawn_scratchpad_dock)
        
        action_dict = other_tools_menu.addAction("📖 Dictionary")
        action_dict.triggered.connect(self.spawn_dictionary_dock)
        
        action_cite = other_tools_menu.addAction("📚 Citations")
        # Note: Make sure self.spawn_citation_dock is still defined in main_window.py!
        action_cite.triggered.connect(self.spawn_citation_dock) 
        
        action_ocr = other_tools_menu.addAction("👁️ OCR Scanner")
        action_ocr.triggered.connect(self.spawn_ocr_dock)
        
        action_audio = other_tools_menu.addAction("🔊 Audio (TTS)")
        action_audio.triggered.connect(self.spawn_audio_dock)
        
        self.btn_other_tools.setMenu(other_tools_menu)
        self.top_toolbar.addWidget(self.btn_other_tools)

        # Spacer to push settings to the right
        spacer2 = QWidget()
        spacer2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.top_toolbar.addWidget(spacer2)

        # 6. Right-side Tools
        self.process_monitor = ProcessMonitorWidget(self.process_registry)
        self._configure_hover_expand_button(self.process_monitor, "🟢", "Process Monitor", expanded_width=150, collapsed_width=44)
        self.process_monitor.setMaximumHeight(30) # Keep it compact
        self.top_toolbar.addWidget(self.process_monitor)

        self.btn_tag_manager = QPushButton()
        self._configure_hover_expand_button(self.btn_tag_manager, "🏷️", "Tag Manager", expanded_width=130, collapsed_width=60)
        self.btn_tag_manager.clicked.connect(self._open_tag_manager)
        self.top_toolbar.addWidget(self.btn_tag_manager)

        self.btn_prompt_editor = QPushButton()
        self._configure_hover_expand_button(self.btn_prompt_editor, "🧠", "Prompt Editor", expanded_width=140)
        self.btn_prompt_editor.clicked.connect(self._open_prompt_editor)
        self.top_toolbar.addWidget(self.btn_prompt_editor)

        self.btn_layouts = QPushButton()
        self._configure_hover_expand_button(self.btn_layouts, "🗔", "Window Layouts", expanded_width=160, collapsed_width=65)
        layout_menu = QMenu(self)
        layout_menu.addAction("⭐ Set Current as Default Layout", self._save_as_default_layout)
        layout_menu.addAction("💾 Save as Custom Template...", self._save_layout_template)
        self.custom_layouts_menu = layout_menu.addMenu("📁 Load Custom Template")
        self.delete_layouts_menu = layout_menu.addMenu("🗑️ Delete Custom Template")
        layout_menu.addAction("🔄 Reset to Default Sane Layout", self._reset_default_layout)
        self.btn_layouts.setMenu(layout_menu)
        self.top_toolbar.addWidget(self.btn_layouts)
        self._refresh_layout_templates_menu()

        # Theme Selector
        theme_widget = QWidget()
        theme_layout = QHBoxLayout(theme_widget)
        theme_layout.setContentsMargins(5, 0, 5, 0)
        theme_layout.addWidget(QLabel("Theme:"))
        self.theme_selector = QComboBox()
        self.theme_selector.addItems(self.theme_manager.themes.keys())
        self.theme_selector.setCurrentText(self.theme_manager.current_theme_name)
        self.theme_selector.currentTextChanged.connect(self._on_theme_changed)
        theme_layout.addWidget(self.theme_selector)
        self.top_toolbar.addWidget(theme_widget)

        self.btn_fullscreen = QPushButton()
        self._configure_hover_expand_button(self.btn_fullscreen, "⛶", "Full Screen", expanded_width=120)
        self.btn_fullscreen.clicked.connect(self.toggle_full_screen)
        self.top_toolbar.addWidget(self.btn_fullscreen)

    

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
            self._set_button_hover_state(self.btn_fullscreen, self.btn_fullscreen.property("hover_expanded"))

    def spawn_essay_dock(self):
        if not hasattr(self, 'essay_docks'):
            self.essay_docks = []
            
        idx = len(self.essay_docks) + 1
        dock = QDockWidget(f"📝 Essay Writer {idx}", self)
        dock.setObjectName(f"EssayDock_{idx}")
        
        essay_view = EssayTab(self.project_manager, self)
        dock.setWidget(essay_view)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.essay_docks.append(essay_view)
        
        if hasattr(self, 'theme_manager'): 
            essay_view.update_theme(self.theme_manager.get_theme())
            
        dock.show()
    def spawn_dictionary_dock(self):
        # STRICT SINGLETON: Only one dictionary dock needed
        if self.dict_docks:
            view = self.dict_docks[0]
            if view.parentWidget():
                view.parentWidget().show()
                view.parentWidget().raise_()
            return

        dock = QDockWidget("📖 Dictionary", self)
        dock.setObjectName("SingleDictionaryDock")
        
        from gui.docks.dictionary_dock import DictionaryTab
        dict_view = DictionaryTab(self.dictionary_manager,self)
        dock.setWidget(dict_view)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.dict_docks.append(dict_view)
        
        if hasattr(self, 'theme_manager'):
            dict_view.update_theme(self.theme_manager.get_theme())
            
        dock.show()

    def spawn_workspace_dock(self):
        # 1. Revive a hidden dock if one exists!
        for view in self.workspace_docks:
            if view.parentWidget() and not view.parentWidget().isVisible():
                view.parentWidget().show()
                view.parentWidget().raise_()
                return
                
        # 2. Ditch the endless counter. Use deterministic ID based on list length.
        idx = len(self.workspace_docks) + 1
        dock = QDockWidget(f"🧠 Workspace {idx}", self)
        dock.setObjectName(f"WorkspaceDock_{idx}") 
        
        ws_view = WorkspaceView(self)
        dock.setWidget(ws_view)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.workspace_docks.append(ws_view)
        
        if hasattr(self, 'theme_manager'): 
            ws_view.update_theme(self.theme_manager.get_theme())
        ws_view._sync_workspace()
        dock.show()
    def spawn_research_dock(self):
        # STRICT SINGLETON: We only need one of these
        if self.research_docks:
            view = self.research_docks[0]
            if view.parentWidget():
                view.parentWidget().show()
                view.parentWidget().raise_()
            return
                
        # Import the NEW Unified Dock
        from gui.docks.unified_research.unified_dock import UnifiedResearchDock
        
        # The UnifiedResearchDock inherits directly from QDockWidget, 
        # so we don't need to create an empty QDockWidget wrapper.
        dock = UnifiedResearchDock(self, self.project_manager, self.shared_llm_manager, self)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.research_docks.append(dock) # Track it so the singleton logic works
        
        if hasattr(self, 'theme_manager'): 
            dock.update_theme(self.theme_manager.get_theme())
            
        dock.show()



    
    def spawn_notes_dock(self):
        for view in self.notes_docks:
            if view.parentWidget() and not view.parentWidget().isVisible():
                view.parentWidget().show()
                view.parentWidget().raise_()
                return
                
        idx = len(self.notes_docks) + 1
        dock = QDockWidget(f"📝 Notes List {idx}", self)
        dock.setObjectName(f"NotesDock_{idx}")
        
        notes_view = NotesTab(None, self.viewer, self)
        dock.setWidget(notes_view)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.notes_docks.append(notes_view)
        
        if hasattr(self, 'theme_manager'): 
            notes_view.update_theme(self.theme_manager.get_theme())
        notes_view.refresh_notes()
        dock.show()
    def _export_llm_log(self):
        if not self.project_manager.project_filepath:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "No Project", "Please open or create a project first.")
            return
            
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        
        # Change dialog to look for .pdf files
        path, _ = QFileDialog.getSaveFileName(
            self, 
            "Export LLM Log", 
            f"{self.project_manager.project_name}_LLM_Log.pdf", 
            "PDF Documents (*.pdf)"
        )
        
        if path:
            # Import and trigger the new PDF generator
            from core.llm_log import LlmLogGenerator
            
            generator = LlmLogGenerator(
                self.project_manager.project_filepath, 
                self.project_manager.project_name
            )
            
            success = generator.generate_pdf(path)
            
            if success:
                QMessageBox.information(self, "Success", f"LLM Log successfully exported to:\n{path}")
            else:
                QMessageBox.warning(self, "Error", "Failed to generate the report. Check the console for details.")
    

    def spawn_citation_dock(self):
        if self.citation_docks:
            view = self.citation_docks[0]
            if view.parentWidget():
                view.parentWidget().show()
                view.parentWidget().raise_()
            return

        dock = QDockWidget("📚 Citation Manager", self)
        dock.setObjectName("SingleCitationDock")

        from gui.docks.citation_dock import CitationDock
        cite_view = CitationDock(self.citation_manager, self.project_manager, self)
        dock.setWidget(cite_view)

        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.citation_docks.append(cite_view)
        
        # ---> FIX: Apply theme instantly on spawn <---
        if hasattr(self, 'theme_manager'): 
            cite_view.update_theme(self.theme_manager.get_theme())
            
        dock.show()

    def spawn_ocr_dock(self):
        # STRICT SINGLETON
        if self.ocr_docks:
            view = self.ocr_docks[0]
            if view.parentWidget():
                view.parentWidget().show(); view.parentWidget().raise_(); return

        dock = QDockWidget("👁️ OCR Scanner", self)
        dock.setObjectName("SingleOCRDock")
        from gui.docks.ocr_dock import OCRTab
        view = OCRTab(None, self)
        dock.setWidget(view)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.ocr_docks.append(view)
        
        # ---> FIX: Apply theme instantly on spawn <---
        if hasattr(self, 'theme_manager'): 
            view.update_theme(self.theme_manager.get_theme())
            
        dock.show()

    def spawn_audio_dock(self):
        # STRICT SINGLETON
        if self.audio_docks:
            view = self.audio_docks[0]
            if view.parentWidget():
                view.parentWidget().show(); view.parentWidget().raise_(); return

        dock = QDockWidget("🔊 Audio (TTS)", self)
        dock.setObjectName("SingleAudioDock")
        from gui.docks.tts_dock import TTSTab
        view = TTSTab(None, self)
        dock.setWidget(view)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.audio_docks.append(view)
        
        # ---> FIX: Apply theme instantly on spawn <---
        if hasattr(self, 'theme_manager'): 
            view.update_theme(self.theme_manager.get_theme())
            
        dock.show()
        
    def spawn_scratchpad_dock(self):
        for view in self.scratchpad_docks:
            if view.parentWidget() and not view.parentWidget().isVisible():
                view.parentWidget().show()
                view.parentWidget().raise_()
                return
                
        idx = len(self.scratchpad_docks) + 1
        dock = QDockWidget(f"✍️ Scratchpad {idx}", self)
        dock.setObjectName(f"ScratchDock_{idx}")
        
        editor = QTextEdit()
        editor.setPlaceholderText("Jot down quick thoughts here...\n\n(Stays perfectly saved in memory until you load a new project)")
        dock.setWidget(editor)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.scratchpad_docks.append(editor)
        
        if hasattr(self, 'theme_manager'):
            theme = self.theme_manager.get_theme()
            editor.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: none;")
        dock.show()
    def _open_feedback_link(self):
        import webbrowser
        webbrowser.open("https://docs.google.com/forms/d/e/1FAIpQLSfm3W0Z-79jSJ1uuUgiUoi2CXMkyxLM3S3jyEw931aIDNDFag/viewform?usp=publish-editor")

    def _open_prompt_editor(self):
        prompt_manager = self.shared_llm_manager.prompt_manager if hasattr(self, 'shared_llm_manager') else PromptManager()
        dialog = PromptEditorDialog(prompt_manager, self)
        dialog.exec()

    def _open_tag_manager(self):
        dialog = TagManagerDialog(self.project_manager, self)
        dialog.exec()
        for c in self.chat_docks: c.refresh_tag_filters()
        self._refresh_doc_tag_filter()

    def _on_doc_list_context_menu(self, pos):
        item_at_pos = self.doc_list.itemAt(pos)
        if not item_at_pos: return
        
        selected_items = self.doc_list.selectedItems()
        
        # Standard OS behavior: if you right-click an item that ISN'T in your current selection,
        # it clears the selection and selects only the item you right-clicked.
        if item_at_pos not in selected_items:
            self.doc_list.clearSelection()
            item_at_pos.setSelected(True)
            selected_items = [item_at_pos]

        menu = QMenu(self)
        
        # ==========================================
        # BATCH MODE (Multiple Documents Selected)
        # ==========================================
        if len(selected_items) > 1:
            mass_tag_menu = menu.addMenu(f"🏷️ Mass Assign Tag to {len(selected_items)} Docs")
            tags = self.project_manager.get_all_tags()
            
            if not tags:
                mass_tag_menu.addAction("No tags created yet").setEnabled(False)
            else:
                for t in tags:
                    action = mass_tag_menu.addAction(t.get("name"))
                    # Use a lambda with a default arg to lock in the tag ID for this loop iteration
                    action.triggered.connect(lambda checked=False, t_id=t.get("id"): self._mass_assign_tag(selected_items, t_id))
            
            # (Optional) You could add a mass-remove action here later if you want
            menu.exec(self.doc_list.viewport().mapToGlobal(pos))
            
        # ==========================================
        # SINGLE MODE (One Document Selected)
        # ==========================================
        else:
            doc_path = item_at_pos.data(Qt.ItemDataRole.UserRole)
            row = self.doc_list.row(item_at_pos)
            
            manage_tags_action = menu.addAction("🏷️ Manage Tags for This Document")
            menu.addSeparator()
            rename_action = menu.addAction("✏️ Rename PDF")
            remove_action = menu.addAction("🗑️ Remove PDF from Project")
            extract_action = menu.addAction("✂️ Extract Pages to New PDF")
            
            chosen = menu.exec(self.doc_list.viewport().mapToGlobal(pos))
            
            if chosen == manage_tags_action:
                from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
                dlg = TagAssignmentDialog(self.project_manager, doc_path, "doc", self)
                dlg.exec()
                for c in getattr(self, 'chat_docks', []): c.refresh_tag_filters()
                self._refresh_doc_list()
                self._refresh_doc_tag_filter()
                
            elif chosen == rename_action:
                self._ui_rename_pdf(doc_path, row)
            elif chosen == remove_action:
                self._ui_remove_pdf(doc_path, row)
            elif chosen == extract_action:
                dialog = ExtractPagesDialog(doc_path, self.project_manager, self)
                if dialog.exec():
                    self._refresh_doc_list()
    def _mass_assign_tag(self, selected_items, tag_id):
        """Loops through selected documents and applies the given tag ID to all of them."""
        for item in selected_items:
            doc_path = item.data(Qt.ItemDataRole.UserRole)
            self.project_manager.assign_tag_to_doc(doc_path, tag_id)
        
        # Refresh the LLM chat filters if they are open
        for c in getattr(self, 'chat_docks', []): 
            c.refresh_tag_filters()
            
        # Refresh the document list to show the new colored dots
        self._refresh_doc_list()
    def _ui_rename_pdf(self, old_path, row):
        # 1. CRITICAL: Force save all current highlights/nodes to disk and DB BEFORE renaming
        # This guarantees physical highlights are baked into the PDF before the OS touches it.
        self.save_project()
        
        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(self, "Rename PDF", "Enter new name for the PDF:", text=old_name)
        
        if not ok or not new_name.strip() or new_name == old_name:
            return
            
        if not new_name.lower().endswith(".pdf"):
            new_name += ".pdf"
            
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        
        if os.path.exists(new_path):
            QMessageBox.warning(self, "Error", "A file with that name already exists in this folder.")
            return
            
        # 2. Perform the rename via ProjectManager (handles DB migration and file locking)
        success = self.project_manager.rename_pdf(old_path, new_path)
        
        if success:
            # 3. Update the Document Explorer Dock cleanly
            self._refresh_doc_list()
            
            # 4. Update the LLM Chat Docks & ChromaDB
            for c_dock in getattr(self, 'chat_docks', []):
                c_dock.refresh_project_ui()
                
            try:
                if hasattr(self, 'shared_llm_manager'):
                    self.shared_llm_manager.rename_document_in_index(old_path, new_path)
            except Exception as e:
                print(f"Failed to tell ChromaDB about rename: {e}")
                
            # 5. Live-update nodes in the workspaces so the "Filter by PDF" doesn't break
            for ws_view in getattr(self, 'workspace_docks', []):
                for node in ws_view.nodes.values():
                    if getattr(node, 'pdf_path', None) == old_path:
                        node.pdf_path = new_path
                
                # Re-populate the filter dropdown with the new name
                if hasattr(ws_view, '_refresh_pdf_list'):
                    ws_view._refresh_pdf_list()
            
            # 6. Re-load the PDF in the viewer if it was the active one
            if self.current_file_path == old_path or self.current_file_path == new_path:
                self.current_file_path = None # force reload
                self.switch_to_pdf(new_path)

    def _ui_remove_pdf(self, doc_path, row):
        reply = QMessageBox.question(self, "Remove PDF", 
                                     f"Are you sure you want to remove '{os.path.basename(doc_path)}' from the project?\n\nThis will delete all highlights and nodes associated with it from the database.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
            
        # 1. Perform the removal via ProjectManager
        success = self.project_manager.remove_pdf(doc_path)
        
        if success:
            # 2. Update the Document Explorer Dock
            self.doc_list.takeItem(row)
            
            # 3. Update LLM Chat & ChromaDB
            for c_dock in getattr(self, 'chat_docks', []):
                c_dock.refresh_project_ui()
                
            try:
                if hasattr(self, 'shared_llm_manager'):
                    self.shared_llm_manager.remove_document_from_index(doc_path)
            except Exception as e:
                print(f"Failed to tell ChromaDB about removal: {e}")
                    
            # 4. Clean up workspace nodes visually
            for ws_view in getattr(self, 'workspace_docks', []):
                nodes_to_delete = [n for n in ws_view.nodes.values() if getattr(n, 'pdf_path', None) == doc_path]
                for node in nodes_to_delete:
                    ws_view.delete_node(node)
                if hasattr(ws_view, '_refresh_pdf_list'):
                    ws_view._refresh_pdf_list()
                    
            # 5. Handle viewer state
            if self.current_file_path == doc_path:
                self.current_file_path = None
                if hasattr(self.viewer, 'scene') and self.viewer.scene:
                    self.viewer.scene.clear()
                if hasattr(self.viewer, 'doc'):
                    self.viewer.doc = None
                
                # Switch to the first available PDF, if any
                if self.doc_list.count() > 0:
                    item = self.doc_list.item(0)
                    self.switch_to_pdf(item.data(Qt.ItemDataRole.UserRole))

    def _on_theme_changed(self, theme_name):
        if theme_name == "Custom":
            self.theme_manager.edit_custom_theme(self)
            
        self.settings.setValue("theme", theme_name)
        self.theme_manager.set_theme(theme_name)

    def update_theme(self, theme):
        self.top_toolbar.setStyleSheet(f"background-color: {theme['bg_panel']}; border-bottom: 1px solid {theme['border']};")
        self.ocr_banner.setStyleSheet(f"background-color: {theme['warning']}; border-bottom: 1px solid {theme['border']};")
        self.lbl_ocr_banner.setStyleSheet(f"font-weight: bold; color: #1e1e1e; border: none;") 

        # 1. BROADCAST TO ALL LIVE DOCKS (Added the missing ones!)
        for ws in self.workspace_docks: ws.update_theme(theme)
        for n in self.notes_docks: n.update_theme(theme)
        for c in getattr(self, 'chat_docks', []): c.update_theme(theme)
        for b in getattr(self, 'brainstorm_docks', []): b.update_theme(theme)
        for r in getattr(self, 'research_docks', []): r.update_theme(theme)
        for d in getattr(self, 'dict_docks', []): d.update_theme(theme)
        for e in getattr(self, 'essay_docks', []): e.update_theme(theme)
        for o in getattr(self, 'ocr_docks', []): o.update_theme(theme)
        for a in getattr(self, 'audio_docks', []): a.update_theme(theme)
        for c in getattr(self, 'citation_docks', []): c.update_theme(theme)
        for s in getattr(self, 'slideshow_docks', []): s.update_theme(theme)
        # 2. Fix Scratchpads explicitly since they are raw QTextEdits
        for s in getattr(self, 'scratchpad_docks', []):
            s.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: none;")

        if hasattr(self.viewer, "update_theme"):
            self.viewer.update_theme(theme)

        svg_color = theme['text_main'].replace('#', '%23')

        global_style = f"""
        /* --- 1. Fix the Separator Hitbox & Double Borders --- */
        QMainWindow::separator {{
            background: {theme['border']};
            width: 5px;  /* Wider hitbox for vertical resizing */
            height: 5px; /* Taller hitbox for horizontal resizing */
        }}
        QMainWindow::separator:hover {{
            background: #b366ff; /* Purple highlight when grabbed */
        }}
        
        QDockWidget {{
            border: none; /* Kill the double-border gaps */
            color: {theme['text_main']};
        }}

        /* --- 2. Fix Dock Title Bar & Button Alignment --- */
        QDockWidget::title {{
            background: {theme['bg_panel']};
            padding: 6px 8px;
            min-height: 18px; /* Force consistent title bar height */
        }}

        QDockWidget::close-button, QDockWidget::float-button {{
            background: transparent;
            border: none;
            width: 22px; 
            height: 22px;
            /* Lock buttons to the vertical center */
            subcontrol-origin: padding;
            subcontrol-position: center right; 
        }}

        QDockWidget::close-button {{
            right: 4px; /* Space from the far right edge */
        }}

        QDockWidget::float-button {{
            right: 30px; /* Push it left, past the close button */
        }}

        QDockWidget::close-button:hover, QDockWidget::float-button:hover {{
            background: rgba(128, 128, 128, 0.2);
            border-radius: 4px;
        }}

        /* Scale the SVGs to fit perfectly */
        QDockWidget::close-button {{
            image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='{svg_color}' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><line x1='18' y1='6' x2='6' y2='18'></line><line x1='6' y1='6' x2='18' y2='18'></line></svg>");
        }}
        QDockWidget::float-button {{
            image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='{svg_color}' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='4' y='4' width='16' height='16' rx='2' ry='2'></rect></svg>");
        }}

        /* --- 3. Nuke ALL Scrollbars Globally --- */
        QScrollBar:vertical {{
            border: none;
            background: {theme['bg_panel']};
            width: 14px; /* Slightly thicker for usability */
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {theme['border']};
            min-height: 30px;
            border-radius: 6px;
            margin: 2px; /* Creates a floating pill effect */
        }}
        QScrollBar::handle:vertical:hover {{
            background: {theme.get('text_dim', '#888888')};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px; 
            border: none;
            background: none;
        }}
        /* THIS FIXES THE WHITE ARTIFACTS */
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: none; 
        }}

        /* Horizontal Scrollbars */
        QScrollBar:horizontal {{
            border: none;
            background: {theme['bg_panel']};
            height: 14px;
            margin: 0px;
        }}
        QScrollBar::handle:horizontal {{
            background: {theme['border']};
            min-width: 30px;
            border-radius: 6px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {theme.get('text_dim', '#888888')};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
            border: none;
            background: none;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: none;
        }}

        /* --- 4. Smooth out the Dock Tabs --- */
        QTabBar::tab {{
            background: {theme['bg_panel']};
            color: #888888;
            padding: 6px 12px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            border-bottom: 1px solid {theme['border']};
        }}
        QTabBar::tab:selected {{
            background: {theme.get('bg_base', '#1e1e1e')};
            color: {theme['text_main']};
            border-bottom: 2px solid #b366ff; 
        }}
        """
        self.setStyleSheet(global_style)

    def _clear_ui_for_new_project(self):
        self.current_file_path = None
        
        if hasattr(self, 'doc_list'):
            self.doc_list.blockSignals(True)
            self.doc_list.clear()
            self.doc_list.blockSignals(False)
        
        if hasattr(self.viewer, 'scene') and self.viewer.scene:
            self.viewer.scene.clear()
        if hasattr(self.viewer, 'doc'):
            self.viewer.doc = None
            
        # Clean up all dynamic docks cleanly
        for lst in [self.workspace_docks, self.notes_docks, self.chat_docks, 
                    self.scratchpad_docks, self.ocr_docks, self.audio_docks,self.brainstorm_docks,self.slideshow_docks]:
            for item in list(lst):
                if item.parentWidget(): 
                    item.parentWidget().deleteLater()
            lst.clear()
            
        # Reset counters
        self.ws_counter = self.notes_counter = self.chat_counter = self.scratch_counter = self.ocr_counter = self.audio_counter = 0

    def _new_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Create New Project", "", "PDF Project (*.pdfproj)")
        if path:
            if not path.lower().endswith(".pdfproj"):
                path += ".pdfproj"
                
            if self.project_manager.project_filepath:
                self.save_project()
                
            self._clear_ui_for_new_project()
            self.project_manager.create_project(path)
            self.settings.setValue("last_project", self.project_manager.project_filepath)
            
            # Purge the old tag drop downs and redraw the document list!
            self._refresh_doc_tag_filter()
            self._refresh_doc_list()
            
            self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")
            QTimer.singleShot(50, self._reset_default_layout)
            

    def _load_project(self, path):
        # 1. Save old project before switching
        if self.project_manager.project_filepath:
            self.save_project()
            
        # 2. Load the new project
        if self.project_manager.load_project(path):
            
            # 3. CRITICAL FIX: Link the ChromaDB index AFTER the new path is loaded!
            if hasattr(self, 'shared_llm_manager'):
                self.shared_llm_manager.set_project_database(self.project_manager.project_filepath)
                
            self._clear_ui_for_new_project()
            self.settings.setValue("last_project", self.project_manager.project_filepath)
            self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")
            self._refresh_doc_list()
            
           # --- Restore Saved Layouts ---
            state_str = self.project_manager.get_metadata("window_layout_state")
            dock_info = self.project_manager.get_metadata("open_docks_count")
            
            # Create a delayed function to apply the UI *after* zombies are cleared
            def apply_project_ui():
                # --- PREVENT UI LAG / FLASHING ---
                self.setUpdatesEnabled(False)
                
                try:
                    if state_str and dock_info:
                        import json
                        try:
                            counts = json.loads(dock_info)
                            self._sync_dock_counts(counts)
                        except Exception as e:
                            print(f"Error loading project docks: {e}")
                            
                        from PySide6.QtCore import QByteArray
                        self.restoreState(QByteArray.fromBase64(state_str.encode('utf-8')))
                    else:
                        self._reset_default_layout()

                    # --- Restore Scratchpad Texts ---
                    text_data = self.project_manager.get_metadata("scratchpad_texts")
                    if text_data:
                        import json
                        try:
                            saved_texts = json.loads(text_data)
                            for i, editor in enumerate(self.scratchpad_docks):
                                if i < len(saved_texts):
                                    editor.setPlainText(saved_texts[i])
                        except Exception as e:
                            print(f"Error loading scratchpad text: {e}")

                    # Force visibility and updates
                    from PySide6.QtWidgets import QDockWidget
                    for dock in self.findChildren(QDockWidget):
                        dock.show()
                    
                    for c in self.chat_docks: c.refresh_project_ui()
                    
                    if self.project_manager.pdfs:
                        self.switch_to_pdf(self.project_manager.pdfs[0])
                        
                finally:
                    # --- RESUME UI DRAWING ALL AT ONCE ---
                    self.setUpdatesEnabled(True)
    def _save_as_default_layout(self):
        import json
        state_bytes = self.saveState().toBase64().data().decode('utf-8')
        counts = {
            "workspaces": self._get_visible_count(self.workspace_docks),
            "notes": self._get_visible_count(self.notes_docks),
            "chats": self._get_visible_count(self.chat_docks),
            "scratchpads": self._get_visible_count(self.scratchpad_docks),
            "ocrs": self._get_visible_count(self.ocr_docks),
            "audios": self._get_visible_count(self.audio_docks),
            "essays": self._get_visible_count(getattr(self, 'essay_docks', [])),
            "dicts": self._get_visible_count(getattr(self, 'dict_docks', [])),
            "research": self._get_visible_count(getattr(self, 'research_docks', [])),
            "brainstorm": self._get_visible_count(getattr(self, 'brainstorm_docks', []))
        }
        self.settings.setValue("default_startup_layout", state_bytes)
        self.settings.setValue("default_startup_counts", json.dumps(counts))
        self.settings.sync()
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import Qt
        msg = QMessageBox(self)
        msg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("Default Set")
        msg.setText("This layout is now your permanent default!")
        msg.exec()

    def _save_layout_template(self):
        import json
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        from PySide6.QtCore import Qt
        
        name, ok = QInputDialog.getText(
            self, "Save Layout Template", "Enter a name for this layout:",
            flags=Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        if ok and name.strip():
            state_bytes = self.saveState().toBase64().data().decode('utf-8')
            counts = {
                "workspaces": self._get_visible_count(self.workspace_docks),
                "notes": self._get_visible_count(self.notes_docks),
                "chats": self._get_visible_count(self.chat_docks),
                "scratchpads": self._get_visible_count(self.scratchpad_docks),
                "ocrs": self._get_visible_count(self.ocr_docks),
                "audios": self._get_visible_count(self.audio_docks),
                "essays": self._get_visible_count(getattr(self, 'essay_docks', [])),
                "dicts": self._get_visible_count(getattr(self, 'dict_docks', [])),
                "research": self._get_visible_count(getattr(self, 'research_docks', [])),
                "brainstorm": self._get_visible_count(getattr(self, 'brainstorm_docks', []))
            }
            payload = json.dumps({"state": state_bytes, "counts": counts})
            self.settings.setValue(f"layouts/{name.strip()}", payload)
            self.settings.sync()
            self._refresh_layout_templates_menu()
            
            msg = QMessageBox(self)
            msg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle("Saved")
            msg.setText(f"Layout '{name}' saved successfully!")
            msg.exec()

    def _reset_default_layout(self):
        import json
        from PySide6.QtCore import QByteArray
        from PySide6.QtWidgets import QDockWidget

        default_state = str(self.settings.value("default_startup_layout", ""))
        counts_str = str(self.settings.value("default_startup_counts", ""))

        # WIRING POINT 3: The Gatekeeper
        if not default_state or not counts_str or default_state == "None" or counts_str == "None":
            self._apply_factory_default()
            return

        counts = {}
        try:
            counts = json.loads(counts_str)
        except Exception as e:
            print(f"Error parsing layout counts: {e}")
            self._apply_factory_default()
            return

        self.setUpdatesEnabled(False) # <--- STOP DRAWING
        try:
            self._sync_dock_counts(counts)
            self.restoreState(QByteArray.fromBase64(default_state.encode('utf-8')))

            for dock in self.findChildren(QDockWidget): 
                dock.show()
        finally:
            self.setUpdatesEnabled(True) # <--- RESUME DRAWING
    def _apply_factory_default(self):
        """The hardcoded layout for fresh installs or before a user sets a custom default."""
        from PySide6.QtCore import QByteArray
        
        # 1. Paste the exact dictionary your console printed out here:
        hardcoded_counts = {'workspaces': 1, 'notes': 0, 'chats': 1, 'scratchpads': 1, 'ocrs': 0, 'audios': 0, 'essays': 1, 'dicts': 0, 'research': 1, 'brainstorm': 1}
        
        # 2. Paste the massive Base64 string your console printed out here:
        hardcoded_state = "AAAA/wAAAAD9AAAAAgAAAAAAAAQRAAAD+fwCAAAAAfwAAAApAAAD+QAAAREA/////AEAAAAD/AAAAAAAAADIAAAAnAD////8AgAAAAT7AAAAHgBEAG8AYwBFAHgAcABsAG8AcgBlAHIARABvAGMAawEAAAApAAADMAAAAKoA////+wAAABoAUwBjAHIAYQB0AGMAaABEAG8AYwBrAF8AMQEAAANfAAAAwwAAAGEA////+wAAABoAUwBjAHIAYQB0AGMAaABEAG8AYwBrAF8AMQEAAALfAAABQwAAAAAAAAAA+wAAABQAQwBoAGEAdABEAG8AYwBrAF8AMQEAAADgAAADWAAAAAAAAAAA/AAAAM4AAANDAAAB5AD////6AAAAAQIAAAAC+wAAABYARQBzAHMAYQB5AEQAbwBjAGsAXwAxAQAAAAD/////AAAAWAD////7AAAAGgBQAEQARgBWAGkAZQB3AGUAcgBEAG8AYwBrAQAAACkAAAQPAAAAXQD////8AAADSwAAAT0AAAAAAP////wCAAAAAfsAAAAeAFcAbwByAGsAcwBwAGEAYwBlAEQAbwBjAGsAXwAyAQAAAjIAAAIGAAAAAAAAAAAAAAABAAADYwAAA/n8AgAAAAj7AAAAFgBOAG8AdABlAHMARABvAGMAawBfADICAAAGEwAAABkAAAFkAAAD9vsAAAAeAFMAaQBuAGcAbABlAEEAdQBkAGkAbwBEAG8AYwBrAAAAAdQAAAJkAAAAAAAAAAD7AAAAGgBTAGMAcgBhAHQAYwBoAEQAbwBjAGsAXwAyAAAAA7MAAACFAAAAAAAAAAD7AAAAFgBOAG8AdABlAHMARABvAGMAawBfADEBAAAAKQAAAIcAAAAAAAAAAPsAAAAWAE4AbwB0AGUAcwBEAG8AYwBrAF8AMQAAAAApAAAEDwAAAAAAAAAA/AAAACkAAAP5AAABmAEAACH6AAAAAAEAAAAF+wAAAB4AVwBvAHIAawBzAHAAYQBjAGUARABvAGMAawBfADEBAAAAAP////8AAABKAP////sAAAAoAFMAaQBuAGcAbABlAEIAcgBhAGkAbgBzAHQAbwByAG0ARABvAGMAawEAAAAA/////wAAAikA////+wAAACoAUgBlAHMAZQBhAHIAYwBoAEEAcwBzAGkAcwB0AGEAbgB0AEQAbwBjAGsBAAAAAP////8AAAIoAP////sAAAAcAFMAaQBuAGcAbABlAEMAaABhAHQARABvAGMAawEAAAAA/////wAAAEoA////+wAAAB4AVwBvAHIAawBzAHAAYQBjAGUARABvAGMAawBfADEBAAAD8gAAA44AAAAAAAAAAPsAAAAaAFMAaQBuAGcAbABlAE8AQwBSAEQAbwBjAGsBAAADpQAAAJMAAAAAAAAAAPsAAAAoAFMAaQBuAGcAbABlAEQAaQBjAHQAaQBvAG4AYQByAHkARABvAGMAawAAAANdAAAA2wAAAAAAAAAAAAAAAAAAA/kAAAAEAAAABAAAAAgAAAAI/AAAAAEAAAACAAAAAQAAABYATQBhAGkAbgBUAG8AbwBsAGIAYQByAQAAAAD/////AAAAAAAAAAA=" 
        
        # 3. Tell the orchestrator to build it!
        self._sync_dock_counts(hardcoded_counts)
        self.restoreState(QByteArray.fromBase64(hardcoded_state.encode('utf-8')))
    def _apply_layout_template(self, name):
        import json
        payload_str = self.settings.value(f"layouts/{name}")
        if not payload_str: return
        
        try:
            # Detect if it's the new JSON format or an old broken string
            if payload_str.startswith("{"):
                payload = json.loads(payload_str)
                state_str = payload.get("state", "")
                counts = payload.get("counts", {})
            else:
                state_str = payload_str
                counts = {} # Old saves don't have counts, so this will close everything safely

            self.setUpdatesEnabled(False) # <--- STOP DRAWING
            try:
                # 1. Let the Orchestrator build the physical windows
                self._sync_dock_counts(counts)

                # 2. Tell Qt to arrange them
                if state_str:
                    from PySide6.QtCore import QByteArray
                    self.restoreState(QByteArray.fromBase64(state_str.encode('utf-8')))

                # 3. Force visibility
                from PySide6.QtWidgets import QDockWidget
                for dock in self.findChildren(QDockWidget): dock.show()
            finally:
                self.setUpdatesEnabled(True) # <--- RESUME DRAWING
            
        except Exception as e:
            print(f"Failed to apply custom layout: {e}")

    def _refresh_layout_templates_menu(self):
        self.custom_layouts_menu.clear()
        self.delete_layouts_menu.clear()
        
        self.settings.beginGroup("layouts")
        keys = self.settings.childKeys()
        self.settings.endGroup()
        
        if not keys:
            self.custom_layouts_menu.addAction("No custom templates saved").setEnabled(False)
            self.delete_layouts_menu.addAction("No custom templates saved").setEnabled(False)
            return
            
        for key in keys:
            # Load Action
            load_action = self.custom_layouts_menu.addAction(key)
            load_action.triggered.connect(lambda checked=False, k=key: self._apply_layout_template(k))
            
            # Delete Action
            delete_action = self.delete_layouts_menu.addAction(f"Delete '{key}'")
            delete_action.triggered.connect(lambda checked=False, k=key: self._delete_layout_template(k))

    def _delete_layout_template(self, name):
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import Qt
        
        # The WindowStaysOnTopHint ensures the confirmation box doesn't get hidden behind the main window
        reply = QMessageBox.question(
            self, "Delete Layout", 
            f"Are you sure you want to delete the layout '{name}'?", 
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.settings.remove(f"layouts/{name}")
            self.settings.sync()
            self._refresh_layout_templates_menu()
    def _get_visible_count(self, dock_list):
        """Only counts docks that are currently open and visible on the screen."""
        return sum(1 for view in dock_list if view and view.parentWidget() and view.parentWidget().isVisible())
    def _sync_dock_counts(self, counts):
        """Spawns missing docks and destroys excess/hidden docks to match a layout."""
        
        # 1. SPAWN missing docks
        while len(self.workspace_docks) < counts.get("workspaces", 0): self.spawn_workspace_dock()
        while len(self.notes_docks) < counts.get("notes", 0): self.spawn_notes_dock()
        while len(self.chat_docks) < counts.get("chats", 0): self.spawn_chat_dock()
        while len(self.scratchpad_docks) < counts.get("scratchpads", 0): self.spawn_scratchpad_dock()
        while len(self.ocr_docks) < counts.get("ocrs", 0): self.spawn_ocr_dock()
        while len(self.audio_docks) < counts.get("audios", 0): self.spawn_audio_dock()
        while len(getattr(self, 'essay_docks', [])) < counts.get("essays", 0): self.spawn_essay_dock()
        while len(getattr(self, 'dict_docks', [])) < counts.get("dicts", 0): self.spawn_dictionary_dock()
        while len(getattr(self, 'research_docks', [])) < counts.get("research", 0): self.spawn_research_dock()
        while len(getattr(self, 'brainstorm_docks', [])) < counts.get("brainstorm", 0): self.spawn_brainstorm_dock()

        # 2. DESTROY excess docks (Targeting hidden ones first!)
        def trim_docks(dock_list, target_count):
            # Phase A: Purge any docks that are currently hidden/closed
            for view in list(dock_list):
                if len(dock_list) <= target_count: break
                if view.parentWidget() and not view.parentWidget().isVisible():
                    dock_list.remove(view)
                    view.parentWidget().close()
                    view.parentWidget().deleteLater()
                    
            # Phase B: If we STILL have too many, pop from the end of the list
            while len(dock_list) > target_count:
                view = dock_list.pop()
                if view and view.parentWidget():
                    view.parentWidget().close()
                    view.parentWidget().deleteLater()

        trim_docks(self.workspace_docks, counts.get("workspaces", 0))
        trim_docks(self.notes_docks, counts.get("notes", 0))
        trim_docks(self.chat_docks, counts.get("chats", 0))
        trim_docks(self.scratchpad_docks, counts.get("scratchpads", 0))
        trim_docks(self.ocr_docks, counts.get("ocrs", 0))
        trim_docks(self.audio_docks, counts.get("audios", 0))
        trim_docks(getattr(self, 'essay_docks', []), counts.get("essays", 0))
        trim_docks(getattr(self, 'dict_docks', []), counts.get("dicts", 0))
        trim_docks(getattr(self, 'research_docks', []), counts.get("research", 0))
        trim_docks(getattr(self, 'brainstorm_docks', []), counts.get("brainstorm", 0))

    def _save_project_as(self):
        if not self.project_manager.project_filepath:
            QMessageBox.warning(self, "No Project", "Create or open a project first.")
            return
            
        path, _ = QFileDialog.getSaveFileName(self, "Save Project As", "", "PDF Project (*.pdfproj)")
        if path:
            if not path.lower().endswith(".pdfproj"):
                path += ".pdfproj"
                
            old_path = self.project_manager.project_filepath
            old_chroma_dir = old_path + "_chroma_db"
            new_chroma_dir = path + "_chroma_db"
            
            for ws in self.workspace_docks: ws.save_workspace_state()
            self.project_manager.save_all_docs()
            
            if self.project_manager._conn:
                self.project_manager._conn.close()
                self.project_manager._conn = None
                
            try:
                shutil.copy2(old_path, path)
                if os.path.exists(old_chroma_dir):
                    if os.path.exists(new_chroma_dir):
                        shutil.rmtree(new_chroma_dir)
                    shutil.copytree(old_chroma_dir, new_chroma_dir)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to copy project database: {e}")
                self.project_manager._init_db() 
                return

            self.project_manager.project_filepath = path
            self.project_manager.project_name = os.path.basename(path).replace(".pdfproj", "")
            
            self.project_manager._init_db()
            cursor = self.project_manager._conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", 
                           ("project_name", self.project_manager.project_name))
            self.project_manager._conn.commit()
            
            for c in self.chat_docks: c.refresh_project_ui()
                
            self.settings.setValue("last_project", path)
            self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")
    def _open_project(self):
        dialog = QFileDialog(self, "Open Project")
        dialog.setNameFilter("PDF Project (*.pdfproj);;All Files (*)")
        
        if dialog.exec():
            path = dialog.selectedFiles()[0]
            self._load_project(path)

   
    def _add_pdf(self):
        if not self.project_manager.project_filepath:
            QMessageBox.warning(self, "No Project", "Please Create or Open a Project first.")
            return
            
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Add PDFs to Project", "", "PDF Files (*.pdf)")
        for path in file_paths:
            self.project_manager.add_pdf(path)
            
        if file_paths:
            self._refresh_doc_list()
            self.switch_to_pdf(file_paths[-1])

    def _refresh_doc_list(self):
        self.doc_list.blockSignals(True)
        self.doc_list.clear()
        
        selected_tag = self.doc_tag_filter.currentData() if hasattr(self, 'doc_tag_filter') else "ALL_TAGS"
        
        for path in self.project_manager.pdfs:
            doc_tags = self.project_manager.get_tags_for_doc(path)
            
            # Filter Logic
            if selected_tag and selected_tag != "ALL_TAGS":
                if selected_tag not in [t.get("name") for t in doc_tags]:
                    continue 

            item = QListWidgetItem(self.doc_list)
            self.doc_list.addItem(item)
            
            # Custom Widget for the List Item
            widget = QWidget()
            widget.setStyleSheet("background: transparent;") # Crucial so selection styling works
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(5, 2, 5, 2)
            layout.setSpacing(4)
            
            # Use the ElidedLabel, and set its policy to Expanding!
            lbl = ElidedLabel(os.path.basename(path))
            lbl.setStyleSheet("background: transparent;")
            from PySide6.QtWidgets import QSizePolicy
            lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            
            # ---> CRITICAL FIX: The '1' gives the label a stretch factor, 
            # effectively turning it into the "spring" that fills the remaining space.
            layout.addWidget(lbl, 1) 
            
            # Group the dots into a fixed-size container so they ALWAYS show on the right edge
            tag_container = QWidget()
            tag_layout = QHBoxLayout(tag_container)
            tag_layout.setContentsMargins(0, 0, 0, 0)
            tag_layout.setSpacing(2)
            tag_container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

            # Add up to 4 colored tag dots to the row
            for t in doc_tags[:4]:
                dot = QLabel("●")
                color = t.get('color', '#888')
                dot.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")
                dot.setToolTip(t.get("name", ""))
                tag_layout.addWidget(dot)
            
            layout.addWidget(tag_container, 0) # '0' means this container will not stretch
            
            item.setSizeHint(widget.sizeHint())
            self.doc_list.setItemWidget(item, widget)
            item.setData(Qt.ItemDataRole.UserRole, path)
            
        self.doc_list.blockSignals(False)

    def _on_doc_list_clicked(self, item):
        pdf_path = item.data(Qt.ItemDataRole.UserRole)
        self.switch_to_pdf(pdf_path)

    def switch_to_pdf(self, pdf_path):
        if not os.path.exists(pdf_path): return
        
        # Highlight it in the Dock Explorer
        for i in range(self.doc_list.count()):
            item = self.doc_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == pdf_path:
                self.doc_list.blockSignals(True)
                self.doc_list.setCurrentItem(item)
                self.doc_list.blockSignals(False)
                break
                
        # ... (Leave the rest of switch_to_pdf exactly as it was)

        self.current_file_path = pdf_path
        self.project_manager.set_active_file(pdf_path)
        
        doc = self.project_manager.get_doc(pdf_path)
        if doc:
            success = self.viewer.load_document(doc)
            if success:
                self._check_needs_ocr()
                self._sync_tools_with_file(pdf_path)
            else:
                QMessageBox.warning(self, "Error", "Failed to load the PDF document.")
        else:
            QMessageBox.warning(self, "Error", "Failed to access the file from the filesystem.")

    # REPLACE THESE METHODS IN: gui/main_window.py

    # REPLACE THESE METHODS IN: gui/main_window.py

    
    def save_project(self):
        if not getattr(self.project_manager, 'project_filepath', None): 
            return

        if hasattr(self, 'workspace_docks'):
            for ws in self.workspace_docks: 
                try: ws.save_workspace_state()
                except Exception: pass

        # 1. Clean the list of dead C++ docks to prevent deadlocks
        valid_essays = []
        for dock in getattr(self, 'essay_docks', []):
            try:
                _ = dock.objectName() # Touch the object to confirm it is alive
                valid_essays.append(dock)
            except RuntimeError:
                pass
        self.essay_docks = valid_essays

        # 2. If no essays are active, save DB instantly
        if not self.essay_docks:
            self.project_manager.save_all_docs()
            if hasattr(self, '_show_save_indicator'):
                self._show_save_indicator()
            return

        # 3. Handle asynchronous saving with countdown
        self.pending_saves = len(self.essay_docks)
        def check_all_saved():
            self.pending_saves -= 1
            if self.pending_saves <= 0:
                self.project_manager.save_all_docs()
                if hasattr(self, '_show_save_indicator'):
                    self._show_save_indicator()

        for essay_view in self.essay_docks:
            try:
                essay_view.save_essay_state(callback_after=check_all_saved)
            except RuntimeError:
                check_all_saved()

    def autosave_project(self):
        if not getattr(self.project_manager, 'project_filepath', None): return

        if hasattr(self, 'workspace_docks'):
            for ws in self.workspace_docks: 
                try: ws.save_workspace_state()
                except Exception: pass

        valid_essays = []
        for dock in getattr(self, 'essay_docks', []):
            try:
                _ = dock.objectName()
                valid_essays.append(dock)
            except RuntimeError: pass
        self.essay_docks = valid_essays

        if not self.essay_docks:
            self.project_manager.save_all_docs()
            return

        self.pending_autosaves = len(self.essay_docks)
        def check_all_autosaved():
            self.pending_autosaves -= 1
            if self.pending_autosaves <= 0:
                self.project_manager.save_all_docs()

        for essay_view in self.essay_docks:
            try: essay_view.save_essay_state(callback_after=check_all_autosaved)
            except RuntimeError: check_all_autosaved()

    def _show_save_indicator(self):
        self.statusBar().showMessage("💾 Project saved successfully.", 3000)
        if hasattr(self, 'btn_save'):
            original_text = self.btn_save.text()
            self.btn_save.setText("✅ Saved!")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.btn_save.setText(original_text))

    def _show_save_indicator(self):
        """Displays visual confirmation that the master database successfully committed."""
        self.statusBar().showMessage("💾 Project saved successfully.", 3000)
        if hasattr(self, 'btn_save'):
            original_text = self.btn_save.text()
            self.btn_save.setText("✅ Saved!")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.btn_save.setText(original_text))

    def add_ai_annotation(self, quote, note, target_doc_name=None, allowed_paths=None, forced_annot_id=None, emit_signal=True):
        if not quote: return False
        clean_quote = quote.strip()
        
        search_paths = allowed_paths if allowed_paths else self.project_manager.pdfs
        
        if target_doc_name:
            filtered_paths = []
            for p in search_paths:
                if target_doc_name.lower().strip() in os.path.basename(p).lower():
                    filtered_paths.append(p)
            if filtered_paths:
                search_paths = filtered_paths

        found_any = False

        for path in search_paths:
            try:
                doc = self.project_manager.get_doc(path)
                if not doc: continue
                
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    
                    # Strictly search for the exact quote. Removed the chunking fallback 
                    # that caused single-word partial highlights in the wrong documents.
                    rects = page.search_for(clean_quote)
                    
                    if rects:
                        quads = [r.quad for r in rects]
                        annot = page.add_highlight_annot(quads)
                        annot.set_colors(stroke=(0.7, 0.4, 1.0))
                        
                        annot_id_to_use = forced_annot_id if forced_annot_id else f"AINote|{uuid.uuid4()}"
                        annot_info = {
                            "title": annot_id_to_use,
                            "content": note,
                            "subject": clean_quote
                        }
                        annot.set_info(info=annot_info)
                        annot.update()
                        
                        self.viewer.annot_manager.highlight_created.emit({
                            "id": annot_id_to_use,
                            "subject": clean_quote,
                            "content": note,
                            "pdf_path": path,
                            "page_num": page_num,
                            "rect_coords": repr(list(annot.rect)),
                            "color": QColor(179, 102, 255).name(),
                        })
                        
                        found_any = True
                        self.project_manager.mark_dirty(path)
                        
                        if path == self.current_file_path:
                            self.viewer.reload_page(page_num)
                            
                        break # Break page loop
                
                if found_any and forced_annot_id:
                    break # Break document loop

            except Exception as e:
                print(f"Error adding AI annotation to {path}: {e}")

        if found_any and emit_signal:
            self.viewer.annot_manager.note_added.emit()
            
        return found_any

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
        # 1. Anchor: Document Explorer Dock
        self.doc_dock = QDockWidget("📁 Document Explorer", self)
        self.doc_dock.setObjectName("DocExplorerDock")
        self.doc_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        
        from PySide6.QtWidgets import QWidget, QVBoxLayout, QListWidget, QComboBox
        doc_container = QWidget()
        doc_layout = QVBoxLayout(doc_container)
        doc_layout.setContentsMargins(0, 0, 0, 0)
        doc_layout.setSpacing(0)
        
        # --- NEW: Tag Filter for Document List ---
        self.doc_tag_filter = QComboBox()
        self.doc_tag_filter.setStyleSheet("padding: 4px; margin: 4px; font-weight: bold;")
        self.doc_tag_filter.addItem("All Tags", "ALL_TAGS")
        self.doc_tag_filter.currentIndexChanged.connect(self._refresh_doc_list)
        doc_layout.addWidget(self.doc_tag_filter)
        # -----------------------------------------
        
        self.doc_list = QListWidget()
        self.doc_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        from PySide6.QtWidgets import QAbstractItemView
        self.doc_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.doc_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        if hasattr(self, '_on_doc_list_context_menu'):
            self.doc_list.customContextMenuRequested.connect(self._on_doc_list_context_menu)
        if hasattr(self, '_on_doc_list_clicked'):
            self.doc_list.itemClicked.connect(self._on_doc_list_clicked)
        doc_layout.addWidget(self.doc_list)
        
        self.btn_add_pdf_dock = QPushButton("➕ Add PDF to Project")
        self.btn_add_pdf_dock.clicked.connect(self._add_pdf)
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

    def _refresh_doc_tag_filter(self):
        """Refreshes the available tags in the Document Explorer combo box."""
        if not hasattr(self, 'doc_tag_filter'): return
        
        current = self.doc_tag_filter.currentData()
        self.doc_tag_filter.blockSignals(True)
        self.doc_tag_filter.clear()
        self.doc_tag_filter.addItem("All Tags", "ALL_TAGS")
        
        tags = self.project_manager.get_all_tags()
        for t in tags:
            if t.get("name"):
                self.doc_tag_filter.addItem(t.get("name"), t.get("name"))
                
        index = self.doc_tag_filter.findData(current)
        if index >= 0:
            self.doc_tag_filter.setCurrentIndex(index)
            
        self.doc_tag_filter.blockSignals(False)

    def broadcast_note_added(self):
        self._mark_current_dirty()
        for notes_view in self.notes_docks: notes_view.refresh_notes()
        for ws_view in self.workspace_docks: ws_view.save_workspace_state()

    def broadcast_highlight_created(self, highlight_data):
        from PySide6.QtGui import QColor
        pm = self.project_manager
        pm.upsert_highlight({
            "id": highlight_data.get("id"),
            "doc_id": highlight_data.get("pdf_path"),
            "page_num": highlight_data.get("page_num"),
            "rect_coords": highlight_data.get("rect_coords"),
            "text_content": highlight_data.get("subject", ""),
            "color": highlight_data.get("color"),
        })
        for ws_view in self.workspace_docks:
            ws_view.handle_highlight_created(highlight_data)

    def broadcast_annotation_clicked(self, annot_id, page_num):
        pdf_path = self.current_file_path
        if not pdf_path or page_num is None or page_num < 0: return
        
        doc = self.project_manager.get_doc(pdf_path)
        if not doc: return
        
        page = doc.load_page(page_num)
        target_annot = None
        for annot in page.annots():
            if annot.info and annot.info.get("title") == annot_id:
                target_annot = annot
                break
                
        if not target_annot: return
        
        # Safely check if it exists AND is not None before deleting
        if hasattr(self, 'quick_note_popup') and self.quick_note_popup is not None:
            try:
                self.quick_note_popup.deleteLater()
            except RuntimeError:
                pass # Catches cases where C++ already deleted the underlying object
        self.quick_note_popup = None
            
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QCursor
        
        self.quick_note_popup = QDialog(self, Qt.WindowType.Tool)
        self.quick_note_popup.setWindowTitle("📝 Edit Highlight")
        # 🔥 FIX: Made the default size much smaller and cleaner
        self.quick_note_popup.setMinimumSize(280, 200) 
        self.quick_note_popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self.quick_note_popup)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # --- 1. Quote Display ---
        quote_text = target_annot.info.get("subject", "No quote extracted")
        lbl_quote = QTextEdit()
        lbl_quote.setPlainText(f'"{quote_text}"')
        lbl_quote.setReadOnly(True)
        lbl_quote.setMaximumHeight(45) # 🔥 FIX: Constrain quote height
        lbl_quote.setStyleSheet("background: transparent; border: none; font-style: italic; color: #888;")
        layout.addWidget(lbl_quote)

        # --- 2. Note Box ---
        layout.addWidget(QLabel("<b>Your Note:</b>"))
        note_editor = QTextEdit()
        note_editor.setPlainText(target_annot.info.get("content", ""))
        note_editor.setPlaceholderText("Type your thoughts here...")
        layout.addWidget(note_editor)

        # --- 3. Toolbar (Colors + Delete) ---
        toolbar = QHBoxLayout()
        colors = [
            ("#ffe16b", (1.0, 0.88, 0.42)), 
            ("#ff9d9d", (1.0, 0.61, 0.61)), 
            ("#a8ff9d", (0.66, 1.0, 0.61)), 
            ("#9de1ff", (0.61, 0.88, 1.0)), 
            ("#d89dff", (0.84, 0.61, 1.0))  
        ]
        
        color_layout = QHBoxLayout()
        color_layout.setSpacing(6)
        for hex_col, rgb_tuple in colors:
            btn_col = QPushButton()
            btn_col.setFixedSize(22, 22)
            btn_col.setStyleSheet(f"background-color: {hex_col}; border-radius: 11px; border: 1px solid #555;")
            btn_col.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_col.clicked.connect(lambda checked, c=rgb_tuple, h=hex_col: change_highlight_color(c, h))
            color_layout.addWidget(btn_col)
            
        toolbar.addLayout(color_layout)
        toolbar.addStretch()
        
        btn_delete = QPushButton("🗑️ Delete")
        btn_delete.setStyleSheet("background-color: transparent; border: none; color: #ff4444; font-weight: bold;")
        btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_delete.clicked.connect(lambda: delete_highlight())
        toolbar.addWidget(btn_delete)
        layout.addLayout(toolbar)

        if hasattr(self, 'theme_manager'):
            theme = self.theme_manager.get_theme()
            self.quick_note_popup.setStyleSheet(f"""
                QDialog {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; }}
                QTextEdit {{ 
                    background-color: {theme['bg_panel']}; 
                    color: {theme['text_main']}; 
                    border: 1px solid {theme['border']}; 
                    border-radius: 6px;
                    padding: 6px;
                }}
            """)

        # --- Logic Closures ---
        state = {"is_deleted": False} # Prevents saving a deleted note
        
        def save_note_text():
            if state["is_deleted"]: return
            
            new_text = note_editor.toPlainText()
            if target_annot.info.get("content") == new_text:
                return # Skip if nothing changed
                
            # 1. Update PyMuPDF
            info = dict(target_annot.info)
            info["content"] = new_text
            target_annot.set_info(info=info)
            target_annot.update()
            
            # 2. Update highlights DB
            pm = self.project_manager
            pm.mark_dirty(pdf_path)
            if pm._conn:
                cursor = pm._conn.cursor()
                cursor.execute("UPDATE highlights SET text_content = ? WHERE id = ?", (new_text, annot_id))
                pm._conn.commit()
                
            # 🔥 FIX: Update Workspace nodes in RAM (solves the SQL Schema Crash)
            for ws in self.workspace_docks:
                for node in ws.nodes.values():
                    if getattr(node, 'highlight_id', None) == annot_id:
                        node.note = new_text
                        node.update()
                pm.mark_dirty("workspace")
                
            for nd in self.notes_docks: nd.refresh_notes()

        def change_highlight_color(rgb_tuple, hex_col):
            target_annot.set_colors(stroke=rgb_tuple)
            target_annot.update()
            
            pm = self.project_manager
            pm.mark_dirty(pdf_path)
            if pm._conn:
                cursor = pm._conn.cursor()
                cursor.execute("UPDATE highlights SET color = ? WHERE id = ?", (hex_col, annot_id))
                pm._conn.commit()

            # Update RAM nodes
            for ws in self.workspace_docks:
                for node in ws.nodes.values():
                    if getattr(node, 'highlight_id', None) == annot_id:
                        node.color = hex_col
                        node.update()
                pm.mark_dirty("workspace")

            self.viewer.reload_page(page_num)
            for nd in self.notes_docks: nd.refresh_notes()

        def delete_highlight():
            state["is_deleted"] = True
            page.delete_annot(target_annot)
            pm = self.project_manager
            pm.mark_dirty(pdf_path)
            pm.delete_highlight_record(annot_id)
            
            self.viewer.reload_page(page_num)
            for nd in self.notes_docks: nd.refresh_notes()
            for ws in self.workspace_docks: ws._sync_workspace()
            self.quick_note_popup.close()

        # 🔥 FIX: Hook saving ONLY to the window closing, not text typing!
        self.quick_note_popup.finished.connect(save_note_text)
        self.quick_note_popup.finished.connect(lambda: setattr(self, 'quick_note_popup', None))

        cursor_pos = QCursor.pos()
        self.quick_note_popup.move(cursor_pos.x() + 15, cursor_pos.y() + 15)
        self.quick_note_popup.show()
    def _check_needs_ocr(self):
        self.ocr_banner.hide()
        if not self.viewer.doc: return
        try:
            pages_to_check = min(3, len(self.viewer.doc))
            total_text = "".join([self.viewer.doc.load_page(i).get_text() for i in range(pages_to_check)])
            if len(total_text.strip()) < 50:
                self.ocr_banner.show()
        except: pass
    def closeEvent(self, event):
        """Intercepts the window closing to check for unsaved changes and clean up threads."""
        
        # 1. Check if there is anything actually waiting to be saved
        has_unsaved_changes = hasattr(self, 'project_manager') and bool(self.project_manager.dirty_docs)
        
        if has_unsaved_changes:
            from PySide6.QtWidgets import QMessageBox
            
            # 2. Pop up the native OS warning dialog
            reply = QMessageBox.question(
                self, 
                "Unsaved Changes",
                "You have unsaved changes in your project. Do you want to save before exiting?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save # Default button
            )
            
            if reply == QMessageBox.StandardButton.Save:
                # Attempt to save. If you have a method that handles this (like self.save_project), call it.
                if hasattr(self, 'save_project'):
                    self.save_project()
                event.accept()
                
            elif reply == QMessageBox.StandardButton.Discard:
                # User explicitly doesn't care, let the app die
                event.accept()
                
            else:
                # User hit Cancel, abort the close sequence entirely!
                event.ignore()
                return 

        # 3. Clean up background workers so the app doesn't leave ghost processes running in Task Manager
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
        self.spawn_ocr_dock()

    def _sync_tools_with_file(self, file_path):
        for n in self.notes_docks: n.refresh_notes()
        for c in self.chat_docks: c.refresh_project_ui()
        if hasattr(self, 'workspace_view'):
            if hasattr(self.workspace_view, 'refresh_unused_highlights'):
                self.workspace_view.refresh_unused_highlights()
            elif hasattr(self.workspace_view, 'load_unused_highlights'):
                self.workspace_view.load_unused_highlights()
    def toggle_tool_panel(self, tool_name, checked):
        dock_map = {
            "Documents": self.doc_dock,
            "Scratchpad": self.scratchpad_dock,
            "LLM Chat": self.llm_dock,
            "OCR": self.ocr_dock,
            "Audio (TTS)": self.audio_dock
        }
        
        if tool_name in dock_map:
            dock = dock_map[tool_name]
            dock.setVisible(checked)
            if checked:
                dock.raise_() # Bring to front if it's tabbed behind something