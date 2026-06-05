# gui/components/main_toolbar.py
from PySide6.QtWidgets import QToolBar, QPushButton, QMenu, QWidget, QHBoxLayout, QLabel, QComboBox, QSizePolicy, QMessageBox, QInputDialog
from PySide6.QtCore import Qt, QEvent
import webbrowser

class MainToolbar(QToolBar):
    def __init__(self, main_window):
        super().__init__("Main Toolbar", main_window)
        self.main_window = main_window
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
        project_menu.addAction("New Project...", mw._new_project)
        project_menu.addAction("Open Project...", mw._open_project)
        project_menu.addAction("Save Project As...", mw._save_project_as)
        project_menu.addSeparator()
        project_menu.addAction("Add PDF to Project...", mw._add_pdf)
        project_menu.addAction("🛡️ Export LLM Log...").triggered.connect(mw._export_llm_log)
        self.btn_project.setMenu(project_menu)
        self.addWidget(self.btn_project)

        # 3. Save Button
        mw.btn_save = QPushButton("💾")
        mw.btn_save.clicked.connect(mw.save_project)
        self.addWidget(mw.btn_save)

        # Spacer
        spacer1 = QWidget()
        spacer1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer1)

        # 4. Core Spawners
        self.addWidget(self._make_spawner("➕ Workspace", "workspaces"))
        self.addWidget(self._make_spawner("🔬 Research Assistant", "research"))
        self.addWidget(self._make_spawner("📝 Writing", "essays"))
        self.addWidget(self._make_spawner("📊 Slideshow Maker", "scratchpads"))

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

        # Spacer
        spacer2 = QWidget()
        spacer2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer2)

        # 6. Right-side Tools
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
        layout_menu.addAction("⭐ Set Current as Default Layout", mw._prompt_save_default)
        layout_menu.addAction("💾 Save as Custom Template...", mw._prompt_save_template)
        mw.custom_layouts_menu = layout_menu.addMenu("📁 Load Custom Template")
        mw.delete_layouts_menu = layout_menu.addMenu("🗑️ Delete Custom Template")
        layout_menu.addAction("🔄 Reset to Default Sane Layout", mw.layout_manager.apply_startup_layout)
        self.btn_layouts.setMenu(layout_menu)
        self.addWidget(self.btn_layouts)
        mw._refresh_layout_templates_menu()

        # Theme Selector
        theme_widget = QWidget()
        theme_layout = QHBoxLayout(theme_widget)
        theme_layout.setContentsMargins(5, 0, 5, 0)
        theme_layout.addWidget(QLabel("Theme:"))
        mw.theme_selector = QComboBox()
        mw.theme_selector.addItems(mw.theme_manager.themes.keys())
        mw.theme_selector.setCurrentText(mw.theme_manager.current_theme_name)
        mw.theme_selector.currentTextChanged.connect(mw._on_theme_changed)
        theme_layout.addWidget(mw.theme_selector)
        self.addWidget(theme_widget)

        # Full Screen
        mw.btn_fullscreen = QPushButton()
        self._configure_hover_expand(mw.btn_fullscreen, "⛶", "Full Screen", expanded_width=120)
        mw.btn_fullscreen.clicked.connect(mw.toggle_full_screen)
        self.addWidget(mw.btn_fullscreen)

    def _make_spawner(self, text, dock_id):
        btn = QPushButton(text)
        btn.clicked.connect(lambda checked=False: self.main_window.dock_manager.spawn(dock_id))
        return btn

    # --- Hover Logic Extracted from MainWindow ---
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