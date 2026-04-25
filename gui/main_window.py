# gui/main_window.py
import os
import uuid
import fitz
import shutil
from PySide6.QtWidgets import (QMainWindow, QSizePolicy, QWidget, QHBoxLayout, QVBoxLayout, 
                             QPushButton, QLabel, QStackedWidget, 
                             QFileDialog, QFrame, QButtonGroup, QMessageBox, QComboBox, QMenu,
                             QApplication, QDockWidget, QListWidget, QListWidgetItem, QTextEdit,QInputDialog) # <-- Added Dock, List, and TextEdit
from PySide6.QtGui import QColor, QShortcut, QKeySequence
from PySide6.QtCore import Qt, QSettings, QTimer, QThread, QEvent

from core.project_manager import ProjectManager
from gui.components.dialogs.extract_pages_dialog import ExtractPagesDialog
from gui.components.pdf_viewer import PDFViewer
from gui.docks.ocr_dock import OCRTab
from gui.docks.tts_dock import TTSTab
from gui.docks.llm_dock import LLMTab
from gui.docks.notes_dock import NotesTab
from gui.theme import ThemeManager
from gui.components.help_dialog import HelpDialog
from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
from gui.components.dialogs.tag_manager_dialog import TagManagerDialog, TagAssignmentDialog
from core.prompt_manager import PromptManager
from core.dictionary_manager import DictionaryManager


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
        self.setWindowTitle("Papyrus - Ethical, Offline Research Assistant")
        self._apply_smart_window_size()
        self.setMinimumSize(800, 600)
        self.settings = QSettings("PDFMultitool", "Workspace")
        
        self.theme_manager = ThemeManager()
        self.project_manager = ProjectManager()
        self.project_manager.main_window = self
        self.current_file_path = None
        
        from core.llm_manager import LocalLLMManager
        self.shared_llm_manager = LocalLLMManager()
        self.shared_llm_manager.set_audit_logger(self.project_manager.log_ai_interaction_threadsafe)
        # 1. INITIALIZE VIEWER EXPLICITLY ONCE
        self.viewer = PDFViewer()
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        dict_data_dir = os.path.join(root_dir, "data")
        self.dictionary_manager = DictionaryManager(dict_data_dir)
        if not self.dictionary_manager.get_available_dictionaries():
            # Assume you placed a file named 'default_english.json' in an 'assets' folder
            default_dict_path = os.path.join(root_dir, "assets", "default_english.json")
            
            if os.path.exists(default_dict_path):
                print("[System] First launch detected. Building default dictionary...")
                self.dictionary_manager.import_json(default_dict_path, "Default English")
        # 2. CONNECT CRITICAL SAVING SIGNALS 
        # This guarantees the ProjectManager knows to save the file!
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
        
        last_project = self.settings.value("last_project", "")
        if last_project and os.path.exists(last_project):
            self._load_project(last_project)

        QTimer.singleShot(1500, self._trigger_background_preload)
        if self.settings.value("show_help_on_startup", True, type=bool):
            QTimer.singleShot(500, self.show_help_window)
            
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

    def toggle_full_screen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._sync_full_screen_button()

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
        from PySide6.QtWidgets import QToolBar, QWidget, QHBoxLayout, QSizePolicy
        from PySide6.QtCore import Qt

        # 🔥 UPGRADE: Native QToolBar handles spacing, heights, and overflow automatically!
        self.top_toolbar = QToolBar("Main Toolbar", self)
        self.top_toolbar.setObjectName("MainToolbar")
        self.top_toolbar.setMovable(False)
        self.top_toolbar.setFloatable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.top_toolbar)

        # 1. Feedback
        self.btn_feedback = QPushButton()
        self._configure_hover_expand_button(self.btn_feedback, "💬", "Feedback", expanded_width=110,collapsed_width=60)
        self.btn_feedback.clicked.connect(lambda: self._open_feedback_link())
        self.top_toolbar.addWidget(self.btn_feedback)

        # 2. Project Menu
        self.btn_project = QPushButton()
        self._configure_hover_expand_button(self.btn_project, "📁", "Project", expanded_width=100,collapsed_width=60)
        project_menu = QMenu(self)
        project_menu.addAction("New Project...", self._new_project)
        project_menu.addAction("Open Project...", self._open_project)
        project_menu.addAction("Save Project As...", self._save_project_as)
        project_menu.addSeparator()
        project_menu.addAction("Add PDF to Project...", self._add_pdf)
        self.top_toolbar.addWidget(self.btn_project)
        export_action = project_menu.addAction("🛡️ Export LLM Log...")
        export_action.triggered.connect(self._export_llm_log)
        
        self.btn_project.setMenu(project_menu)
        # 3. Save Button
        self.btn_save = QPushButton("💾")
        self.btn_save.clicked.connect(self.save_project)
        self.top_toolbar.addWidget(self.btn_save)

        # Add a flexible spacer to push the next items to the right/center
        spacer1 = QWidget()
        spacer1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.top_toolbar.addWidget(spacer1)

        # 4. Spawners (Workspace, Chat, Notes, etc.)
        self.btn_spawn_ws = QPushButton("➕ Workspace")
        self.btn_spawn_ws.clicked.connect(self.spawn_workspace_dock)
        self.top_toolbar.addWidget(self.btn_spawn_ws)

        self.btn_spawn_chat = QPushButton("➕ AI Chat")
        self.btn_spawn_chat.clicked.connect(self.spawn_chat_dock)
        self.top_toolbar.addWidget(self.btn_spawn_chat)

        self.btn_spawn_notes = QPushButton("➕ Notes List")
        self.btn_spawn_notes.clicked.connect(self.spawn_notes_dock)
        self.top_toolbar.addWidget(self.btn_spawn_notes)

        self.btn_spawn_research = QPushButton("➕ Research Assistant")
        self.btn_spawn_research.clicked.connect(self.spawn_research_dock)
        self.top_toolbar.addWidget(self.btn_spawn_research)
        
        self.btn_spawn_scratch = QPushButton("➕ Scratchpad")
        self.btn_spawn_scratch.clicked.connect(self.spawn_scratchpad_dock)
        self.top_toolbar.addWidget(self.btn_spawn_scratch)
        
        self.btn_spawn_ocr = QPushButton("➕ OCR Scanner")
        self.btn_spawn_ocr.clicked.connect(self.spawn_ocr_dock)
        self.top_toolbar.addWidget(self.btn_spawn_ocr)

        self.btn_spawn_audio = QPushButton("➕ Audio (TTS)")
        self.btn_spawn_audio.clicked.connect(self.spawn_audio_dock)
        self.top_toolbar.addWidget(self.btn_spawn_audio)

        self.btn_spawn_dict = QPushButton("📖 Dictionary")
        self.btn_spawn_dict.clicked.connect(self.spawn_dictionary_dock)
        self.top_toolbar.addWidget(self.btn_spawn_dict)

        spacer2 = QWidget()
        spacer2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.top_toolbar.addWidget(spacer2)

        # 5. Right-side Tools
        self.btn_tag_manager = QPushButton()
        self._configure_hover_expand_button(self.btn_tag_manager, "🏷️", "Tag Manager", expanded_width=130,collapsed_width=60)
        self.btn_tag_manager.clicked.connect(self._open_tag_manager)
        self.top_toolbar.addWidget(self.btn_tag_manager)

        self.btn_prompt_editor = QPushButton()
        self._configure_hover_expand_button(self.btn_prompt_editor, "🧠", "Prompt Editor", expanded_width=140)
        self.btn_prompt_editor.clicked.connect(self._open_prompt_editor)
        self.top_toolbar.addWidget(self.btn_prompt_editor)

        self.btn_layouts = QPushButton()
        self._configure_hover_expand_button(self.btn_layouts, "🗔", "Window Layouts", expanded_width=160,collapsed_width=65)
        layout_menu = QMenu(self)
        layout_menu.addAction("⭐ Set Current as Default Layout", self._save_as_default_layout)
        layout_menu.addAction("💾 Save as Custom Template...", self._save_layout_template)
        self.custom_layouts_menu = layout_menu.addMenu("📁 Load Custom Template")
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
                
        # 2. Otherwise, create a new one
        self.ws_counter = getattr(self, 'ws_counter', 0) + 1
        dock = QDockWidget(f"🧠 Workspace {self.ws_counter}", self)
        dock.setObjectName(f"WorkspaceDock_{self.ws_counter}") 
        
        from gui.components.workspace_view import WorkspaceView
        ws_view = WorkspaceView(self)
        dock.setWidget(ws_view)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.workspace_docks.append(ws_view)
        if hasattr(self, 'theme_manager'): ws_view.update_theme(self.theme_manager.get_theme())
        ws_view._sync_workspace()
        dock.show()
    def spawn_research_dock(self):
        # STRICT SINGLETON: Like AI Chat, we only need one of these
        if self.research_docks:
            view = self.research_docks[0]
            if view.parentWidget():
                view.parentWidget().show()
                view.parentWidget().raise_()
            return
                
        dock = QDockWidget("🔬 Research Assistant", self)
        dock.setObjectName("ResearchAssistantDock") 
        
        # Import our new controller
        from gui.docks.research_assistant.controller import ResearchDockWidget
        
        research_view = ResearchDockWidget(self)
        dock.setWidget(research_view)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.research_docks.append(research_view)
        
        if hasattr(self, 'theme_manager'): 
            research_view.update_theme(self.theme_manager.get_theme())
            
        dock.show()

    def _save_as_default_layout(self):
        state_bytes = self.saveState().toBase64().data().decode('utf-8')
        self.settings.setValue("default_startup_layout", state_bytes)
        
        # Save the dock counts so we can recreate them on Reset!
        import json
        counts = {
            "workspaces": len(self.workspace_docks),
            "notes": len(self.notes_docks),
            "chats": len(self.chat_docks),
            "scratchpads": len(self.scratchpad_docks),
            "ocrs": len(self.ocr_docks),
            "audios": len(self.audio_docks)
        }
        self.settings.setValue("default_startup_counts", json.dumps(counts))
        
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import Qt
        msg = QMessageBox(self)
        msg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("Default Set")
        msg.setText("This layout is now your permanent default!")
        msg.exec()

    
    def spawn_notes_dock(self):
        for view in self.notes_docks:
            if view.parentWidget() and not view.parentWidget().isVisible():
                view.parentWidget().show(); view.parentWidget().raise_(); return
                
        self.notes_counter = getattr(self, 'notes_counter', 0) + 1
        dock = QDockWidget(f"📝 Notes List {self.notes_counter}", self)
        dock.setObjectName(f"NotesDock_{self.notes_counter}")
        
        from gui.docks.notes_dock import NotesTab
        notes_view = NotesTab(None, self.viewer, self)
        dock.setWidget(notes_view)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.notes_docks.append(notes_view)
        if hasattr(self, 'theme_manager'): notes_view.update_theme(self.theme_manager.get_theme())
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
    def spawn_chat_dock(self):
        # STRICT SINGLETON: AI doesn't run well concurrently
        if self.chat_docks:
            view = self.chat_docks[0]
            if view.parentWidget():
                view.parentWidget().show()
                view.parentWidget().raise_()
            return
                
        dock = QDockWidget("🤖 AI Chat", self)
        dock.setObjectName("SingleChatDock") # Constant name for layout saving
        
        from gui.docks.llm_dock import LLMTab
        chat_view = LLMTab(self.shared_llm_manager, None, self)
        dock.setWidget(chat_view)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.chat_docks.append(chat_view)
        if hasattr(self, 'theme_manager'): chat_view.update_theme(self.theme_manager.get_theme())
        chat_view.refresh_project_ui()
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
        dock.show()
        
    def spawn_scratchpad_dock(self):
        for view in self.scratchpad_docks:
            if view.parentWidget() and not view.parentWidget().isVisible():
                view.parentWidget().show(); view.parentWidget().raise_(); return
                
        self.scratch_counter = getattr(self, 'scratch_counter', 0) + 1
        dock = QDockWidget(f"✍️ Scratchpad {self.scratch_counter}", self)
        dock.setObjectName(f"ScratchDock_{self.scratch_counter}")
        
        from PySide6.QtWidgets import QTextEdit
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
        item = self.doc_list.itemAt(pos)
        if not item: return
        doc_path = item.data(Qt.ItemDataRole.UserRole)
        row = self.doc_list.row(item)
        
        menu = QMenu(self)
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
            for c in self.chat_docks: c.refresh_tag_filters()
        elif chosen == rename_action:
            self._ui_rename_pdf(doc_path, row)
        elif chosen == remove_action:
            self._ui_remove_pdf(doc_path, row)
        elif chosen == extract_action:
            dialog = ExtractPagesDialog(doc_path, self.project_manager, self)
            if dialog.exec():
                # Refresh your document list UI if the user successfully extracted a PDF
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
            # 3. Update the Document Explorer Dock
            item = self.doc_list.item(row)
            item.setText(new_name)
            item.setData(Qt.ItemDataRole.UserRole, new_path)
            
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
                    s
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

        # Broadcast to all live docks
        for ws in self.workspace_docks: ws.update_theme(theme)
        for n in self.notes_docks: n.update_theme(theme)
        for c in self.chat_docks: c.update_theme(theme)

        if hasattr(self.viewer, "update_theme"):
            self.viewer.update_theme(theme)

        # Add inside update_theme(self, theme):
        dock_style = f"""
            QDockWidget {{
                font-weight: bold;
                color: {theme['text_main']};
            }}
            QDockWidget::title {{
                background: {theme['bg_panel']};
                padding: 6px 10px;
                border: 1px solid {theme['border']};
            }}
            QDockWidget::close-button, QDockWidget::float-button {{
                background: transparent;
                padding: 4px;
                icon-size: 18px; 
            }}
            QDockWidget::close-button:hover, QDockWidget::float-button:hover {{
                background: {theme['accent_hover']};
                border-radius: 4px;
            }}
            /* NEW: Make dock separators fat and grabbable */
            QMainWindow::separator {{
                background: {theme['border']};
                width: 6px; 
                height: 6px;
            }}
            QMainWindow::separator:hover {{
                background: {theme['accent']};
            }}
        """
        self.setStyleSheet(self.styleSheet() + dock_style)

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
                    self.scratchpad_docks, self.ocr_docks, self.audio_docks]:
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
            self._refresh_doc_list()
            self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")
            
            # Spawn default docks for the new project
            

    def _load_project(self, path):
        if hasattr(self, 'shared_llm_manager'):
            self.shared_llm_manager.set_project_database(self.project_manager.project_filepath)
        if self.project_manager.project_filepath:
            self.save_project()
            
        if self.project_manager.load_project(path):
            self._clear_ui_for_new_project()
            self.settings.setValue("last_project", self.project_manager.project_filepath)
            self.setWindowTitle(f"Papyrus - {self.project_manager.project_name}")
            self._refresh_doc_list()
            
            # --- NEW: Restore Saved Layouts ---
            import json
            dock_info = self.project_manager.get_metadata("open_docks_count")
            if dock_info:
                counts = json.loads(dock_info)
                for _ in range(counts.get("workspaces", 0)): self.spawn_workspace_dock()
                for _ in range(counts.get("notes", 0)): self.spawn_notes_dock()
                for _ in range(counts.get("chats", 0)): self.spawn_chat_dock()
                for _ in range(counts.get("scratchpads", 0)): self.spawn_scratchpad_dock()
            else:
                # Fallback for old projects: spawn one of each AND apply your default layout
                self.spawn_workspace_dock()
                self.spawn_notes_dock()
                self._reset_default_layout() # <-- ADD THIS LINE
            text_data = self.project_manager.get_metadata("scratchpad_texts")
            if text_data:
                    try:
                        saved_texts = json.loads(text_data)
                        # Pair each saved text with its corresponding scratchpad dock
                        for i, editor in enumerate(self.scratchpad_docks):
                            if i < len(saved_texts):
                                editor.setPlainText(saved_texts[i])
                    except Exception as e:
                        print(f"Error loading scratchpad text: {e}")
            state_str = self.project_manager.get_metadata("window_layout_state")
            if state_str:
                from PySide6.QtCore import QByteArray
                self.restoreState(QByteArray.fromBase64(state_str.encode('utf-8')))

            # --- BULLETPROOF FIX: Query the C++ object tree directly ---
            from PySide6.QtWidgets import QDockWidget
            for dock in self.findChildren(QDockWidget):
                dock.show()
            # ----------------------------------------------------------
            
            for c in self.chat_docks: c.refresh_project_ui()
            
            if self.project_manager.pdfs:
                self.switch_to_pdf(self.project_manager.pdfs[0])
        else:
            QMessageBox.warning(self, "Error", "Failed to load project file.")
        self._refresh_doc_tag_filter()
    def _save_layout_template(self):
        # The WindowStaysOnTopHint prevents XFCE from hiding the popup
        name, ok = QInputDialog.getText(
            self, 
            "Save Layout Template", 
            "Enter a name for this layout (e.g., 'Writing Mode'):",
            flags=Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        if ok and name.strip():
            state_bytes = self.saveState().toBase64().data().decode('utf-8')
            self.settings.setValue(f"layouts/{name.strip()}", state_bytes)
            self._refresh_layout_templates_menu()
            
            # Force the success box to stay on top too
            msg = QMessageBox(self)
            msg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle("Saved")
            msg.setText(f"Layout '{name}' saved successfully!")
            msg.exec()

    def _refresh_layout_templates_menu(self):
        self.custom_layouts_menu.clear()
        self.settings.beginGroup("layouts")
        for key in self.settings.childKeys():
            action = self.custom_layouts_menu.addAction(key)
            action.triggered.connect(lambda checked, k=key: self._apply_layout_template(k))
        self.settings.endGroup()
        if self.custom_layouts_menu.isEmpty():
            self.custom_layouts_menu.addAction("No custom templates saved yet").setEnabled(False)

    def _apply_layout_template(self, name):
        state_str = self.settings.value(f"layouts/{name}")
        if state_str:
            from PySide6.QtCore import QByteArray
            self.restoreState(QByteArray.fromBase64(state_str.encode('utf-8')))

    def _reset_default_layout(self):
        import json
        default_state = self.settings.value("default_startup_layout")
        counts_str = self.settings.value("default_startup_counts")
        
        # 1. Hydrate the exact number of docks required for the layout
        if counts_str:
            try:
                counts = json.loads(counts_str)
                while len(self.workspace_docks) < counts.get("workspaces", 0): self.spawn_workspace_dock()
                while len(self.notes_docks) < counts.get("notes", 0): self.spawn_notes_dock()
                while len(self.chat_docks) < counts.get("chats", 0): self.spawn_chat_dock()
                while len(self.scratchpad_docks) < counts.get("scratchpads", 0): self.spawn_scratchpad_dock()
                while len(self.ocr_docks) < counts.get("ocrs", 0): self.spawn_ocr_dock()
                while len(self.audio_docks) < counts.get("audios", 0): self.spawn_audio_dock()
            except Exception as e: print(f"Error parsing layout counts: {e}")
        
        # 2. Restore the positions
        if default_state:
            from PySide6.QtCore import QByteArray
            self.restoreState(QByteArray.fromBase64(default_state.encode('utf-8')))
        else:
            HARDCODED_DEFAULT = "YOUR_MASTER_STRING_HERE"
            from PySide6.QtCore import QByteArray
            self.restoreState(QByteArray.fromBase64(HARDCODED_DEFAULT.encode('utf-8')))

        # 3. Force visibility
        from PySide6.QtWidgets import QDockWidget
        for dock in self.findChildren(QDockWidget): dock.show()

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
            
            lbl = QLabel(os.path.basename(path))
            lbl.setStyleSheet("background: transparent;")
            layout.addWidget(lbl)
            
            layout.addStretch()
            
            # Add up to 4 colored tag dots to the row
            for t in doc_tags[:4]:
                dot = QLabel("●")
                color = t.get('color', '#888')
                dot.setStyleSheet(f"color: {color}; font-size: 12px; background: transparent;")
                dot.setToolTip(t.get("name", ""))
                layout.addWidget(dot)
            
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

    def autosave_project(self):
        if self.project_manager.project_filepath:
            try:
                for ws in self.workspace_docks: ws.save_workspace_state()
                self.project_manager.save_all_docs()
            except Exception as e:
                print(f"Background autosave failed: {e}")

    def save_project(self):
        if not self.project_manager.project_filepath: return
        try:
            for ws in self.workspace_docks: ws.save_workspace_state()
            self.project_manager.save_all_docs()
            
            # --- NEW: Save Dock Layout State ---
            state_bytes = self.saveState().toBase64().data().decode('utf-8')
            self.project_manager.set_metadata("window_layout_state", state_bytes)
            
            active_docks = {
                "workspaces": self.ws_counter if hasattr(self, 'ws_counter') else 0,
                "notes": self.notes_counter if hasattr(self, 'notes_counter') else 0,
                "chats": self.chat_counter if hasattr(self, 'chat_counter') else 0,
                "scratchpads": self.scratch_counter if hasattr(self, 'scratch_counter') else 0
            }
            import json
            self.project_manager.set_metadata("open_docks_count", json.dumps(active_docks))
            scratch_texts = [editor.toPlainText() for editor in self.scratchpad_docks]
            self.project_manager.set_metadata("scratchpad_texts", json.dumps(scratch_texts))
            # -----------------------------------

            # Replace the old success/error boxes with these forced-on-top versions:
            msg = QMessageBox(self)
            msg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle("Success")
            msg.setText("Project, layouts, and highlights saved successfully!")
            msg.exec()
        except Exception as e:
            err = QMessageBox(self)
            err.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
            err.setIcon(QMessageBox.Icon.Warning)
            err.setWindowTitle("Save Error")
            err.setText(f"Error saving project: {str(e)}")
            err.exec()

    def add_ai_annotation(self, quote, note, target_doc_name=None, allowed_paths=None, forced_annot_id=None, emit_signal=True):
        if not quote: return False
        clean_quote = quote.strip()
        words = clean_quote.split()
        if not words: return False
        
        chunks = []
        if len(words) <= 6:
            chunks = [" ".join(words)]
        else:
            for i in range(0, len(words), 4):
                chunk = " ".join(words[i:i+6])
                if chunk.strip(): chunks.append(chunk)

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
                    
                    rects = page.search_for(clean_quote)
                    
                    if not rects and len(chunks) > 1:
                        rects = []
                        for chunk in chunks:
                            res = page.search_for(chunk)
                            if res: rects.extend(res)
                    
                    if rects:
                        quads = [r.quad for r in rects]
                        annot = page.add_highlight_annot(quads)
                        annot.set_colors(stroke=(0.7, 0.4, 1.0))
                        
                        # Apply forced ID if provided for workspace linking
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
                            
                        # Break out of page loop to avoid duplicates for the same quote
                        break
                
                if found_any and forced_annot_id:
                    break

            except Exception as e:
                print(f"Error adding AI annotation to {path}: {e}")

        # Suspend UI triggers for batched workspace graph building
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
        
        if hasattr(self, 'quick_note_popup') and self.quick_note_popup is not None:
            try:
                self.quick_note_popup.close()
                self.quick_note_popup.deleteLater()
            except RuntimeError:
                pass
            
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