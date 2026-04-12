# gui/main_window.py
import os
import uuid
import fitz
import shutil
from PyQt6.QtWidgets import (QMainWindow, QSizePolicy, QWidget, QHBoxLayout, QVBoxLayout, 
                             QPushButton, QLabel, QStackedWidget, 
                             QFileDialog, QFrame, QButtonGroup, QMessageBox, QComboBox, QMenu,
                             QApplication, QDockWidget, QListWidget, QListWidgetItem, QTextEdit,QInputDialog) # <-- Added Dock, List, and TextEdit
from PyQt6.QtGui import QColor, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, QSettings, QTimer, QThread, QEvent

from core.project_manager import ProjectManager
from gui.components.pdf_viewer import PDFViewer
from gui.tabs.ocr_tab import OCRTab
from gui.tabs.tts_tab import TTSTab
from gui.tabs.llm_tab import LLMTab
from gui.tabs.notes_tab import NotesTab
from gui.theme import ThemeManager
from gui.components.help_dialog import HelpDialog
from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
from gui.components.dialogs.tag_manager_dialog import TagManagerDialog, TagAssignmentDialog
from core.prompt_manager import PromptManager


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
        
        # 1. Put this back so the app remembers its size on your monitor!
        self._apply_smart_window_size()
        
        # 2. You can leave the QSettings clearing lines here if you want, or delete them.
        self.settings = QSettings("PDFMultitool", "Workspace")
        # self.settings.remove("geometry") 
        # self.settings.remove("windowState")
        
        
        # 3. Force XFCE to respect the window size
        self.setMinimumSize(1200, 800)
        
        self.theme_manager = ThemeManager()
        self.project_manager = ProjectManager()
        self.project_manager.main_window = self
        self.current_file_path = None
        
        # 4. Initialize Viewer BEFORE building the menu
        self.viewer = PDFViewer()

        # 5. Top Menu
        self._build_top_menu()

        # 6. Ensure Docks can take up the whole screen cleanly
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks | 
            QMainWindow.DockOption.AnimatedDocks | 
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.GroupedDragging
        )
        self.setDockNestingEnabled(True)
        
        # 7. Give the central widget a 1x1 minimum size so it NEVER reports 0x0 to XFCE
        dummy_central = QWidget()
        dummy_central.setMinimumSize(1, 1)
        self.setCentralWidget(dummy_central)
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks | 
            QMainWindow.DockOption.AnimatedDocks | 
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.GroupedDragging
        )
        self.setDockNestingEnabled(True)
        dummy_central = QWidget()
        dummy_central.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCentralWidget(dummy_central)

        # Track active tool instances
        self.workspace_docks = []
        self.notes_docks = []
        self.chat_docks = []
        self.scratchpad_docks = []
        self.ocr_docks = []      # <-- NEW
        self.audio_docks = []

        from core.llm_manager import LocalLLMManager
        self.shared_llm_manager = LocalLLMManager()
        self.viewer = PDFViewer()

        # 2. Top Menu is now set as a standard QMainWindow MenuWidget
        self._build_top_menu()

        # 3. The Central Widget now exclusively holds the Canvas & OCR Banner
        self.central_wrapper = QWidget()
        self.central_layout = QVBoxLayout(self.central_wrapper)
        self.central_layout.setContentsMargins(0, 0, 0, 0)
        self.central_layout.setSpacing(0)
        self.setCentralWidget(self.central_wrapper)

        self._build_ocr_banner()
        self._build_workspace()
        self._setup_shortcuts()
        
        # Connect Theme Manager to trigger visual updates
        self.theme_manager.theme_changed.connect(self.update_theme)
        self.update_theme(self.theme_manager.get_theme()) # Initial Apply
        
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave_project)
        self.autosave_timer.start(5 * 60 * 1000) 
        
        last_project = self.settings.value("last_project", "")
        if last_project and os.path.exists(last_project):
            self._load_project(last_project)

        QTimer.singleShot(1500, self._trigger_background_preload)
        if self.settings.value("show_help_on_startup", True, type=bool):
            QTimer.singleShot(500, self.show_help_window)
            
    def show_help_window(self):
        # We keep a reference to it so it doesn't get garbage collected
        self.help_dialog = HelpDialog(self)
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
            
            default_model = self.chat_docks[0].model_combo.currentText() if self.chat_docks else "llama3"
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
        from PyQt6.QtWidgets import QScrollArea, QHBoxLayout, QWidget
        self.top_menu_scroll = QScrollArea()
        self.top_menu_scroll.setWidgetResizable(True)
        self.top_menu_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.top_menu_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.top_menu_scroll.setFixedHeight(60)

        self.top_menu = QWidget()
        self.top_menu.setMinimumHeight(55)
        menu_layout = QHBoxLayout(self.top_menu)
        menu_layout.setContentsMargins(10, 5, 10, 5)

        # Feedback Button (opens a placeholder link)
        self.btn_feedback = QPushButton()
        self._configure_hover_expand_button(self.btn_feedback, "💬", "Feedback", expanded_width=120)
        self.btn_feedback.clicked.connect(lambda: self._open_feedback_link())
        menu_layout.addWidget(self.btn_feedback)
        menu_layout.addSpacing(4)

        self.btn_project = QPushButton()
        self._configure_hover_expand_button(self.btn_project, "📁", "Project", expanded_width=120, collapsed_width=56)
        
        project_menu = QMenu(self)
        project_menu.addAction("New Project...", self._new_project)
        project_menu.addAction("Open Project...", self._open_project)
        project_menu.addAction("Save Project As...", self._save_project_as)
        project_menu.addSeparator()
        project_menu.addAction("Add PDF to Project...", self._add_pdf)
        self.btn_project.setMenu(project_menu)
        menu_layout.addWidget(self.btn_project)
        menu_layout.addSpacing(8)

       
        menu_layout.addSpacing(8)
        self.btn_save = QPushButton()
        self._configure_hover_expand_button(self.btn_save, "💾", "Save Project", expanded_width=140)
        self.btn_save.clicked.connect(self.save_project)
        menu_layout.addWidget(self.btn_save)
        menu_layout.addStretch()

        self.btn_zoom_out = QPushButton("➖")
        self.btn_zoom_out.clicked.connect(self.viewer.zoom_out)
        self.btn_zoom_reset = QPushButton("Fit Width")
        self.btn_zoom_reset.clicked.connect(self.viewer.zoom_reset)
        self.btn_zoom_in = QPushButton("➕")
        self.btn_zoom_in.clicked.connect(self.viewer.zoom_in)

        self.btn_fullscreen = QPushButton()
        self._configure_hover_expand_button(self.btn_fullscreen, "⛶", "Enter Full Screen", expanded_width=180)
        self.btn_fullscreen.clicked.connect(self.toggle_full_screen)
        
        menu_layout.addWidget(self.btn_zoom_out)
        menu_layout.addWidget(self.btn_zoom_reset)
        menu_layout.addWidget(self.btn_zoom_in)
        menu_layout.addWidget(self.btn_fullscreen)
        menu_layout.addStretch()

        # Theme Selector
        menu_layout.addWidget(QLabel("Theme:"))
        self.theme_selector = QComboBox()
        self.theme_selector.addItems(self.theme_manager.themes.keys())
        self.theme_selector.setCurrentText(self.theme_manager.current_theme_name)
        self.theme_selector.currentTextChanged.connect(self._on_theme_changed)
        menu_layout.addWidget(self.theme_selector)
        menu_layout.addSpacing(8)
        self.btn_layouts = QPushButton()
        self._configure_hover_expand_button(self.btn_layouts, "🗔", "Window Layouts", expanded_width=140)
        
        layout_menu = QMenu(self)
        layout_menu.addAction("⭐ Set Current as Default Layout", self._save_as_default_layout)
        layout_menu.addSeparator()
        layout_menu.addAction("💾 Save as Custom Template...", self._save_layout_template)
        layout_menu.addSeparator()
        self.custom_layouts_menu = layout_menu.addMenu("📁 Load Custom Template")
        layout_menu.addSeparator()
        layout_menu.addAction("🔄 Reset to Default Sane Layout", self._reset_default_layout)
        
        self.btn_layouts.setMenu(layout_menu)
        menu_layout.addWidget(self.btn_layouts)
        self._refresh_layout_templates_menu()
        self.btn_edit_theme = QPushButton()
        self._configure_hover_expand_button(self.btn_edit_theme, "✏️", "Edit Custom Theme", expanded_width=170)
        self.btn_edit_theme.clicked.connect(lambda: self.theme_manager.edit_custom_theme(self))
        menu_layout.addWidget(self.btn_edit_theme)

        self.btn_tag_manager = QPushButton()
        self._configure_hover_expand_button(self.btn_tag_manager, "🏷️", "Tag Manager", expanded_width=130)
        self.btn_tag_manager.clicked.connect(self._open_tag_manager)
        menu_layout.addWidget(self.btn_tag_manager)
        
        menu_layout.addSpacing(8)
        self.btn_help = QPushButton()
        self._configure_hover_expand_button(self.btn_help, "❓", "Help", expanded_width=100)
        self.btn_help.clicked.connect(self.show_help_window)
        menu_layout.addWidget(self.btn_help)
        menu_layout.addSpacing(8)

        self.btn_prompt_editor = QPushButton()
        self._configure_hover_expand_button(self.btn_prompt_editor, "🧠", "Prompt Editor", expanded_width=150)
        self.btn_prompt_editor.clicked.connect(self._open_prompt_editor)
        menu_layout.addWidget(self.btn_prompt_editor)
        menu_layout.addSpacing(8)

        self.tool_group = QButtonGroup(self)
        # Independent Tool Spawners
        self.btn_spawn_ws = QPushButton("➕ Workspace")
        self.btn_spawn_ws.clicked.connect(self.spawn_workspace_dock)
        menu_layout.addWidget(self.btn_spawn_ws)

        self.btn_spawn_chat = QPushButton("➕ AI Chat")
        self.btn_spawn_chat.clicked.connect(self.spawn_chat_dock)
        menu_layout.addWidget(self.btn_spawn_chat)

        self.btn_spawn_notes = QPushButton("➕ Notes List")
        self.btn_spawn_notes.clicked.connect(self.spawn_notes_dock)
        menu_layout.addWidget(self.btn_spawn_notes)
        
        self.btn_spawn_scratch = QPushButton("➕ Scratchpad")
        self.btn_spawn_scratch.clicked.connect(self.spawn_scratchpad_dock)
        menu_layout.addWidget(self.btn_spawn_scratch)

        self.top_menu_scroll.setWidget(self.top_menu)
        self.setMenuWidget(self.top_menu_scroll)

        self.top_menu_scroll.setWidget(self.top_menu)
        self.setMenuWidget(self.top_menu_scroll)

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
        
        from PyQt6.QtWidgets import QMessageBox
        from PyQt6.QtCore import Qt
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
        
        from gui.tabs.notes_tab import NotesTab
        notes_view = NotesTab(None, self.viewer, self)
        dock.setWidget(notes_view)
        
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.notes_docks.append(notes_view)
        if hasattr(self, 'theme_manager'): notes_view.update_theme(self.theme_manager.get_theme())
        notes_view.refresh_notes()
        dock.show()

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
        
        from gui.tabs.llm_tab import LLMTab
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
        from gui.tabs.ocr_tab import OCRTab
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
        from gui.tabs.tts_tab import TTSTab
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
        
        from PyQt6.QtWidgets import QTextEdit
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
            
            # 4. Update the LLM Tab list & ChromaDB
            if "LLM Chat" in self.tabs:
                self.tabs["LLM Chat"].refresh_project_ui()
                try:
                    self.tabs["LLM Chat"].llm_manager.rename_document_in_index(old_path, new_path)
                except Exception as e:
                    print(f"Failed to tell ChromaDB about rename: {e}")
                
            # 5. Live-update nodes in the workspace so the "Filter by PDF" doesn't break
            if "Notes" in self.tabs:
                ws_view = self.tabs["Notes"].workspace_view
                for node in ws_view.nodes.values():
                    if node.pdf_path == old_path:
                        node.pdf_path = new_path
                ws_view._refresh_pdf_list() # Re-populate the filter dropdown with the new name
            
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
            
            # 3. Update LLM Tab & ChromaDB
            if "LLM Chat" in self.tabs:
                self.tabs["LLM Chat"].refresh_project_ui()
                try:
                    self.tabs["LLM Chat"].llm_manager.remove_document_from_index(doc_path)
                except Exception as e:
                    print(f"Failed to tell ChromaDB about removal: {e}")
                    
            # 4. Clean up workspace nodes visually
            if "Notes" in self.tabs:
                ws_view = self.tabs["Notes"].workspace_view
                nodes_to_delete = [n for n in ws_view.nodes.values() if n.pdf_path == doc_path]
                for node in nodes_to_delete:
                    ws_view.delete_node(node)
                ws_view._refresh_pdf_list()
                
            # 5. Handle viewer state
            if self.current_file_path == doc_path:
                self.current_file_path = None
                if hasattr(self.viewer, 'scene') and self.viewer.scene:
                    self.viewer.scene.clear()
                if hasattr(self.viewer, 'doc'):
                    self.viewer.doc = None
                
                # Switch to the first available PDF, if any
                if self.pdf_selector.count() > 0:
                    self.switch_to_pdf(self.pdf_selector.itemData(0))

    def _on_theme_changed(self, theme_name):
        if theme_name == "Custom":
            self.theme_manager.edit_custom_theme(self)
            
        self.settings.setValue("theme", theme_name)
        self.theme_manager.set_theme(theme_name)

    def update_theme(self, theme):
        self.top_menu.setStyleSheet(f"background-color: {theme['bg_panel']}; border-bottom: 1px solid {theme['border']};")
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
                icon-size: 18px; /* Bigger hit target */
            }}
            QDockWidget::close-button:hover, QDockWidget::float-button:hover {{
                background: {theme['accent_hover']};
                border-radius: 4px;
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

            state_str = self.project_manager.get_metadata("window_layout_state")
            if state_str:
                from PyQt6.QtCore import QByteArray
                self.restoreState(QByteArray.fromBase64(state_str.encode('utf-8')))

            # --- BULLETPROOF FIX: Query the C++ object tree directly ---
            from PyQt6.QtWidgets import QDockWidget
            for dock in self.findChildren(QDockWidget):
                dock.show()
            # ----------------------------------------------------------
            
            for c in self.chat_docks: c.refresh_project_ui()
            
            if self.project_manager.pdfs:
                self.switch_to_pdf(self.project_manager.pdfs[0])
        else:
            QMessageBox.warning(self, "Error", "Failed to load project file.")
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
            from PyQt6.QtCore import QByteArray
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
            from PyQt6.QtCore import QByteArray
            self.restoreState(QByteArray.fromBase64(default_state.encode('utf-8')))
        else:
            HARDCODED_DEFAULT = "YOUR_MASTER_STRING_HERE"
            from PyQt6.QtCore import QByteArray
            self.restoreState(QByteArray.fromBase64(HARDCODED_DEFAULT.encode('utf-8')))

        # 3. Force visibility
        from PyQt6.QtWidgets import QDockWidget
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
        for path in self.project_manager.pdfs:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.doc_list.addItem(item)
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
        self.ocr_banner = QFrame()
        self.ocr_banner.setFixedHeight(45)
        banner_layout = QHBoxLayout(self.ocr_banner)
        banner_layout.setContentsMargins(20, 0, 10, 0)
        self.lbl_ocr_banner = QLabel("⚠️ Scanned document detected. Run OCR?")
        banner_layout.addWidget(self.lbl_ocr_banner)
        banner_layout.addStretch()
        btn_run = QPushButton("Run OCR")
        btn_run.setStyleSheet("background-color: white; color: black; border: none;")
        btn_run.clicked.connect(self._trigger_auto_ocr)
        banner_layout.addWidget(btn_run)
        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.setStyleSheet("background-color: transparent; border: 1px solid #1e1e1e; color: #1e1e1e;")
        btn_dismiss.clicked.connect(self.ocr_banner.hide)
        banner_layout.addWidget(btn_dismiss)
        self.central_layout.addWidget(self.ocr_banner)
        self.ocr_banner.hide()

    def _build_workspace(self):
        # 1. Anchor: PDF Viewer Dock (Permanently Locked)
        self.pdf_dock = QDockWidget("📄 PDF Viewer", self)
        self.pdf_dock.setObjectName("PDFViewerDock")
        self.pdf_dock.setWidget(self.viewer)
        # NoDockWidgetFeatures strips the dragging handles and close buttons
        self.pdf_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures) 
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.pdf_dock)

        # 2. Anchor: Document Explorer Dock (Permanently Locked)
        self.doc_dock = QDockWidget("📁 Document Explorer", self)
        self.doc_dock.setObjectName("DocExplorerDock")
        self.doc_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        
        # Create a container widget to hold the list AND the Add button
        from PyQt6.QtWidgets import QWidget, QVBoxLayout, QListWidget
        doc_container = QWidget()
        doc_layout = QVBoxLayout(doc_container)
        doc_layout.setContentsMargins(0, 0, 0, 0)
        doc_layout.setSpacing(0)
        
        self.doc_list = QListWidget()
        self.doc_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        if hasattr(self, '_on_doc_list_context_menu'):
            self.doc_list.customContextMenuRequested.connect(self._on_doc_list_context_menu)
        if hasattr(self, '_on_doc_list_clicked'):
            self.doc_list.itemClicked.connect(self._on_doc_list_clicked)
        doc_layout.addWidget(self.doc_list)
        
        # The new inline Add PDF button
        self.btn_add_pdf_dock = QPushButton("➕ Add PDF to Project")
        self.btn_add_pdf_dock.clicked.connect(self._add_pdf)
        # Give it a bit of padding so it looks like a nice bottom-anchored action
        self.btn_add_pdf_dock.setStyleSheet("padding: 10px; font-weight: bold; border: none; border-top: 1px solid #444;") 
        doc_layout.addWidget(self.btn_add_pdf_dock)
            
        self.doc_dock.setWidget(doc_container)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.doc_dock)
        
    

    def broadcast_note_added(self):
        self._mark_current_dirty()
        for notes_view in self.notes_docks: notes_view.refresh_notes()
        for ws_view in self.workspace_docks: ws_view.save_workspace_state()

    def broadcast_highlight_created(self, highlight_data):
        from PyQt6.QtGui import QColor
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

    def broadcast_annotation_clicked(self, annot_id):
        # Open a notes dock if none exist
        if not self.notes_docks: self.spawn_notes_dock()
        # Make the first available notes dock scroll to the specific note
        if self.notes_docks:
            self.notes_docks[0].scroll_to_note(annot_id)

    def _on_annotation_clicked(self, annot_id):
        self.tool_buttons["Notes"].setChecked(True)
        self.toggle_tool_panel("Notes")
        self.tabs["Notes"].scroll_to_note(annot_id)

    def _check_needs_ocr(self):
        self.ocr_banner.hide()
        if not self.viewer.doc: return
        try:
            pages_to_check = min(3, len(self.viewer.doc))
            total_text = "".join([self.viewer.doc.load_page(i).get_text() for i in range(pages_to_check)])
            if len(total_text.strip()) < 50:
                self.ocr_banner.show()
        except: pass

    def _trigger_auto_ocr(self):
        self.ocr_banner.hide()
        self.tool_buttons["OCR"].setChecked(True)
        self.toggle_tool_panel("OCR")

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