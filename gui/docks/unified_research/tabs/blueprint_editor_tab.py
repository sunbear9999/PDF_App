import json
import re
import dataclasses
import uuid
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QComboBox, QFrame, QTextEdit, QLineEdit, QDialog,
                             QInputDialog, QStackedWidget, QSplitter, QTabWidget, QMessageBox)
from PySide6.QtCore import Qt
from gui.managers.dialog_manager import exec_as_modal, get_for_widget
from PySide6.QtGui import QCursor

from core.engine.action_model import AIActionBlueprint
from core.engine.default_blueprints import DefaultBlueprints
from core.events.event_bus import EventBus
from core.events.domains.workflow_events import WorkflowEvent, WorkflowIntent, WorkflowPayload
from gui.docks.unified_research.components.workflow_editor_canvas import VisualWorkflowEditor


class BlueprintHelpDialog(QDialog):
    """A comprehensive help and API documentation dialog for the Workflow Builder."""
    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("Blueprint Architect Documentation")
        self.resize(850, 650)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {self.theme.get('bg_main', '#1e1e1e')}; color: {self.theme.get('text_main', '#fff')}; }}
            QTabWidget::pane {{ border: 1px solid {self.theme.get('border', '#444')}; }}
            QTabBar::tab {{ background: {self.theme.get('bg_panel', '#333')}; color: #aaa; padding: 8px 16px; border: 1px solid {self.theme.get('border', '#444')}; border-bottom: none; }}
            QTabBar::tab:selected {{ background: {self.theme.get('bg_input', '#2b2b2b')}; color: #fff; font-weight: bold; border-top: 2px solid {self.theme.get('accent', '#b366ff')}; }}
            QTextEdit {{ background-color: {self.theme.get('bg_input', '#252525')}; border: none; font-size: 13px; line-height: 1.4; }}
        """)

        tabs = QTabWidget()
        
        # 1. Overview Tab
        tab_overview = QTextEdit()
        tab_overview.setReadOnly(True)
        tab_overview.setHtml("""
            <h2 style='color:#b366ff;'>Welcome to the Blueprint Builder</h2>
            <p>Blueprints are powerful, multi-step AI pipelines. You can chain together LLM queries, RAG searches, and Python scripts to automate complex research tasks.</p>
            <h3>Node Types:</h3>
            <ul>
                <li><b>LLM Query:</b> Prompts the AI. Can enforce JSON schemas.</li>
                <li><b>RAG Search:</b> Searches your indexed PDFs and returns text context.</li>
                <li><b>Python Script:</b> Runs local code to transform data or calculate metrics.</li>
                <li><b>Branch / Condition:</b> Routes the workflow based on variables.</li>
                <li><b>For Each:</b> Runs a sub-pipeline on a list of items.</li>
            </ul>
        """)
        tabs.addTab(tab_overview, "Overview")

        # 2. Variables & Context Tab
        tab_vars = QTextEdit()
        tab_vars.setReadOnly(True)
        tab_vars.setHtml("""
            <h2 style='color:#b366ff;'>State Variables</h2>
            <p>Every blueprint shares a <code>state</code> dictionary. When a node finishes, it saves its output to the key specified in <b>"Save Result As"</b>.</p>
            <p>You can inject these variables into subsequent prompts using brackets: <code>{variable_name}</code></p>
            <h3>Built-in Variables:</h3>
            <ul>
                <li><code>{user_input}</code> - The text the user entered when launching the tool.</li>
                <li><code>{doc_path}</code> - The currently active document.</li>
                <li><code>{project_manifest}</code> - The JSON project strategy document.</li>
                <li><code>{workspace_data}</code> - The current state of the visual argument graph.</li>
                <li><code>{selected_model}</code> - The LLM currently selected by the user.</li>
            </ul>
        """)
        tabs.addTab(tab_vars, "State Variables")

        # 3. Python API Tab
        tab_python = QTextEdit()
        tab_python.setReadOnly(True)
        tab_python.setHtml("""
            <h2 style='color:#00cc66;'>Python Script Environment</h2>
            <p>Python nodes run in a secure local execution environment. They are perfect for transforming data between LLM steps.</p>
            
            <h3>The Rules:</h3>
            <ol>
                <li>You have access to the global <code>state</code> dictionary.</li>
                <li>You MUST assign your final output to a variable named <code>result</code>.</li>
            </ol>
            
            <pre style='background:#111; padding:10px; border-radius:4px;'>
import json

# 1. Read from previous steps
ai_output = state.get("llm_raw_data", "{}")

# 2. Transform
data = json.loads(ai_output)
count = len(data.get("items", []))

# 3. Save to output
result = f"Found {count} items."
            </pre>

            <h3 style='color:#00cc66;'>Exposed APIs:</h3>
            <p>You have access to <code>analysis_api</code>, which connects directly to the Ontology Engine.</p>
            <ul>
                <li><code>analysis_api.build_contract(template_dict)</code> - Generates strict LLM schemas.</li>
                <li><code>analysis_api.normalize_graph_object(raw_json, prefix, contract)</code> - Cleans, repairs, and deduplicates raw LLM graph JSON.</li>
            </ul>
        """)
        tabs.addTab(tab_python, "Python API")

        # 4. Routing & UI Tab
        tab_ui = QTextEdit()
        tab_ui.setReadOnly(True)
        tab_ui.setHtml("""
            <h2 style='color:#3399ff;'>UI Targets & Formatting</h2>
            <p>The final step of your blueprint dictates how the user sees the data.</p>
            <h3>Formats:</h3>
            <ul>
                <li><b>Live Stream:</b> Types out text dynamically like ChatGPT.</li>
                <li><b>Data Table:</b> Renders an array of JSON objects as a spreadsheet.</li>
                <li><b>Card Grid:</b> Renders an array of JSON objects as visual cards.</li>
                <li><b>Workspace Graph:</b> Automatically parses nodes/edges and sends them to the spatial canvas.</li>
            </ul>
            <h3>Targets:</h3>
            <ul>
                <li><b>Floating Overlay:</b> Pops up over the main app.</li>
                <li><b>Chat Tab / Custom Tools Tab:</b> Injects the result into the sidebar.</li>
            </ul>
        """)
        tabs.addTab(tab_ui, "UI & Routing")

        layout.addWidget(tabs)
        
        btn_close = QPushButton("Close Documentation")
        btn_close.clicked.connect(self.accept)
        btn_close.setStyleSheet(f"background-color: {self.theme.get('bg_panel', '#333')}; padding: 8px; border-radius: 4px;")
        layout.addWidget(btn_close)


class BlueprintEditorTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = self.main_window.theme_manager.get_theme() if hasattr(main_window, 'theme_manager') else {}
        self.bpm = getattr(self.main_window, 'blueprint_manager', None)
        self.blueprint_registry = getattr(self.main_window, 'blueprint_registry', None)
        self.step_manager = getattr(self.main_window, 'step_manager', None)
        
        self.current_blueprint = None
        self.bus = EventBus.get_instance()
        self._workflow_requests = {}
        self.bus.workflow_state_changed.connect(self._handle_workflow_state)
        
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # --- Top Bar ---
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("<b>Target Blueprint:</b>"))
        
        self.combo_blueprints = QComboBox()
        self.combo_blueprints.currentIndexChanged.connect(self._load_selected_blueprint)
        top_bar.addWidget(self.combo_blueprints, 1)
        
        self.btn_restore = QPushButton("🔄 Restore Default")
        self.btn_restore.clicked.connect(self._restore_default)
        self.btn_restore.hide() 
        top_bar.addWidget(self.btn_restore)
        
        self.btn_delete = QPushButton("🗑️ Delete")
        self.btn_delete.clicked.connect(self._delete_tool)
        self.btn_delete.hide() 
        top_bar.addWidget(self.btn_delete)
        
        btn_create = QPushButton("✨ New Tool")
        btn_create.setStyleSheet(f"background-color: {self.theme.get('success', '#00cc66')}; color: white; font-weight: bold; border-radius: 4px; padding: 4px 8px;")
        btn_create.clicked.connect(self._create_new_tool)
        top_bar.addWidget(btn_create)

        btn_help = QPushButton("❓ Docs & Help")
        btn_help.setStyleSheet(f"background-color: {self.theme.get('bg_panel', '#333')}; color: white; border-radius: 4px; padding: 4px 8px;")
        btn_help.clicked.connect(self._show_help)
        top_bar.addWidget(btn_help)
        
        layout.addLayout(top_bar)

        # --- Meta Layout ---
        meta_layout = QHBoxLayout()
        self.input_bp_name = QLineEdit()
        self.input_bp_desc = QLineEdit()
        meta_layout.addWidget(QLabel("Name:"))
        meta_layout.addWidget(self.input_bp_name, 1)
        meta_layout.addWidget(QLabel("Desc:"))
        meta_layout.addWidget(self.input_bp_desc, 2)
        layout.addLayout(meta_layout)

        # --- Splitter (Canvas + Assistants) ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.visual_editor = VisualWorkflowEditor(
            self.theme,
            node_type_registry=getattr(self.main_window, "workflow_node_type_registry", None),
            step_manager=self.step_manager,
            parent=self,
        )
        
        self.right_tabs = QTabWidget()
        self.right_tabs.setStyleSheet(f"QTabWidget::pane {{ border: 1px solid {self.theme.get('border', '#444')}; background-color: #111; }} QTabBar::tab {{ background: {self.theme.get('bg_panel', '#333')}; color: white; padding: 8px; }} QTabBar::tab:selected {{ background: {self.theme.get('accent', '#b366ff')}; }}")
        
        # 1. AI Builder Assistant
        self.assistant_tab = QWidget()
        ast_lyt = QVBoxLayout(self.assistant_tab)
        self.txt_ast_chat = QTextEdit()
        self.txt_ast_chat.setReadOnly(True)
        self.txt_ast_chat.setStyleSheet("background: transparent; border: none; color: white;")
        self.txt_ast_chat.append("<i>Hello! I am the Papyrus Blueprint Architect. Describe what you want your new tool to do, and I will build the JSON pipeline for you automatically!</i><br>")
        ast_lyt.addWidget(self.txt_ast_chat)
        
        ast_input_lyt = QHBoxLayout()
        self.input_ast = QLineEdit()
        self.input_ast.setPlaceholderText("e.g., 'Make a tool that searches for a topic...'")
        self.input_ast.returnPressed.connect(self._send_chat)
        self.btn_ast_send = QPushButton("Send")
        self.btn_ast_send.clicked.connect(self._send_chat)
        ast_input_lyt.addWidget(self.input_ast)
        ast_input_lyt.addWidget(self.btn_ast_send)
        ast_lyt.addLayout(ast_input_lyt)
        self.right_tabs.addTab(self.assistant_tab, "🤖 AI Builder")

        # 2. Debugger
        self.debugger_tab = QWidget()
        dbg_lyt = QVBoxLayout(self.debugger_tab)
        self.btn_test_run = QPushButton("▶ Run Test in Debugger")
        self.btn_test_run.setStyleSheet(f"background-color: {self.theme.get('accent','#b366ff')}; color: white; padding: 5px;")
        self.btn_test_run.clicked.connect(self._run_debugger)
        dbg_lyt.addWidget(self.btn_test_run)
        self.txt_debugger = QTextEdit()
        self.txt_debugger.setReadOnly(True)
        self.txt_debugger.setStyleSheet("color: #00ff00; font-family: monospace; background: transparent; border: none;")
        dbg_lyt.addWidget(self.txt_debugger)
        self.right_tabs.addTab(self.debugger_tab, "⚡ Debugger")

        self.main_splitter.addWidget(self.visual_editor)
        self.main_splitter.addWidget(self.right_tabs)
        self.main_splitter.setSizes([700, 400]) 
        
        layout.addWidget(self.main_splitter, 1)

        # --- Bottom Bar ---
        bottom_bar = QHBoxLayout()
        self.btn_add_step = QPushButton("➕ Add Node")
        self.btn_add_step.clicked.connect(lambda: self.visual_editor.add_step("LLM_QUERY"))
        self.btn_connect = QPushButton("🔗 Auto-Link Selected")
        self.btn_connect.clicked.connect(lambda: self.visual_editor.connect_selected())
        self.btn_save = QPushButton("💾 Save Blueprint")
        self.btn_save.clicked.connect(self._save_blueprints)
        
        bottom_bar.addWidget(self.btn_add_step)
        bottom_bar.addWidget(self.btn_connect)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.btn_save)
        layout.addLayout(bottom_bar)

        self.update_theme(self.theme)
        if self.bpm: self._populate_combo_box()

    def _show_help(self):
        dlg = BlueprintHelpDialog(self.theme, self)
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dlg)
        else:
            exec_as_modal(dlg)

    def _delete_tool(self):
        key = self._current_blueprint_key()
        if not self.bpm or not key or key not in self.bpm.blueprints:
            return
        
        reply = QMessageBox.question(self, 'Delete Tool', f"Are you sure you want to permanently delete the custom tool '{key}'?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.combo_blueprints.blockSignals(True)
            if key in self.bpm.blueprints:
                del self.bpm.blueprints[key]
            
            self.current_blueprint = None 
            self._save_blueprints()
            self._populate_combo_box()
            self.combo_blueprints.blockSignals(False)
            self._load_selected_blueprint()

    def _restore_default(self):
        key = self._current_blueprint_key()
        if not self.bpm or not key or key not in self.bpm.blueprints or not self._registry_definition(key):
            return
        
        reply = QMessageBox.question(self, 'Restore Default', f"Are you sure you want to restore '{key}' to its default factory settings?\n\nThis will permanently overwrite any changes you made.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.combo_blueprints.blockSignals(True)
            if key in self.bpm.blueprints:
                del self.bpm.blueprints[key]
                
            self.current_blueprint = None 
            self._save_blueprints()
            self._populate_combo_box()
            self._set_current_blueprint_key(key)
            self.combo_blueprints.blockSignals(False)
            self._load_selected_blueprint()

    def _send_chat(self):
        user_text = self.input_ast.text().strip()
        if not user_text: return
        self.input_ast.clear()
        self.btn_ast_send.setEnabled(False)
        self.txt_ast_chat.append(f"<br><b>You:</b> {user_text}<br><b>Architect:</b> ")
        
        if self.current_blueprint:
            self.current_blueprint = self.visual_editor.to_blueprint(
                self.input_bp_name.text().strip() or self.current_blueprint.name,
                self.input_bp_desc.text().strip(),
            )
            current_json = json.dumps(dataclasses.asdict(self.current_blueprint), indent=2)
        else:
            current_json = "{}"

        architect_bp = DefaultBlueprints.get_blueprint_architect(self.main_window.prompt_manager)
        state = {"user_text": user_text, "current_json": current_json}
        
        request_id = str(uuid.uuid4())
        self._workflow_requests[request_id] = {"kind": "architect"}
        self.bus.workflow_action_requested.emit(
            WorkflowIntent.RUN_BLUEPRINT,
            WorkflowPayload(
                blueprint=architect_bp,
                initial_state=state,
                job_id=request_id,
                job_name=architect_bp.name,
            ),
        )

    def _on_chat_complete(self, final_state):
        self.btn_ast_send.setEnabled(True)
        full_text = final_state.get("architect_response", "")
        
        pattern = r'`' * 3 + r'(?:json)?\s*(\{.*?\})\s*' + r'`' * 3
        match = re.search(pattern, full_text, re.DOTALL)
        
        if match:
            json_str = match.group(1).strip()
            try:
                json_str = re.sub(r',\s*}', '}', json_str)
                json_str = re.sub(r',\s*\]', ']', json_str)
                data = json.loads(json_str)
                
                if "expected_inputs" in data and isinstance(data["expected_inputs"], list):
                    for inp in data["expected_inputs"]:
                        if "name" in inp and "key" not in inp: inp["key"] = inp.pop("name")
                            
                new_bp = AIActionBlueprint.from_dict(data)
                
                if self.bpm:
                    self.current_blueprint = None 
                    self.bpm.blueprints[new_bp.name] = new_bp
                    self._populate_combo_box() 
                    self.combo_blueprints.setCurrentText(new_bp.name)
                    self._save_blueprints()
                    self.txt_ast_chat.append("<br><br><b style='color:#00cc66;'>✅ Successfully applied and saved the new blueprint!</b>")
            except Exception as e:
                self.txt_ast_chat.append(f"<br><br><b style='color:#ff4444;'>❌ Failed to parse JSON blueprint: {e}</b>")

    def _populate_combo_box(self):
        current_key = self._current_blueprint_key()
        self.combo_blueprints.blockSignals(True)
        self.combo_blueprints.clear()

        seen = set()
        if self.blueprint_registry:
            for definition in self.blueprint_registry.all():
                self.combo_blueprints.addItem(definition.label or definition.id, definition.id)
                seen.add(definition.id)
        if self.bpm:
            for key, blueprint in self.bpm.blueprints.items():
                if key in seen: continue
                self.combo_blueprints.addItem(blueprint.name or key, key)
                seen.add(key)

        self.combo_blueprints.blockSignals(False)
        if current_key and current_key in [self.combo_blueprints.itemData(i) for i in range(self.combo_blueprints.count())]:
            self._set_current_blueprint_key(current_key)
        else:
            self._load_selected_blueprint()

    def _create_new_tool(self):
        name, ok = QInputDialog.getText(self, "New Tool", "Enter a name for your custom tool:")
        if ok and name and name not in self.bpm.blueprints:
            new_bp = DefaultBlueprints.get_blank_custom_tool(name)
            self.bpm.blueprints[name] = new_bp
            self._populate_combo_box()
            self._set_current_blueprint_key(name)

    def _load_selected_blueprint(self):
        if not self.bpm: return
        key = self._current_blueprint_key()
        if not key: return
        
        is_custom_override = key in self.bpm.blueprints
        is_registered_default = self._registry_definition(key) is not None
        self.btn_restore.setVisible(is_custom_override and is_registered_default)
        self.btn_delete.setVisible(is_custom_override and not is_registered_default)
        
        if self.current_blueprint:
            self.current_blueprint = self.visual_editor.to_blueprint(
                self.input_bp_name.text().strip() or self.current_blueprint.name,
                self.input_bp_desc.text().strip(),
            )
            self.bpm.blueprints[self.current_blueprint.name] = self.current_blueprint
            
        self.current_blueprint = self.bpm.get_blueprint(key, lambda: self._create_registered_blueprint(key))
        if not self.current_blueprint:
            self.current_blueprint = AIActionBlueprint(name=key, description="")
            
        self.current_blueprint.name = key 
        self.input_bp_name.setText(self.current_blueprint.name)
        self.input_bp_desc.setText(self.current_blueprint.description)
        
        self.visual_editor.load_blueprint(self.current_blueprint)

    def _current_blueprint_key(self):
        return self.combo_blueprints.currentData() or self.combo_blueprints.currentText()

    def _set_current_blueprint_key(self, key):
        for index in range(self.combo_blueprints.count()):
            if self.combo_blueprints.itemData(index) == key or self.combo_blueprints.itemText(index) == key:
                self.combo_blueprints.setCurrentIndex(index)
                return

    def _registry_definition(self, key):
        return self.blueprint_registry.get(key) if self.blueprint_registry else None

    def _create_registered_blueprint(self, key):
        if not self.blueprint_registry: return None
        return self.blueprint_registry.create(key, pm=getattr(self.main_window, "prompt_manager", None))

    def _save_blueprints(self):
        if not self.bpm: return
        if self.current_blueprint:
            self.current_blueprint = self.visual_editor.to_blueprint(
                self.input_bp_name.text().strip(),
                self.input_bp_desc.text().strip(),
            )
            self.bpm.blueprints[self.current_blueprint.name] = self.current_blueprint

        out_data = {k: dataclasses.asdict(v) for k, v in self.bpm.blueprints.items()}
        with open(self.bpm.blueprint_file, 'w', encoding='utf-8') as f:
            json.dump(out_data, f, indent=4)
        if hasattr(self.bpm, "_register_custom_blueprints"):
            self.bpm._register_custom_blueprints()
            
        custom_tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "CustomToolsTab"), None)
        if custom_tab and hasattr(custom_tab, 'refresh_tools'): custom_tab.refresh_tools()

    def _run_debugger(self):
        if not self.current_blueprint: return
        self.txt_debugger.clear()
        self.txt_debugger.append("<i>Initializing Test Run...</i>\n")
        
        self.current_blueprint = self.visual_editor.to_blueprint(
            self.input_bp_name.text().strip() or self.current_blueprint.name,
            self.input_bp_desc.text().strip(),
        )
            
        mock_state = {"user_input": "Test Input Data", "doc_path": "sample.pdf"}
        
        request_id = str(uuid.uuid4())
        self._workflow_requests[request_id] = {"kind": "debug"}
        self.bus.workflow_action_requested.emit(
            WorkflowIntent.RUN_BLUEPRINT,
            WorkflowPayload(
                blueprint=self.current_blueprint,
                initial_state=mock_state,
                job_id=request_id,
                job_name=self.current_blueprint.name,
            ),
        )

    def _handle_workflow_state(self, event, payload):
        request_id = payload.get("job_id") if hasattr(payload, "get") else None
        meta = self._workflow_requests.get(request_id)
        if not meta: return

        kind = meta.get("kind")
        if kind == "architect":
            if event == WorkflowEvent.PROGRESS:
                self.txt_ast_chat.insertPlainText((payload.get("data") or {}).get("chunk", ""))
            elif event == WorkflowEvent.COMPLETED:
                self._workflow_requests.pop(request_id, None)
                self._on_chat_complete(payload.get("initial_state") or {})
            elif event == WorkflowEvent.FAILED:
                self._workflow_requests.pop(request_id, None)
                self.btn_ast_send.setEnabled(True)
                self.txt_ast_chat.append(f"<br><b style='color:red;'>Error: {payload.get('errors')}</b>")
            return

        if kind == "debug":
            if event == WorkflowEvent.STATE_SNAPSHOT:
                data = payload.get("data") or {}
                self._on_debug_snapshot(data.get("step_id", ""), data.get("state_json", ""))
            elif event == WorkflowEvent.FAILED:
                self._workflow_requests.pop(request_id, None)
                self.txt_debugger.append(f"<span style='color:red;'>ERROR: {payload.get('errors')}</span>")
            elif event == WorkflowEvent.COMPLETED:
                self._workflow_requests.pop(request_id, None)
                self.txt_debugger.append("\n<b>[PIPELINE COMPLETE]</b>")

    def _on_debug_snapshot(self, step_id, state_json):
        self.txt_debugger.append(f"\n<b>--- STATE AFTER: {step_id} ---</b>")
        self.txt_debugger.append(state_json)

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")
        style = f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px;"
        
        self.combo_blueprints.setStyleSheet(style)
        self.input_bp_name.setStyleSheet(style)
        self.input_bp_desc.setStyleSheet(style)
        
        btn_action_style = f"background-color: {theme.get('bg_panel', '#333')}; font-weight: bold; color: white; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px 8px;"
        self.btn_restore.setStyleSheet(btn_action_style)
        self.btn_delete.setStyleSheet(btn_action_style)
        
        self.btn_save.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 4px; padding: 6px;")
        self.btn_add_step.setStyleSheet(style)
        self.btn_connect.setStyleSheet(style)
        if hasattr(self, "visual_editor"): self.visual_editor.update_theme(theme)
        
        self.input_ast.setStyleSheet(style)
        self.btn_ast_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; color: white; padding: 4px;")