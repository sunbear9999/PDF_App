# gui/components/main_toolbar.py
from PySide6.QtWidgets import QToolBar, QPushButton, QMenu, QWidget, QHBoxLayout, QLabel, QComboBox, QSizePolicy, QMessageBox, QInputDialog, QFileDialog
from PySide6.QtCore import Qt, QEvent
import webbrowser
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentIntent, DocumentPayload
from core.events.domains.project_events import ProjectIntent, ProjectPayload

class MainToolbar(QToolBar):
    def __init__(self, main_window):
        super().__init__("Main Toolbar", main_window)
        self.main_window = main_window
        self.bus = EventBus.get_instance()
        self.setObjectName("MainToolbar")
        self.setMovable(False)
        self.setFloatable(False)
        
        self._build_ui()

    def _build_ui(self):
        mw = self.main_window # Shorthand
        
        # 1. Feedback
        self.btn_feedback = QPushButton()
        self._configure_hover_expand(self.btn_feedback, "💬", "Feedback", expanded_width=110, collapsed_width=60)
        self.btn_feedback.clicked.connect(lambda: webbrowser.open("https://docs.google.com/forms/d/e/1FAIpQLSfm3W0Z-79jSJ1uuUgiUoi2CXMkyxLM3S3jyEw931aIDNDFag/viewform?usp=publish-editor"))
        self.addWidget(self.btn_feedback)

        # 2. Project Menu
        self.btn_project = QPushButton()
        self._configure_hover_expand(self.btn_project, "📁", "Project", expanded_width=100, collapsed_width=60)
        project_menu = QMenu(self)
        project_menu.addAction("📄 New Project...", self._trigger_new_project)
        project_menu.addAction("📂 Open Project...", self._trigger_open_project)
        project_menu.addAction("💾 Save Project As...", self._trigger_save_as)
        project_menu.addSeparator()
        project_menu.addAction("➕ Add PDF to Project...", self._trigger_add_pdf)
        project_menu.addAction("🛡️ Export LLM Log...").triggered.connect(self._trigger_export_log)
        self.btn_project.setMenu(project_menu)
        self.addWidget(self.btn_project)

        # 3. Save Button
        mw.btn_save = QPushButton("💾")
        mw.btn_save.setToolTip("Save Project")
        # Fixed the lambda so it actually executes the save method
        mw.btn_save.clicked.connect(lambda: self.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload()))
        self.addWidget(mw.btn_save)

        # Spacer 1
        spacer1 = QWidget()
        spacer1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer1)

        # 4. Core Spawners
        self.addWidget(self._make_spawner("➕ Workspace", "workspaces"))
        self.addWidget(self._make_spawner("🔬 Research Assistant", "research"))
        self.addWidget(self._make_spawner("📝 Writing", "essays"))
        self.addWidget(self._make_spawner("📊 Slideshow Maker", "slides"))

        # 5. Dropdown Spawner
        self.btn_other_tools = QPushButton("➕ Other Tools")
        other_menu = QMenu(self)
        other_menu.addAction("📝 Notes List").triggered.connect(lambda c=False: mw.dock_manager.spawn("notes"))
        other_menu.addAction("✍️ Scratchpad").triggered.connect(lambda c=False: mw.dock_manager.spawn("scratchpads"))
        other_menu.addAction("📖 Dictionary").triggered.connect(lambda c=False: mw.dock_manager.spawn("dicts"))
        other_menu.addAction("📚 Citations").triggered.connect(lambda c=False: mw.dock_manager.spawn("citations"))
        other_menu.addAction("👁️ OCR Scanner").triggered.connect(lambda c=False: mw.dock_manager.spawn("ocrs"))
        other_menu.addAction("🔊 Audio (TTS)").triggered.connect(lambda c=False: mw.dock_manager.spawn("audios"))
        self.btn_other_tools.setMenu(other_menu)
        self.addWidget(self.btn_other_tools)

        # Spacer 2
        spacer2 = QWidget()
        spacer2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer2)

        # 6. Right-side Tools
        if hasattr(mw, 'process_monitor'):
            mw.process_monitor.setMaximumHeight(30)
            self.addWidget(mw.process_monitor)

        self.btn_tag_manager = QPushButton()
        self._configure_hover_expand(self.btn_tag_manager, "🏷️", "Tag Manager", expanded_width=130, collapsed_width=60)
        self.btn_tag_manager.clicked.connect(mw._open_tag_manager)
        self.addWidget(self.btn_tag_manager)

        self.btn_prompt_editor = QPushButton()
        self._configure_hover_expand(self.btn_prompt_editor, "🧠", "Prompt Editor", expanded_width=140)
        self.btn_prompt_editor.clicked.connect(mw._open_prompt_editor)
        self.addWidget(self.btn_prompt_editor)

        # Layouts Menu
        self.btn_layouts = QPushButton()
        self._configure_hover_expand(self.btn_layouts, "🗔", "Window Layouts", expanded_width=160, collapsed_width=65)
        layout_menu = QMenu(self)
        layout_menu.addAction("⭐ Set Current as Default Layout", self._prompt_save_default)
        layout_menu.addAction("💾 Save as Custom Template...", self._prompt_save_template)
        
        # Switched these to self instead of mw so the local helpers can rebuild them
        self.custom_layouts_menu = layout_menu.addMenu("📁 Load Custom Template")
        self.delete_layouts_menu = layout_menu.addMenu("🗑️ Delete Custom Template")
        layout_menu.addAction("🔄 Reset to Default Sane Layout", mw.layout_manager.apply_startup_layout)
        
        self.btn_layouts.setMenu(layout_menu)
        self.addWidget(self.btn_layouts)
        self._refresh_layout_templates_menu()

        # Theme Selector
        theme_widget = QWidget()
        theme_layout = QHBoxLayout(theme_widget)
        theme_layout.setContentsMargins(5, 0, 5, 0)
        theme_layout.addWidget(QLabel("Theme:"))
        
        self.theme_selector = QComboBox()
        # Uses the new theme manager safely
        if hasattr(mw.theme_manager, 'get_available_themes'):
            self.theme_selector.addItems(mw.theme_manager.get_available_themes())
        else:
            self.theme_selector.addItems(mw.theme_manager.themes.keys())
            
        if hasattr(mw.theme_manager, 'current_theme_name'):
            self.theme_selector.setCurrentText(mw.theme_manager.current_theme_name)
            
        self.theme_selector.currentTextChanged.connect(mw._on_theme_changed)
        theme_layout.addWidget(self.theme_selector)
        self.addWidget(theme_widget)

        # Full Screen
        mw.btn_fullscreen = QPushButton()
        self._configure_hover_expand(mw.btn_fullscreen, "⛶", "Full Screen", expanded_width=120)
        mw.btn_fullscreen.clicked.connect(mw.toggle_full_screen)
        self.addWidget(mw.btn_fullscreen)


    # ==========================================
    # --- DECOUPLED EVENT BUS HANDLERS ---
    # ==========================================

    def _trigger_new_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Create New Project", "", "PDF Project (*.pdfproj)")
        if path: self.bus.project_action_requested.emit(ProjectIntent.CREATE, ProjectPayload(path=path))

    def _trigger_open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "PDF Project (*.pdfproj);;All Files (*)")
        if path: self.bus.project_action_requested.emit(ProjectIntent.LOAD, ProjectPayload(path=path))

    def _trigger_save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Project As", "", "PDF Project (*.pdfproj)")
        if path: self.bus.project_action_requested.emit(ProjectIntent.SAVE_AS, ProjectPayload(new_path=path))
        
    def _trigger_add_pdf(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add PDFs", "", "PDF Files (*.pdf)")
        if paths: self.bus.document_action_requested.emit(DocumentIntent.ADD_FILES, DocumentPayload(paths=paths))

    def _trigger_export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export LLM Log", "LLM_Log.pdf", "PDF Documents (*.pdf)")
        if path: self.bus.project_action_requested.emit(ProjectIntent.EXPORT_LOG, ProjectPayload(path=path))

    def _prompt_save_default(self):
        if hasattr(self.main_window, 'layout_manager'):
            self.main_window.layout_manager.save_current_as_default()
            QMessageBox.information(self, "Default Set", "This layout is now your permanent default!", QMessageBox.StandardButton.Ok)

    def _prompt_save_template(self):
        name, ok = QInputDialog.getText(self, "Save Layout Template", "Enter a name for this layout:")
        if ok and name.strip() and hasattr(self.main_window, 'layout_manager'):
            self.main_window.layout_manager.save_template(name)
            self._refresh_layout_templates_menu()
            QMessageBox.information(self, "Saved", f"Layout '{name}' saved successfully!", QMessageBox.StandardButton.Ok)

    def _prompt_delete_template(self, name):
        reply = QMessageBox.question(self, "Delete Layout", f"Delete the layout '{name}'?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes and hasattr(self.main_window, 'layout_manager'):
            self.main_window.layout_manager.delete_template(name)
            self._refresh_layout_templates_menu()

    def _refresh_layout_templates_menu(self):
        """Rebuilds the dynamic layout menu."""
        if not hasattr(self, 'custom_layouts_menu'): return
        
        self.custom_layouts_menu.clear()
        self.delete_layouts_menu.clear()
        
        if not hasattr(self.main_window, 'layout_manager'): return
        keys = self.main_window.layout_manager.get_template_names()
        
        if not keys:
            self.custom_layouts_menu.addAction("No custom templates saved").setEnabled(False)
            self.delete_layouts_menu.addAction("No custom templates saved").setEnabled(False)
            return
            
        for key in keys:
            load_action = self.custom_layouts_menu.addAction(key)
            load_action.triggered.connect(lambda checked=False, k=key: self.main_window.layout_manager.load_template(k))
            
            delete_action = self.delete_layouts_menu.addAction(f"Delete '{key}'")
            delete_action.triggered.connect(lambda checked=False, k=key: self._prompt_delete_template(k))


    # ==========================================
    # --- ANIMATION & HOVER LOGIC ---
    # ==========================================

    def _make_spawner(self, text, dock_id):
        btn = QPushButton(text)
        btn.clicked.connect(lambda checked=False: self.main_window.dock_manager.spawn(dock_id))
        return btn

    def _configure_hover_expand(self, button, icon, label, expanded_width=170, collapsed_width=44):
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