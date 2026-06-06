from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QSplitter,
    QWidget, QInputDialog, QSizePolicy, QComboBox
)
from PySide6.QtCore import Qt
from core.events.domains.metadata_events import PromptIntent, PromptPayload
class PromptEditorDialog(QDialog):
    def __init__(self, prompt_manager, parent=None):
        super().__init__(parent)
        self.prompt_manager = prompt_manager
        self.blueprint_manager = getattr(parent, 'blueprint_manager', None)
        self.blueprint_registry = getattr(parent, 'blueprint_registry', None)
        self.step_manager = getattr(parent, 'step_manager', None)
        self.prompt_service = getattr(parent, 'prompt_app_service', None)
        if not self.prompt_service:
            from core.services.prompt_app_service import PromptAppService
            self.prompt_service = PromptAppService(
                prompt_manager,
                blueprint_manager=self.blueprint_manager,
                blueprint_registry=self.blueprint_registry,
                step_manager=self.step_manager,
            )

        self.theme = parent.theme_manager.get_theme() if hasattr(parent, 'theme_manager') else {
            'bg_main': '#1e1e1e', 'text_main': '#ffffff', 'bg_panel': '#333333',
            'bg_input': '#2b2b2b', 'border': '#444444', 'accent': '#b366ff',
            'danger': '#ff4444', 'success': '#00cc66', 'text_muted': '#aaaaaa'
        }

        self.setWindowTitle("System Prompt Configuration Manager")
        self.setMinimumSize(1050, 700)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # --- WARNING BANNER ---
        self.warning_label = QLabel(
            "<b>⚠️ WARNING:</b> Changing system prompts may alter AI output formatting. "
            "If modifying a tool's prompt, ensure any JSON output instructions remain intact."
        )
        self.warning_label.setWordWrap(True)
        self.warning_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.warning_label.setStyleSheet(f"""
            color: {self.theme.get('danger', '#ff4444')};
            background-color: rgba(255, 68, 68, 0.1);
            border: 1px solid {self.theme.get('danger', '#ff4444')};
            border-radius: 6px;
            padding: 10px;
        """)
        main_layout.addWidget(self.warning_label)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- LEFT PANEL: Categories & Custom Prompt Controls ---
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # View Mode Switcher
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItems(["📂 View by Category", "🗺️ View by Blueprint Pipeline"])
        self.view_mode_combo.currentIndexChanged.connect(self._on_view_mode_changed)
        left_layout.addWidget(self.view_mode_combo)

        # Blueprint Selector (Hidden initially)
        self.blueprint_combo = QComboBox()
        self.blueprint_combo.setVisible(False)
        self.blueprint_combo.currentIndexChanged.connect(self._populate_tree)
        left_layout.addWidget(self.blueprint_combo)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._on_item_selected)
        left_layout.addWidget(self.tree)

        # Controls for Custom Prompts
        self.tree_btn_layout = QHBoxLayout()
        self.btn_new = QPushButton("➕ New Prompt")
        self.btn_new.clicked.connect(self._on_new_prompt)
        self.btn_delete = QPushButton("🗑️ Delete")
        self.btn_delete.clicked.connect(self._on_delete_prompt)

        self.tree_btn_layout.addWidget(self.btn_new)
        self.tree_btn_layout.addWidget(self.btn_delete)
        left_layout.addLayout(self.tree_btn_layout)

        self.splitter.addWidget(left_container)

        # --- RIGHT PANEL: Editor ---
        self.editor_container = QWidget()
        editor_layout = QVBoxLayout(self.editor_container)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)

        self.lbl_current_prompt = QLabel("<b>No Prompt Selected</b>")
        self.lbl_current_prompt.setStyleSheet(f"font-size: 14px; color: {self.theme.get('text_main')};")
        editor_layout.addWidget(self.lbl_current_prompt)

        self.prompt_editor = QTextEdit()
        self.prompt_editor.setPlaceholderText("Select a prompt from the left panel to begin editing...")
        editor_layout.addWidget(self.prompt_editor)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.restore_button = QPushButton("🔄 Restore Default")
        self.restore_button.clicked.connect(self._on_restore_default)

        self.save_button = QPushButton("💾 Save Selection")
        self.save_button.clicked.connect(self._on_save)

        button_layout.addWidget(self.restore_button)
        button_layout.addWidget(self.save_button)
        editor_layout.addLayout(button_layout)

        self.splitter.addWidget(self.editor_container)
        self.splitter.setSizes([350, 700])
        main_layout.addWidget(self.splitter, 1)

        # --- BOTTOM ROW ---
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()

        self.cancel_button = QPushButton("Close")
        self.cancel_button.setFixedWidth(100)
        self.cancel_button.clicked.connect(self.reject)
        bottom_layout.addWidget(self.cancel_button)

        main_layout.addLayout(bottom_layout)

        self.current_prompt_key = None
        self._all_blueprints_cache = {}

        self._apply_theme()
        self._load_blueprint_dropdown()
        self._populate_tree()

    def _apply_theme(self):
        self.setStyleSheet(f"""
            QDialog {{ background-color: {self.theme.get('bg_main')}; color: {self.theme.get('text_main')}; }}
            QTreeWidget {{
                background-color: {self.theme.get('bg_input')}; color: {self.theme.get('text_main')};
                border: 1px solid {self.theme.get('border')}; border-radius: 6px; padding: 4px;
            }}
            QTreeWidget::item {{ padding: 6px; border-radius: 4px; }}
            QTreeWidget::item:selected {{ background-color: {self.theme.get('accent')}; color: white; }}
            QTextEdit {{
                background-color: {self.theme.get('bg_input')}; color: {self.theme.get('text_main')};
                border: 1px solid {self.theme.get('border')}; border-radius: 6px;
                font-family: monospace; font-size: 13px; padding: 12px; line-height: 1.4;
            }}
            QPushButton {{
                background-color: {self.theme.get('bg_panel')}; color: {self.theme.get('text_main')};
                border: 1px solid {self.theme.get('border')}; padding: 6px 14px; border-radius: 4px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {self.theme.get('border')}; }}
            QComboBox {{
                background-color: {self.theme.get('bg_input')}; color: {self.theme.get('text_main')};
                border: 1px solid {self.theme.get('border')}; border-radius: 4px; padding: 6px; font-weight: bold;
            }}
            QComboBox::drop-down {{ border: none; }}
        """)

        self.btn_new.setStyleSheet(f"background-color: {self.theme.get('success', '#00cc66')}; color: white; border: none;")
        self.save_button.setStyleSheet(f"background-color: {self.theme.get('accent')}; color: white; border: none; padding: 6px 16px;")
        self.restore_button.setStyleSheet(f"background-color: transparent; color: {self.theme.get('text_muted', '#aaa')}; border: 1px solid {self.theme.get('border')};")
        self.btn_delete.setStyleSheet("background-color: transparent; color: #ff4444; border: 1px solid #444;")

    def _get_prompts_dict(self):
        return self.prompt_service.get_prompts_dict()

    def _on_view_mode_changed(self, index):
        is_blueprint_mode = (index == 1)
        self.blueprint_combo.setVisible(is_blueprint_mode)
        for i in range(self.tree_btn_layout.count()):
            widget = self.tree_btn_layout.itemAt(i).widget()
            if widget: widget.setVisible(not is_blueprint_mode)
        self._populate_tree()

    def _load_blueprint_dropdown(self):
        self.blueprint_combo.blockSignals(True)
        self.blueprint_combo.clear()
        self._all_blueprints_cache = {}

        for item in self.prompt_service.list_blueprints():
            self._all_blueprints_cache[item["label"]] = item["blueprint"]

        if self._all_blueprints_cache:
            self.blueprint_combo.addItems(sorted(self._all_blueprints_cache.keys()))
        else:
            self.blueprint_combo.addItem("No blueprints available.")

        self.blueprint_combo.blockSignals(False)

    def _populate_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        if self.view_mode_combo.currentIndex() == 0:
            self._populate_category_tree()
        else:
            self._populate_blueprint_tree()

        self.tree.blockSignals(False)

        if self.current_prompt_key:
            items = self.tree.findItems(self.current_prompt_key, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)
            if items: self.tree.setCurrentItem(items[0])

    def _populate_category_tree(self):
        tracked_keys = set()
        if hasattr(self.prompt_manager, 'CATEGORIES'):
            for category, keys in self.prompt_manager.CATEGORIES.items():
                cat_item = QTreeWidgetItem(self.tree, [category])
                cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                cat_item.setExpanded(True)
                for key in keys:
                    prompt_item = QTreeWidgetItem(cat_item, [key])
                    prompt_item.setData(0, Qt.ItemDataRole.UserRole, key)
                    tracked_keys.add(key)

        prompts_dict = self._get_prompts_dict()
        custom_keys = [k for k in prompts_dict.keys() if k not in tracked_keys]
        if custom_keys:
            cat_item = QTreeWidgetItem(self.tree, ["🛠️ Custom Prompts"])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            cat_item.setExpanded(True)
            for key in custom_keys:
                prompt_item = QTreeWidgetItem(cat_item, [key])
                prompt_item.setData(0, Qt.ItemDataRole.UserRole, key)

    def _populate_blueprint_tree(self):
        bp_name = self.blueprint_combo.currentText()
        blueprint = self._all_blueprints_cache.get(bp_name)
        if not blueprint: return

        prompt_usage = self.prompt_service.get_blueprint_prompt_usage(blueprint)

        global_node = QTreeWidgetItem(self.tree, ["🌐 Master Runner (Global Context Injections)"])
        global_node.setFlags(global_node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        global_node.setExpanded(True)

        used_global_prompts = prompt_usage["global_prompts"]
        if used_global_prompts:
            for p_key in used_global_prompts:
                p_item = QTreeWidgetItem(global_node, [f"⚡ {p_key}"])
                p_item.setData(0, Qt.ItemDataRole.UserRole, p_key)
                p_item.setForeground(0, Qt.GlobalColor.cyan)
        else:
            empty_item = QTreeWidgetItem(global_node, ["No context injectors used by this blueprint"])
            empty_item.setFlags(empty_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

        for step_data in prompt_usage["steps"]:
            step_title = f"⚙️ Step: {step_data['step_id']} ({step_data['step_type']})"
            step_node = QTreeWidgetItem(self.tree, [step_title])
            step_node.setFlags(step_node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            step_node.setForeground(0, Qt.GlobalColor.lightGray)
            step_node.setExpanded(True)

            for p_key in step_data["explicit"]:
                prompt_node = QTreeWidgetItem(step_node, [f"📝 {p_key}"])
                prompt_node.setData(0, Qt.ItemDataRole.UserRole, p_key)

            for p_key in step_data["implicit"]:
                prompt_node = QTreeWidgetItem(step_node, [f"⚡ {p_key} (Auto-Injected)"])
                prompt_node.setData(0, Qt.ItemDataRole.UserRole, p_key)
                prompt_node.setForeground(0, Qt.GlobalColor.yellow)

    def _on_item_selected(self):
        selected = self.tree.selectedItems()
        if not selected: return
        prompt_key = selected[0].data(0, Qt.ItemDataRole.UserRole)
        if prompt_key:
            self.current_prompt_key = prompt_key
            self.lbl_current_prompt.setText(f"<b>Editing:</b> {prompt_key}")
            self.prompt_editor.setPlainText(self.prompt_manager.get_prompt(prompt_key))
            self.btn_delete.setEnabled(not self.prompt_service.is_core_prompt(prompt_key))

    def _on_new_prompt(self):
        name, ok = QInputDialog.getText(self, "New Custom Prompt", "Enter a unique name for this prompt:")
        if ok and name.strip():
            name = name.strip()
            if name in self.prompt_service.get_prompts_dict():
                QMessageBox.warning(self, "Conflict", f"A prompt named '{name}' already exists.")
                return

            # Use the bus to save
            from core.events.event_bus import EventBus
            EventBus.get_instance().prompt_action_requested.emit(PromptIntent.SAVE, PromptPayload(key=name, content="Enter your custom AI instructions here..."))

            self.current_prompt_key = name
            self._populate_tree()

    def _on_delete_prompt(self):
        if not self.current_prompt_key: return
        if self.prompt_service.is_core_prompt(self.current_prompt_key):
            QMessageBox.warning(self, "Restricted", "Core system prompts cannot be deleted. Use 'Restore Default' instead.")
            return

        if QMessageBox.question(self, "Confirm Deletion", f"Permanently delete '{self.current_prompt_key}'?") == QMessageBox.StandardButton.Yes:
            # Use the bus to delete
            from core.events.event_bus import EventBus
            EventBus.get_instance().prompt_action_requested.emit(PromptIntent.DELETE, PromptPayload(key=self.current_prompt_key))

            self.current_prompt_key = None
            self.lbl_current_prompt.setText("<b>No Prompt Selected</b>")
            self.prompt_editor.clear()
            self._populate_tree()

    def _on_restore_default(self):
        if not self.current_prompt_key: return
        if QMessageBox.question(self, "Confirm Restore", f"Revert '{self.current_prompt_key}' to default?") == QMessageBox.StandardButton.Yes:
            # Use the bus to restore
            from core.events.event_bus import EventBus
            EventBus.get_instance().prompt_action_requested.emit(PromptIntent.RESTORE, PromptPayload(key=self.current_prompt_key))
            self.prompt_editor.setPlainText(self.prompt_manager.get_prompt(self.current_prompt_key))

    def _on_save(self):
        if not self.current_prompt_key:
            QMessageBox.warning(self, "No Selection", "Select a prompt from the panel to save.")
            return

        # Use the bus to save
        from core.events.event_bus import EventBus
        EventBus.get_instance().prompt_action_requested.emit(PromptIntent.SAVE, PromptPayload(key=self.current_prompt_key, content=self.prompt_editor.toPlainText().strip()))

        msg = QMessageBox(self)
        msg.setWindowTitle("Success")
        msg.setText(f"'{self.current_prompt_key}' was saved successfully.")
        msg.setStyleSheet(f"background-color: {self.theme.get('bg_panel')}; color: {self.theme.get('text_main')};")
        msg.exec()
