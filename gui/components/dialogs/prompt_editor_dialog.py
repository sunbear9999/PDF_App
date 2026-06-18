from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QSplitter,
    QWidget, QInputDialog, QSizePolicy, QComboBox
)
from PySide6.QtCore import Qt, QTimer
from core.events.domains.metadata_events import PromptIntent, PromptPayload
class PromptEditorDialog(QDialog):
    def __init__(self, prompt_manager, parent=None, trace_id=None, trace_record=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
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
        self.view_mode_combo.addItems(["📂 View by Category", "🗺️ View by Blueprint Pipeline", "📊 View by Analysis Mode"])
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
        self._blueprint_ids_by_label = {}
        self.trace_record = trace_record
        self.trace_id = trace_id

        self._apply_theme()
        self._load_blueprint_dropdown()
        self._populate_tree()
        if self.trace_id or self.trace_record:
            QTimer.singleShot(0, self.open_trace)

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
        self._blueprint_ids_by_label = {}

        for item in self.prompt_service.list_blueprints():
            self._all_blueprints_cache[item["label"]] = item["blueprint"]
            self._blueprint_ids_by_label[item["label"]] = item.get("id")

        if self._all_blueprints_cache:
            for label in sorted(self._all_blueprints_cache.keys()):
                self.blueprint_combo.addItem(label, self._blueprint_ids_by_label.get(label))
        else:
            self.blueprint_combo.addItem("No blueprints available.")

        self.blueprint_combo.blockSignals(False)

    def _populate_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        if self.view_mode_combo.currentIndex() == 0:
            self._populate_category_tree()
        elif self.view_mode_combo.currentIndex() == 1:
            self._populate_blueprint_tree()
        else:
            self._populate_analysis_mode_tree()

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

        self._populate_run_trace_tree()

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

    def _populate_run_trace_tree(self):
        trace = self._trace_as_dict()
        if not trace:
            return
        trace_node = QTreeWidgetItem(self.tree, [f"Run Trace: {trace.get('blueprint_name') or 'AI Action'}"])
        trace_node.setFlags(trace_node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        trace_node.setExpanded(True)
        meta = QTreeWidgetItem(trace_node, ["Run metadata"])
        meta.setData(0, Qt.ItemDataRole.UserRole, {
            "preview": True,
            "title": "Run metadata",
            "content": self._format_trace_metadata(trace),
        })
        meta.setForeground(0, Qt.GlobalColor.cyan)

        calls = trace.get("calls") or []
        if not calls:
            empty = QTreeWidgetItem(trace_node, ["No LLM calls were recorded for this run"])
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            return

        for idx, call in enumerate(calls, 1):
            call_node = QTreeWidgetItem(trace_node, [
                f"LLM Call {idx}: {call.get('step_id', 'Unknown Step')} ({call.get('model', 'model unknown')})"
            ])
            call_node.setFlags(call_node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            call_node.setExpanded(idx == 1)

            system_item = QTreeWidgetItem(call_node, ["Exact rendered system prompt"])
            system_item.setData(0, Qt.ItemDataRole.UserRole, {
                "preview": True,
                "title": f"{call.get('step_id', 'Step')} system prompt",
                "content": call.get("system_prompt", ""),
            })
            system_item.setForeground(0, Qt.GlobalColor.cyan)

            query_item = QTreeWidgetItem(call_node, ["Exact user/query prompt"])
            query_item.setData(0, Qt.ItemDataRole.UserRole, {
                "preview": True,
                "title": f"{call.get('step_id', 'Step')} query",
                "content": call.get("query", ""),
            })
            query_item.setForeground(0, Qt.GlobalColor.cyan)

            details_item = QTreeWidgetItem(call_node, ["Prompt keys, injections, and options"])
            details_item.setData(0, Qt.ItemDataRole.UserRole, {
                "preview": True,
                "title": f"{call.get('step_id', 'Step')} trace details",
                "content": self._format_trace_call_details(call),
            })
            details_item.setForeground(0, Qt.GlobalColor.cyan)
    
    def _populate_analysis_mode_tree(self):
        project_manager = getattr(self.parent(), "project_manager", None)
        templates = project_manager.get_analysis_templates() if project_manager and hasattr(project_manager, "get_analysis_templates") else []
        usage_rows = self.prompt_service.get_analysis_template_prompt_usage(templates)
        if not usage_rows:
            empty = QTreeWidgetItem(self.tree, ["No analysis modes found"])
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            return

        for row in usage_rows:
            mode_node = QTreeWidgetItem(self.tree, [f"📊 {row['title']} via {row.get('blueprint', 'Document Analysis blueprint')}"])
            mode_node.setFlags(mode_node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            mode_node.setExpanded(True)

            pipeline_node = QTreeWidgetItem(mode_node, ["Blueprint pipeline: analysis_contract -> chunk_document -> quote selection per section -> compact numbered quotes -> synthesis pass -> final graph builder -> finalize_analysis"])
            pipeline_node.setFlags(pipeline_node.flags() & ~Qt.ItemFlag.ItemIsSelectable)

            editable_node = QTreeWidgetItem(mode_node, ["Editable shared PromptManager keys used by this mode"])
            editable_node.setFlags(editable_node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            editable_node.setExpanded(True)
            for prompt_key in dict.fromkeys(row["prompts"]):
                prompt_item = QTreeWidgetItem(editable_node, [prompt_key])
                prompt_item.setData(0, Qt.ItemDataRole.UserRole, prompt_key)

            rendered_node = QTreeWidgetItem(mode_node, ["Rendered LLM prompts/previews"])
            rendered_node.setFlags(rendered_node.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            rendered_node.setExpanded(True)
            for preview in row.get("rendered", []):
                title = preview.get("title") or "Rendered prompt"
                prompt_item = QTreeWidgetItem(rendered_node, [title])
                prompt_item.setData(0, Qt.ItemDataRole.UserRole, {
                    "preview": True,
                    "title": title,
                    "content": preview.get("content", ""),
                })
                prompt_item.setForeground(0, Qt.GlobalColor.cyan)

            detail = QTreeWidgetItem(mode_node, [f"Nodes: {', '.join(row['node_types']) or 'registry defaults'}"])
            detail.setFlags(detail.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            detail = QTreeWidgetItem(mode_node, [f"Relations: {', '.join(row['relation_types']) or 'registry defaults'}"])
            detail.setFlags(detail.flags() & ~Qt.ItemFlag.ItemIsSelectable)

    def _on_item_selected(self):
        selected = self.tree.selectedItems()
        if not selected: return
        prompt_key = selected[0].data(0, Qt.ItemDataRole.UserRole)
        if isinstance(prompt_key, dict) and prompt_key.get("preview"):
            self.current_prompt_key = None
            self.lbl_current_prompt.setText(f"<b>Preview:</b> {prompt_key.get('title', 'Rendered prompt')}")
            self.prompt_editor.setPlainText(prompt_key.get("content", ""))
            self.prompt_editor.setReadOnly(True)
            self.btn_delete.setEnabled(False)
            self.save_button.setEnabled(False)
            self.restore_button.setEnabled(False)
        elif prompt_key:
            self.current_prompt_key = prompt_key
            self.lbl_current_prompt.setText(f"<b>Editing:</b> {prompt_key}")
            self.prompt_editor.setReadOnly(False)
            self.prompt_editor.setPlainText(self.prompt_manager.get_prompt(prompt_key))
            self.btn_delete.setEnabled(not self.prompt_service.is_core_prompt(prompt_key))
            self.save_button.setEnabled(True)
            self.restore_button.setEnabled(True)

    def open_trace(self):
        trace = self._trace_as_dict()
        if not trace and self.trace_id:
            pm = getattr(self.parent(), "project_manager", None)
            if pm and hasattr(pm, "get_prompt_trace"):
                trace = pm.get_prompt_trace(self.trace_id)
        if not trace:
            return
        self.trace_record = trace
        self.trace_id = trace.get("trace_id", self.trace_id)
        self.view_mode_combo.setCurrentIndex(1)
        self._select_trace_blueprint(trace)
        self._populate_tree()
        self._select_first_trace_preview()

    def _trace_as_dict(self):
        if not self.trace_record:
            return None
        if isinstance(self.trace_record, dict):
            return self.trace_record
        if hasattr(self.trace_record, "as_dict"):
            return self.trace_record.as_dict()
        return None

    def _select_trace_blueprint(self, trace):
        blueprint_id = trace.get("blueprint_id")
        blueprint_name = trace.get("blueprint_name")
        for idx in range(self.blueprint_combo.count()):
            if blueprint_id and self.blueprint_combo.itemData(idx) == blueprint_id:
                self.blueprint_combo.setCurrentIndex(idx)
                return
        if blueprint_name:
            for idx in range(self.blueprint_combo.count()):
                label = self.blueprint_combo.itemText(idx)
                if blueprint_name == label or blueprint_name in label or label in blueprint_name:
                    self.blueprint_combo.setCurrentIndex(idx)
                    return

    def _select_first_trace_preview(self):
        root = self.tree.topLevelItem(0)
        if not root or not root.text(0).startswith("Run Trace"):
            return
        if root.childCount() > 1:
            call_node = root.child(1)
            if call_node and call_node.childCount():
                self.tree.setCurrentItem(call_node.child(0))
                return
        if root.childCount():
            self.tree.setCurrentItem(root.child(0))

    def _format_trace_metadata(self, trace):
        return (
            f"Trace ID: {trace.get('trace_id')}\n"
            f"Job ID: {trace.get('job_id')}\n"
            f"Blueprint ID: {trace.get('blueprint_id')}\n"
            f"Blueprint Name: {trace.get('blueprint_name')}\n"
            f"Target: {trace.get('target_id')}\n"
            f"Created: {trace.get('created_at')}\n"
            f"LLM Calls: {len(trace.get('calls') or [])}"
        )

    def _format_trace_call_details(self, call):
        import json
        return (
            f"Step ID: {call.get('step_id')}\n"
            f"Step Type: {call.get('step_type')}\n"
            f"Model: {call.get('model')}\n"
            f"Prompt Key: {call.get('prompt_key')}\n"
            f"Explicit Prompt Keys: {', '.join(call.get('explicit_prompt_keys') or []) or '(none)'}\n"
            f"Implicit Prompt Keys: {', '.join(call.get('implicit_prompt_keys') or []) or '(none)'}\n"
            f"Timestamp: {call.get('timestamp')}\n\n"
            f"LLM Options:\n{json.dumps(call.get('llm_options') or {}, indent=2)}"
        )

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
