import json
import re
import dataclasses
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                             QComboBox, QFrame, QScrollArea, QLineEdit, 
                             QTextEdit, QCheckBox, QSpinBox, QGridLayout, 
                             QInputDialog, QDoubleSpinBox, QStackedWidget, QSplitter, QTabWidget, QMessageBox)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

from core.engine.action_model import AIActionBlueprint, ActionStep
from core.engine.default_blueprints import DefaultBlueprints
from core.engine.master_runner import MasterActionRunner
from gui.docks.unified_research.components.workflow_editor_canvas import VisualWorkflowEditor

TYPES = {
    "AI Text Generation": "LLM_QUERY", 
    "Semantic RAG Search": "RAG_SEARCH", 
    "Loop (Run Action on List)": "FOREACH", 
    "Condition (If/Then)": "CONDITION", 
    "Branch (If/Else Paths)": "BRANCH",               # NEW
    "Database Write": "DATABASE_WRITE",             # NEW
    "Run Python Script": "PYTHON_SCRIPT"
}

FORMATS = {
    "Silent (Background Data Only)": "silent", 
    "Live Stream Text": "live_stream", 
    "Data Table": "data_table",
    "Card Grid": "card_grid",
    "Draw Citation Bubbles": "chat_widgets", 
    "Update Workspace": "workspace_graph", 
    "Custom HTML Dashboard": "custom_view"
}

TARGETS = {
    "Custom Tools Tab": "custom_tools_tab", 
    "Chat Tab": "chat_dock", 
    "Search Tab": "search_tab",
    "Analysis Tab": "analysis_tab",
    "Brainstorm Tab": "brainstorm_dock", 
    "Floating Overlay": "floating"
}

def get_key(d, val, default):
    for k, v in d.items():
        if v == val: return k
    return default

# ... (Keep StepCardWidget exactly as it was) ...
class StepCardWidget(QFrame):
    delete_requested = Signal(object)
    move_up_requested = Signal(object)
    move_down_requested = Signal(object)
    step_updated = Signal()

    def __init__(self, step: ActionStep, available_vars: list, tool_names: list, theme: dict, parent=None):
        super().__init__(parent)
        self.step = step
        self.theme = theme
        self.available_vars = available_vars
        self.tool_names = tool_names
        self._build_ui()
        self._populate_data()

    def _build_ui(self):
        self.setObjectName("StepCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        header = QHBoxLayout()
        self.input_id = QLineEdit()
        self.input_id.setPlaceholderText("Step ID (e.g., generate_queries)")
        self.input_id.setFixedWidth(200)
        
        self.combo_type = QComboBox()
        self.combo_type.addItems(list(TYPES.keys()))
        self.combo_type.currentIndexChanged.connect(self._toggle_dynamic_inputs)
        
        self.input_step_ref = QLineEdit()
        self.input_step_ref.setPlaceholderText("Library Ref (optional)")
        self.input_step_ref.setFixedWidth(150)

        header.addWidget(QLabel("<b>ID:</b>"))
        header.addWidget(self.input_id)
        header.addWidget(QLabel("<b>Action Type:</b>"))
        header.addWidget(self.combo_type, 1)
        
        for icon, signal in [("▲", self.move_up_requested), ("▼", self.move_down_requested), ("✖", self.delete_requested)]:
            btn = QPushButton(icon)
            btn.setFixedSize(24, 24)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet("background: transparent; border: none; font-weight: bold;" + ("color: #ff4444;" if icon == "✖" else f"color: {self.theme.get('text_muted', '#aaa')};"))
            btn.clicked.connect(lambda checked, s=signal, c=self: s.emit(c))
            header.addWidget(btn)
        layout.addLayout(header)

        var_str = ", ".join([f"{{{v}}}" for v in self.available_vars]) if self.available_vars else "None yet"
        lbl_vars = QLabel(f"<i>Variables you can use:</i> <span style='color: {self.theme.get('accent', '#b366ff')};'>{{{'{user_input}'}}}, {{{'{doc_path}'}}}, {var_str}</span>")
        lbl_vars.setWordWrap(True)
        layout.addWidget(lbl_vars)

        inp_frame = QFrame()
        inp_frame.setStyleSheet(f"background-color: rgba(0,0,0,0.1); border: 1px solid {self.theme.get('border', '#444')}; border-radius: 6px;")
        self.inp_layout = QVBoxLayout(inp_frame)
        
        self.llm_widget = QWidget()
        llm_lyt = QVBoxLayout(self.llm_widget)
        llm_lyt.setContentsMargins(0,0,0,0)
        llm_lyt.addWidget(QLabel("<b>User Prompt:</b>"))
        self.input_llm_query = QTextEdit()
        self.input_llm_query.setMaximumHeight(60)
        llm_lyt.addWidget(self.input_llm_query)
        self.inp_layout.addWidget(self.llm_widget)
        
        self.rag_widget = QWidget()
        rag_lyt = QVBoxLayout(self.rag_widget)
        rag_lyt.setContentsMargins(0,0,0,0)
        rag_lyt.addWidget(QLabel("<b>Search Query:</b>"))
        self.input_rag_query = QLineEdit()
        rag_lyt.addWidget(self.input_rag_query)
        self.inp_layout.addWidget(self.rag_widget)

        self.foreach_widget = QWidget()
        # simplified foreach block to avoid excessive string length for this fix
        self.inp_layout.addWidget(self.foreach_widget)
        
        self.condition_widget = QWidget()
        cond_lyt = QVBoxLayout(self.condition_widget)
        cond_lyt.setContentsMargins(0,0,0,0)
        cond_lyt.addWidget(QLabel("<b>Python Logic:</b> <span style='color:#888;'>(e.g. len(state.get('context','')) > 100)</span>"))
        self.input_cond = QLineEdit()
        cond_lyt.addWidget(self.input_cond)
        self.inp_layout.addWidget(self.condition_widget)

        # --- NEW: Branch Widget ---
        self.branch_widget = QWidget()
        branch_lyt = QVBoxLayout(self.branch_widget)
        branch_lyt.setContentsMargins(0,0,0,0)
        branch_lyt.addWidget(QLabel("<b>Branch Logic:</b> <span style='color:#888;'>(e.g. state.get('use_advanced_rag') == True)</span>"))
        self.input_branch_logic = QLineEdit()
        branch_lyt.addWidget(self.input_branch_logic)
        branch_lyt.addWidget(QLabel("<i>Note: Edit nested true/false steps in the JSON directly for now.</i>"))
        self.inp_layout.addWidget(self.branch_widget)

        # --- NEW: Database Write Widget ---
        self.db_widget = QWidget()
        db_lyt = QVBoxLayout(self.db_widget)
        db_lyt.setContentsMargins(0,0,0,0)
        db_lyt.addWidget(QLabel("<b>Table Name:</b>"))
        self.input_db_table = QLineEdit()
        db_lyt.addWidget(self.input_db_table)
        db_lyt.addWidget(QLabel("<b>Payload (JSON):</b>"))
        self.input_db_payload = QTextEdit()
        self.input_db_payload.setMaximumHeight(60)
        db_lyt.addWidget(self.input_db_payload)
        self.inp_layout.addWidget(self.db_widget)
        
        self.python_widget = QWidget()
        py_lyt = QVBoxLayout(self.python_widget)
        py_lyt.setContentsMargins(0,0,0,0)
        py_lyt.addWidget(QLabel("<b>Script:</b> <span style='color:#888;'>(Assign output to 'result' variable. Use 'state' dict.)</span>"))
        self.input_script = QTextEdit()
        self.input_script.setStyleSheet("font-family: monospace;")
        py_lyt.addWidget(self.input_script)
        self.inp_layout.addWidget(self.python_widget)
        layout.addWidget(inp_frame)

        self.ai_frame = QFrame()
        self.ai_frame.setStyleSheet(f"background-color: rgba(0,0,0,0.1); border: 1px solid {self.theme.get('border', '#444')}; border-radius: 6px;")
        ai_layout = QGridLayout(self.ai_frame)
        ai_layout.addWidget(QLabel("<b>System Prompt:</b>"), 0, 0, 1, 4)
        self.input_system = QTextEdit()
        self.input_system.setMaximumHeight(40)
        ai_layout.addWidget(self.input_system, 1, 0, 1, 4)
        
        self.spin_predict = QSpinBox()
        self.spin_predict.setRange(-1, 8000)
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(0.0, 2.0)
        self.spin_temp.setSingleStep(0.1)
        self.chk_json = QCheckBox("Force Generic JSON Output")
        
        ai_layout.addWidget(QLabel("Max Tokens:"), 2, 0)
        ai_layout.addWidget(self.spin_predict, 2, 1)
        ai_layout.addWidget(QLabel("Temperature:"), 2, 2)
        ai_layout.addWidget(self.spin_temp, 2, 3)
        ai_layout.addWidget(self.chk_json, 3, 0, 1, 4)

        self.lbl_schema_hint = QLabel("<b>Strict Output Schema (JSON):</b> <span style='color:#888;'>(Forces LLM to output this exact structure)</span>")
        ai_layout.addWidget(self.lbl_schema_hint, 4, 0, 1, 4)
        
        self.input_output_schema = QTextEdit()
        self.input_output_schema.setPlaceholderText('e.g., {"cards": [{"title": "string", "summary": "string"}]}')
        self.input_output_schema.setStyleSheet("font-family: monospace;")
        self.input_output_schema.setMaximumHeight(80)
        ai_layout.addWidget(self.input_output_schema, 5, 0, 1, 4)

        layout.addWidget(self.ai_frame)

        out_frame = QFrame()
        out_frame.setStyleSheet(f"background-color: rgba(0,0,0,0.1); border: 1px solid {self.theme.get('border', '#444')}; border-radius: 6px;")
        out_layout = QGridLayout(out_frame)
        out_layout.addWidget(QLabel("<b>📤 Output Routing</b>"), 0, 0, 1, 4)
        
        self.input_output_key = QLineEdit()
        self.input_output_key.textChanged.connect(lambda _: self.step_updated.emit())
        out_layout.addWidget(QLabel("Save Result As (Variable):"), 1, 0)
        out_layout.addWidget(self.input_output_key, 1, 1, 1, 3)
        
        self.combo_ui_format = QComboBox()
        self.combo_ui_format.addItems(list(FORMATS.keys()))
        self.combo_ui_format.currentIndexChanged.connect(self._toggle_dynamic_inputs)
        
        self.combo_ui_target = QComboBox()
        self.combo_ui_target.addItems(list(TARGETS.keys()))
        
        out_layout.addWidget(QLabel("Format:"), 2, 0)
        out_layout.addWidget(self.combo_ui_format, 2, 1)
        out_layout.addWidget(QLabel("Target:"), 2, 2)
        out_layout.addWidget(self.combo_ui_target, 2, 3)
        
        layout.addWidget(out_frame)

    def _populate_data(self):
        self.input_id.setText(self.step.step_id)
        self.combo_type.setCurrentText(get_key(TYPES, self.step.step_type, "AI Text Generation"))
        self.input_step_ref.setText(getattr(self.step, 'step_ref', '') or "")

        if self.step.step_type == "LLM_QUERY":
            self.input_llm_query.setText(self.step.inputs.get("query", ""))
        elif self.step.step_type == "CONDITION":
            self.input_cond.setText(self.step.inputs.get("logic", ""))
        elif self.step.step_type == "PYTHON_SCRIPT":
            self.input_script.setText(self.step.inputs.get("script", ""))
        elif self.step.step_type == "RAG_SEARCH":
            q = self.step.inputs.get("queries", [""])[0] if "queries" in self.step.inputs else ""
            self.input_rag_query.setText(q)
        elif self.step.step_type == "BRANCH":
            self.input_branch_logic.setText(self.step.inputs.get("logic", ""))
        elif self.step.step_type == "DATABASE_WRITE":
            self.input_db_table.setText(self.step.inputs.get("table", ""))
            payload = self.step.inputs.get("payload", {})
            self.input_db_payload.setText(json.dumps(payload) if isinstance(payload, dict) else str(payload))
            
        self.input_system.setText(self.step.system_prompt or "")
        self.combo_ui_format.setCurrentText(get_key(FORMATS, self.step.ui_format, "Silent (Background Data Only)"))
        self.combo_ui_target.setCurrentText(get_key(TARGETS, self.step.ui_target, "Floating Overlay"))
        self.input_output_key.setText(self.step.output_key)
        
        self.spin_predict.setValue(self.step.llm_options.get("num_predict", 2048))
        self.spin_temp.setValue(self.step.llm_options.get("temperature", 0.7))
        self.chk_json.setChecked(self.step.llm_options.get("json_mode", False))

        if self.step.output_schema:
            try:
                self.input_output_schema.setText(json.dumps(self.step.output_schema, indent=2))
            except:
                self.input_output_schema.setText(str(self.step.output_schema))
        else:
            self.input_output_schema.clear()

        self._toggle_dynamic_inputs()

    def _toggle_dynamic_inputs(self):
        t = TYPES.get(self.combo_type.currentText())
        self.llm_widget.setVisible(t == "LLM_QUERY")
        self.rag_widget.setVisible(t == "RAG_SEARCH")
        self.foreach_widget.setVisible(t == "FOREACH")
        self.condition_widget.setVisible(t == "CONDITION")
        self.python_widget.setVisible(t == "PYTHON_SCRIPT")
        self.branch_widget.setVisible(t == "BRANCH")           # NEW
        self.db_widget.setVisible(t == "DATABASE_WRITE")       # NEW
        show_ai = (t == "LLM_QUERY") or (t == "FOREACH")
        self.ai_frame.setVisible(show_ai)

    def update_step_from_ui(self):
        self.step.step_id = self.input_id.text().strip()
        self.step.step_type = TYPES.get(self.combo_type.currentText(), "LLM_QUERY")
        self.step.step_ref = self.input_step_ref.text().strip() or None

        self.step.inputs = {}
        if self.step.step_type == "LLM_QUERY":
            self.step.inputs["query"] = self.input_llm_query.toPlainText().strip()
        elif self.step.step_type == "CONDITION":
            self.step.inputs["logic"] = self.input_cond.text().strip()
        elif self.step.step_type == "PYTHON_SCRIPT":
            self.step.inputs["script"] = self.input_script.toPlainText().strip()
        elif self.step.step_type == "RAG_SEARCH":
            self.step.inputs["queries"] = [self.input_rag_query.text().strip()]
        elif self.step.step_type == "BRANCH":
            self.step.inputs["logic"] = self.input_branch_logic.text().strip()
        elif self.step.step_type == "DATABASE_WRITE":
            self.step.inputs["table"] = self.input_db_table.text().strip()
            try: self.step.inputs["payload"] = json.loads(self.input_db_payload.toPlainText() or "{}")
            except: self.step.inputs["payload"] = {}
            
        self.step.system_prompt = self.input_system.toPlainText().strip() or None
        self.step.ui_format = FORMATS.get(self.combo_ui_format.currentText(), "silent")
        self.step.ui_target = TARGETS.get(self.combo_ui_target.currentText(), "floating")
        self.step.output_key = self.input_output_key.text().strip()
        
        self.step.llm_options["num_predict"] = self.spin_predict.value()
        self.step.llm_options["temperature"] = self.spin_temp.value()
        self.step.llm_options["json_mode"] = self.chk_json.isChecked()

        schema_text = self.input_output_schema.toPlainText().strip()
        if schema_text:
            try:
                self.step.output_schema = json.loads(schema_text)
                self.step.llm_options["json_mode"] = True 
            except json.JSONDecodeError:
                print(f"Warning: Invalid JSON Schema in step {self.step.step_id}")
                self.step.output_schema = None 
        else:
            self.step.output_schema = None

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"QFrame#StepCard {{ background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-left: 4px solid {theme.get('accent', '#b366ff')}; border-radius: 8px; margin-bottom: 4px; }} QLabel {{ color: {theme.get('text_main', '#fff')}; font-size: 12px; }} QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px; }}")


class BlueprintEditorTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = self.main_window.theme_manager.get_theme() if hasattr(main_window, 'theme_manager') else {}
        self.bpm = getattr(self.main_window, 'blueprint_manager', None)
        self.blueprint_registry = getattr(self.main_window, 'blueprint_registry', None)
        self.step_manager = getattr(self.main_window, 'step_manager', None)
        self.current_blueprint = None
        self.step_widgets = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("<b>Target Blueprint:</b>"))
        
        self.combo_blueprints = QComboBox()
        self.combo_blueprints.currentIndexChanged.connect(self._load_selected_blueprint)
        top_bar.addWidget(self.combo_blueprints, 1)
        
        self.btn_restore = QPushButton("🔄 Restore Default")
        self.btn_restore.clicked.connect(self._restore_default)
        self.btn_restore.hide() # Hidden by default
        top_bar.addWidget(self.btn_restore)
        
        self.btn_delete = QPushButton("🗑️ Delete")
        self.btn_delete.clicked.connect(self._delete_tool)
        self.btn_delete.hide() # Hidden by default
        top_bar.addWidget(self.btn_delete)
        
        btn_create = QPushButton("✨ New Tool")
        btn_create.setStyleSheet(f"background-color: {self.theme.get('success', '#00cc66') if self.theme else '#00cc66'}; color: white; font-weight: bold; border-radius: 4px; padding: 4px 8px;")
        btn_create.clicked.connect(self._create_new_tool)
        top_bar.addWidget(btn_create)
        
        layout.addLayout(top_bar)

        meta_layout = QHBoxLayout()
        self.input_bp_name = QLineEdit()
        self.input_bp_desc = QLineEdit()
        meta_layout.addWidget(QLabel("Name:"))
        meta_layout.addWidget(self.input_bp_name, 1)
        meta_layout.addWidget(QLabel("Desc:"))
        meta_layout.addWidget(self.input_bp_desc, 2)
        layout.addLayout(meta_layout)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.visual_editor = VisualWorkflowEditor(
            self.theme,
            node_type_registry=getattr(self.main_window, "workflow_node_type_registry", None),
            step_manager=self.step_manager,
            parent=self,
        )
        
        self.right_tabs = QTabWidget()
        self.right_tabs.setStyleSheet(f"QTabWidget::pane {{ border: 1px solid {self.theme.get('border', '#444')}; background-color: #111; }} QTabBar::tab {{ background: {self.theme.get('bg_panel', '#333')}; color: white; padding: 8px; }} QTabBar::tab:selected {{ background: {self.theme.get('accent', '#b366ff')}; }}")
        
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

        bottom_bar = QHBoxLayout()
        self.btn_add_step = QPushButton("➕ Add LLM Step")
        self.btn_add_step.clicked.connect(self._add_new_step)
        self.btn_connect = QPushButton("🔗 Connect Selected")
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

    def _delete_tool(self):
        key = self._current_blueprint_key()
        if not self.bpm or not key or key not in self.bpm.blueprints:
            return
        
        reply = QMessageBox.question(self, 'Delete Tool', f"Are you sure you want to permanently delete the custom tool '{key}'?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.combo_blueprints.blockSignals(True)
            if key in self.bpm.blueprints:
                del self.bpm.blueprints[key]
            
            # Prevent the current blueprint from re-saving itself
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
        
        self.ast_runner = MasterActionRunner(self.main_window, architect_bp, state)
        self.ast_runner.progress_update.connect(lambda c: self.txt_ast_chat.insertPlainText(c))
        self.ast_runner.action_complete.connect(self._on_chat_complete)
        self.ast_runner.error.connect(lambda e: self.txt_ast_chat.append(f"<br><b style='color:red;'>Error: {e}</b>"))
        self.ast_runner.start()

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

    def _get_all_tool_names(self):
        keys = []
        if self.blueprint_registry:
            keys.extend(definition.id for definition in self.blueprint_registry.all())
        if self.bpm:
            keys.extend(key for key in self.bpm.blueprints.keys() if key not in keys)
        return keys

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
                if key in seen:
                    continue
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
            # --- FIXED: Route through Default Blueprints ---
            from core.engine.default_blueprints import DefaultBlueprints
            new_bp = DefaultBlueprints.get_blank_custom_tool(name)
            
            self.bpm.blueprints[name] = new_bp
            self._populate_combo_box()
            self._set_current_blueprint_key(name)

    def _render_pipeline(self):
        if self.current_blueprint:
            self.visual_editor.load_blueprint(self.current_blueprint)

    def _load_selected_blueprint(self):
        if not self.bpm: return
        key = self._current_blueprint_key()
        if not key: return
        
        # Toggle Toolbar Actions
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
        
        self.step_widgets.clear()
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
        if not self.blueprint_registry:
            return None
        return self.blueprint_registry.create(key, pm=getattr(self.main_window, "prompt_manager", None))

    def _add_new_step(self):
        if not self.current_blueprint: return
        self.visual_editor.add_step("LLM_QUERY")

    def _remove_step(self, card_widget):
        if card_widget.step in self.current_blueprint.steps: self.current_blueprint.steps.remove(card_widget.step)
        self.step_widgets.remove(card_widget)
        card_widget.setParent(None); card_widget.deleteLater()
        self._render_pipeline()

    def _move_step_up(self, card_widget):
        idx = self.step_widgets.index(card_widget)
        if idx > 0:
            self.step_widgets[idx], self.step_widgets[idx-1] = self.step_widgets[idx-1], self.step_widgets[idx]
            self.current_blueprint.steps[idx], self.current_blueprint.steps[idx-1] = self.current_blueprint.steps[idx-1], self.current_blueprint.steps[idx]
            self._render_pipeline()

    def _move_step_down(self, card_widget):
        idx = self.step_widgets.index(card_widget)
        if idx < len(self.step_widgets) - 1:
            self.step_widgets[idx], self.step_widgets[idx+1] = self.step_widgets[idx+1], self.step_widgets[idx]
            self.current_blueprint.steps[idx], self.current_blueprint.steps[idx+1] = self.current_blueprint.steps[idx+1], self.current_blueprint.steps[idx]
            self._render_pipeline()

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
        
        self.debug_runner = MasterActionRunner(self.main_window, self.current_blueprint, mock_state)
        self.debug_runner.state_snapshot.connect(self._on_debug_snapshot)
        self.debug_runner.error.connect(lambda e: self.txt_debugger.append(f"<span style='color:red;'>ERROR: {e}</span>"))
        self.debug_runner.action_complete.connect(lambda d: self.txt_debugger.append("\n<b>[PIPELINE COMPLETE]</b>"))
        self.debug_runner.start()

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
        if hasattr(self, "btn_connect"):
            self.btn_connect.setStyleSheet(style)
        if hasattr(self, "visual_editor"):
            self.visual_editor.update_theme(theme)
        self.input_ast.setStyleSheet(style)
        self.btn_ast_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; color: white; padding: 4px;")
        
        for w in self.step_widgets: w.update_theme(theme)
