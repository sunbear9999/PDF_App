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
        from gui.managers.dock_manager import DockManager, DockDefinition
        self.dock_manager = DockManager(self)
        self.layout_manager = LayoutManager(self)
        self._register_core_docks()
        self.process_registry = ProcessRegistry()
        from core.llm_manager import LocalLLMManager
        self.shared_llm_manager = LocalLLMManager()
        self.shared_llm_manager.set_audit_logger(self.project_manager.log_ai_interaction_threadsafe)
        self.prompt_manager = PromptManager()
        self.step_manager = StepManager()
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
        self.bus = EventBus.get_instance()

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
        self.ui_router.attach_runner(runner) 
        
        # Enqueue the runner instead of starting it directly
        job_name = getattr(blueprint, 'name', 'AI Action')
        self.process_registry.enqueue_runner(runner, job_name)
    def _register_core_docks(self):
        from gui.managers.dock_manager import DockDefinition
        from PySide6.QtWidgets import QDockWidget, QTextEdit
        from PySide6.QtCore import Qt

        def make_research(w):
            from gui.docks.unified_research.unified_dock import UnifiedResearchDock
            return UnifiedResearchDock(w, w.project_manager, w.shared_llm_manager, w)
            
        def make_workspace(w):
            dock = QDockWidget("🧠 Workspace", w)
            from gui.components.workspace_view import WorkspaceView
            dock.setWidget(WorkspaceView(w))
            return dock
            
        def make_notes(w):
            dock = QDockWidget("📝 Notes List", w)
            from gui.docks.notes_dock import NotesTab
            dock.setWidget(NotesTab(None, w.viewer, w))
            return dock

        def make_dict(w):
            dock = QDockWidget("📖 Dictionary", w)
            from gui.docks.dictionary_dock import DictionaryTab
            dock.setWidget(DictionaryTab(w.dictionary_manager, w))
            return dock

        def make_essay(w):
            dock = QDockWidget("📝 Essay Writer", w)
            from gui.docks.essay_dock import EssayTab
            dock.setWidget(EssayTab(w.project_manager, w))
            return dock

        def make_citations(w):
            dock = QDockWidget("📚 Citation Manager", w)
            from gui.docks.citation_dock import CitationDock
            dock.setWidget(CitationDock(w.citation_manager, w.project_manager, w))
            return dock

        def make_ocr(w):
            dock = QDockWidget("👁️ OCR Scanner", w)
            from gui.docks.ocr_dock import OCRTab
            dock.setWidget(OCRTab(None, w))
            return dock

        def make_audio(w):
            dock = QDockWidget("🔊 Audio (TTS)", w)
            from gui.docks.tts_dock import TTSTab
            dock.setWidget(TTSTab(None, w))
            return dock

        def make_scratchpad(w):
            dock = QDockWidget("✍️ Scratchpad", w)
            editor = QTextEdit()
            editor.setPlaceholderText("Jot down quick thoughts here...\n\n(Stays perfectly saved in memory until you load a new project)")
            dock.setWidget(editor)
            return dock

        dm = self.dock_manager
        R = Qt.DockWidgetArea.RightDockWidgetArea
        # ID, ObjectName, Menu UI Name, Area, Singleton, Factory
        dm.register(DockDefinition("research", "SingleResearchDock", "Research Assistant", R, True, make_research))
        dm.register(DockDefinition("workspaces", "WorkspaceDock", "Workspaces", R, False, make_workspace))
        dm.register(DockDefinition("notes", "NotesDock", "Notes List", R, True, make_notes))
        dm.register(DockDefinition("dicts", "SingleDictionaryDock", "Dictionary", R, True, make_dict))
        dm.register(DockDefinition("essays", "EssayDock", "Essay Writer", R, False, make_essay))
        dm.register(DockDefinition("citations", "SingleCitationDock", "Citations", R, True, make_citations))
        dm.register(DockDefinition("ocrs", "SingleOCRDock", "OCR Scanner", R, True, make_ocr))
        dm.register(DockDefinition("audios", "SingleAudioDock", "Audio (TTS)", R, True, make_audio))
        dm.register(DockDefinition("scratchpads", "ScratchDock", "Scratchpad", R, False, make_scratchpad))
    def _run_startup_sequence(self):
        settings = QSettings("PDFMultitool", "Workspace")
        last_project_path = settings.value("last_project_path", "")
        
        import os
        if last_project_path and os.path.exists(last_project_path):
            self._load_project(last_project_path)
        else:
            # Fallback if no project was found
            if hasattr(self, 'layout_manager'):
                self.layout_manager.restore_last_session()       
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


    def _trigger_background_preload(self):
        try:
            # --- THE PRE-WARM FIX ---
            # Boot the heavy graphics engine using a Page instead of a View.
            # This completely prevents the GUI flash without crashing the OS Compositor!
            from PySide6.QtWebEngineCore import QWebEnginePage
            self._dummy_browser = QWebEnginePage(self)

            if not hasattr(self, 'shared_llm_manager') or not self.shared_llm_manager.ai_enabled: 
                return
            
            try:
                active_model = "gemma4:e2b" # Fallback default
                
                # Safely ask the registry if any Research docks are open
                research_docks = self.dock_manager.get_inner_widgets("research")
                if research_docks:
                    research_ui = research_docks[0]
                    # Dig into your unified dock to find the active model combo box
                    # Adjust 'model_combo' to whatever the attribute is actually named inside UnifiedResearchDock
                    if hasattr(research_ui, 'model_combo'):
                        active_model = research_ui.model_combo.currentText()
                        
                self.shared_llm_manager.preload_model(active_model)
            except Exception as e:
                print(f"Could not trigger preload: {e}")
        except Exception as e:
            print(f"Preload setup failed: {e}")
                

   

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self.viewer.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self.viewer.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self.viewer.zoom_reset)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.viewer.annot_manager.toggle_search)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_project)
        QShortcut(QKeySequence("F11"), self).activated.connect(self.toggle_full_screen)

    def _prompt_save_default(self):
        self.layout_manager.save_current_as_default()
        QMessageBox.information(self, "Default Set", "This layout is now your permanent default!", QMessageBox.StandardButton.Ok)

    def _prompt_save_template(self):
        name, ok = QInputDialog.getText(self, "Save Layout Template", "Enter a name for this layout:")
        if ok and name.strip():
            self.layout_manager.save_template(name)
            self._refresh_layout_templates_menu()
            QMessageBox.information(self, "Saved", f"Layout '{name}' saved successfully!", QMessageBox.StandardButton.Ok)

    def _prompt_delete_template(self, name):
        reply = QMessageBox.question(self, "Delete Layout", f"Delete the layout '{name}'?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.layout_manager.delete_template(name)
            self._refresh_layout_templates_menu()
    def _refresh_layout_templates_menu(self):
        self.custom_layouts_menu.clear()
        self.delete_layouts_menu.clear()
        
        keys = self.layout_manager.get_template_names()
        
        if not keys:
            self.custom_layouts_menu.addAction("No custom templates saved").setEnabled(False)
            self.delete_layouts_menu.addAction("No custom templates saved").setEnabled(False)
            return
            
        for key in keys:
            load_action = self.custom_layouts_menu.addAction(key)
            load_action.triggered.connect(lambda checked=False, k=key: self.layout_manager.load_template(k))
            
            delete_action = self.delete_layouts_menu.addAction(f"Delete '{key}'")
            delete_action.triggered.connect(lambda checked=False, k=key: self._prompt_delete_template(k))
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
    
    def _open_prompt_editor(self):
        prompt_manager = self.shared_llm_manager.prompt_manager if hasattr(self, 'shared_llm_manager') else PromptManager()
        dialog = PromptEditorDialog(prompt_manager, self)
        dialog.exec()

    def _open_tag_manager(self):
        dialog = TagManagerDialog(self.project_manager, self)
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

    def _clear_ui_for_new_project(self):
        self.current_file_path = None
        
        if hasattr(self, 'doc_explorer'):
            self.doc_explorer.refresh_list()
        
        if hasattr(self.viewer, 'scene') and self.viewer.scene:
            self.viewer.scene.clear()
        if hasattr(self.viewer, 'doc'):
            self.viewer.doc = None
            
        # Nuke all docks cleanly via the manager!
        self.dock_manager.clear_all()

    def broadcast_note_added(self):
        self._mark_current_dirty()
        for notes_view in self.dock_manager.get_inner_widgets("notes"): 
            notes_view.refresh_notes()
        for ws_view in self.dock_manager.get_inner_widgets("workspaces"): 
            ws_view.save_workspace_state()

    

    
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
            if hasattr(self, 'doc_explorer'):
                self.doc_explorer.refresh_tag_filter()
            self.bus.project_loaded.emit()
        
            # Failsafe direct call
            if hasattr(self, 'doc_explorer'):
                self.doc_explorer.refresh_list()
            
            self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")
            QTimer.singleShot(50, self.layout_manager.reset_default_layout)
            

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
            if hasattr(self, 'doc_explorer'):
                self.doc_explorer.refresh_list()
            
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
                            self.layout_manager._apply_state(state_str, counts)
                        except Exception as e:
                            print(f"Error loading project docks: {e}")
                    else:
                        self.layout_manager.restore_last_session()

                    # --- Restore Scratchpad Texts using Registry ---
                    text_data = self.project_manager.get_metadata("scratchpad_texts")
                    if text_data:
                        import json
                        try:
                            saved_texts = json.loads(text_data)
                            for i, editor in enumerate(self.dock_manager.get_inner_widgets("scratchpads")):
                                if i < len(saved_texts):
                                    editor.setPlainText(saved_texts[i])
                        except Exception as e:
                            print(f"Error loading scratchpad text: {e}")
                
                    # Update Research UI using Registry
                    for r in self.dock_manager.get_instances("research"): 
                        if hasattr(r, 'refresh_project_ui'): r.refresh_project_ui()
                    
                    if self.project_manager.pdfs:
                        self.switch_to_pdf(self.project_manager.pdfs[0])
                        
                finally:
                    # --- RESUME UI DRAWING ALL AT ONCE ---
                    self.setUpdatesEnabled(True)


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
            
            for r in self.research_docks: r.refresh_project_ui()
                
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
            if hasattr(self, 'doc_explorer'):
                self.doc_explorer.refresh_list()
            self.switch_to_pdf(file_paths[-1])

    

    def switch_to_pdf(self, pdf_path):
        if not os.path.exists(pdf_path): return
        
        # Highlight it in the Modular Dock Explorer
        if hasattr(self, 'doc_explorer'):
            for i in range(self.doc_explorer.doc_list.count()):
                item = self.doc_explorer.doc_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == pdf_path:
                    self.doc_explorer.doc_list.blockSignals(True)
                    self.doc_explorer.doc_list.setCurrentItem(item)
                    self.doc_explorer.doc_list.blockSignals(False)
                    break
                
        # ... (Leave the rest of switch_to_pdf exactly as it was)

        self.current_file_path = pdf_path
        self.project_manager.set_active_file(pdf_path)
        
        doc = self.project_manager.get_doc(pdf_path)
        if doc:
            success = self.viewer.load_document(doc)
            if success:
                self._check_needs_ocr()
                
                # --- CHANGED: Shouts to the Event Bus instead of manual syncing! ---
                EventBus.get_instance().pdf_switched.emit(pdf_path)
                
            else:
                QMessageBox.warning(self, "Error", "Failed to load the PDF document.")
        else:
            QMessageBox.warning(self, "Error", "Failed to access the file from the filesystem.")

    def save_project(self):
        if not getattr(self.project_manager, 'project_filepath', None): 
            return
        try:
            state_bytes = self.saveState().toBase64().data().decode('utf-8')
            counts = self.layout_manager.get_current_dock_counts()
            import json
            self.project_manager.set_metadata("window_layout_state", state_bytes)
            self.project_manager.set_metadata("open_docks_count", json.dumps(counts))
        except Exception as e:
            print(f"Failed to save layout to project metadata: {e}")
            
        for ws in self.dock_manager.get_inner_widgets("workspaces"): 
            try: ws.save_workspace_state()
            except Exception: pass

        essay_views = self.dock_manager.get_inner_widgets("essays")
        if not essay_views:
            self.project_manager.save_all_docs()
            if hasattr(self, '_show_save_indicator'):
                self._show_save_indicator()
            return

        self.pending_saves = len(essay_views)
        def check_all_saved():
            self.pending_saves -= 1
            if self.pending_saves <= 0:
                self.project_manager.save_all_docs()
                if hasattr(self, '_show_save_indicator'):
                    self._show_save_indicator()

        for essay_view in essay_views:
            try: essay_view.save_essay_state(callback_after=check_all_saved)
            except RuntimeError: check_all_saved()

    def autosave_project(self):
        if not getattr(self.project_manager, 'project_filepath', None): return

        for ws in self.dock_manager.get_inner_widgets("workspaces"): 
            try: ws.save_workspace_state()
            except Exception: pass

        essay_views = self.dock_manager.get_inner_widgets("essays")
        if not essay_views:
            self.project_manager.save_all_docs()
            return

        self.pending_autosaves = len(essay_views)
        def check_all_autosaved():
            self.pending_autosaves -= 1
            if self.pending_autosaves <= 0:
                self.project_manager.save_all_docs()

        for essay_view in essay_views:
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

    def add_ai_annotation(self, quote, note, target_doc_name=None, allowed_paths=None, forced_annot_id=None):
        if not quote: return False
        clean_quote = quote.strip()
        
        search_paths = allowed_paths if allowed_paths else self.project_manager.pdfs
        if target_doc_name:
            target = target_doc_name.lower().strip()
            search_paths = [p for p in search_paths if target in os.path.basename(p).lower()]

        found_any = False
        import uuid
        import os

        for path in search_paths:
            try:
                doc = self.project_manager.get_doc(path)
                if not doc: continue
                
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    rects = page.search_for(clean_quote)
                    
                    if rects:
                        quads = [r.quad for r in rects]
                        annot = page.add_highlight_annot(quads)
                        annot.set_colors(stroke=(0.7, 0.4, 1.0)) 
                        
                        annot_id = forced_annot_id or f"AINote|{uuid.uuid4()}"
                        annot.set_info(info={"title": annot_id, "content": note, "subject": clean_quote})
                        annot.update()
                        self.project_manager.mark_dirty(path)
                        
                        # Use the instance attribute you already set up!
                        self.bus.highlight_created.emit({
                            "id": annot_id,
                            "subject": clean_quote,
                            "content": note,
                            "pdf_path": path,
                            "page_num": page_num,
                            "rect_coords": repr(list(annot.rect)),
                            "color": "#b366ff"
                        })
                        
                        if path == self.current_file_path:
                            self.viewer.reload_page(page_num)
                            
                        found_any = True
                        break 
                
                if found_any and forced_annot_id:
                    break 

            except Exception as e:
                print(f"Error adding AI annotation to {path}: {e}")

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
        
        # Safely clean up old popup
        if hasattr(self, 'quick_note_popup') and self.quick_note_popup is not None:
            try:
                self.quick_note_popup.deleteLater()
            except RuntimeError: pass
            
        # Dispatch to our clean extracted dialog
        from gui.components.dialogs.quick_note_dialog import QuickNoteDialog
        from PySide6.QtGui import QCursor
        
        self.quick_note_popup = QuickNoteDialog(target_annot, annot_id, page_num, pdf_path, self, self)
        
        # Position and show
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
                if hasattr(self, 'save_project'):
                    self.save_project()
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