# gui/docks/unified_research/tabs/blueprint_editor_tab.py
import json
import re
import dataclasses
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                             QComboBox, QFrame, QScrollArea, QLineEdit, 
                             QTextEdit, QCheckBox, QSpinBox, QMessageBox, QGridLayout, 
                             QInputDialog, QDoubleSpinBox, QStackedWidget, QSplitter, QTabWidget)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

from core.engine.action_model import AIActionBlueprint, ActionStep
from core.engine.default_blueprints import DefaultBlueprints
from core.engine.master_runner import MasterActionRunner

TYPES = {
    "AI Text Generation": "LLM_QUERY", 
    "Semantic RAG Search": "RAG_SEARCH", 
    "Loop (Run Action on List)": "FOREACH", 
    "Condition (If/Then)": "CONDITION", 
    "Run Python Script": "PYTHON_SCRIPT"
}

FORMATS = {
    "Silent (Background Data Only)": "silent", 
    "Live Stream Text": "live_stream", 
    "Draw Citation Bubbles": "chat_widgets", 
    "Popup Full Document": "static_document", 
    "Update Workspace": "workspace_graph", 
    "Custom HTML Dashboard": "custom_view"
}

TARGETS = {
    "Custom Tools Tab": "custom_tools_tab", 
    "Chat Tab": "chat_dock", 
    "Brainstorm Tab": "brainstorm_dock", 
    "Floating Overlay": "floating"
}

def get_key(d, val, default):
    for k, v in d.items():
        if v == val: return k
    return default

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
        foreach_lyt = QVBoxLayout(self.foreach_widget)
        foreach_lyt.setContentsMargins(0,0,0,0)
        foreach_lyt.addWidget(QLabel("<b>List Variable to Loop Over:</b>"))
        self.input_foreach_list = QLineEdit()
        self.input_foreach_list.setPlaceholderText("e.g. {search_queries}")
        foreach_lyt.addWidget(self.input_foreach_list)
        foreach_lyt.addWidget(QLabel("<b>Action per item:</b> <span style='color:#888;'>(The item is available as {item})</span>"))
        self.combo_foreach_mode = QComboBox()
        self.combo_foreach_mode.addItems(["Inline AI Prompt", "Inline RAG Search", "Run Custom Tool"])
        self.combo_foreach_mode.currentIndexChanged.connect(lambda idx: self.stack_foreach.setCurrentIndex(idx))
        foreach_lyt.addWidget(self.combo_foreach_mode)

        self.stack_foreach = QStackedWidget()
        w_ai = QWidget()
        l_ai = QVBoxLayout(w_ai)
        l_ai.setContentsMargins(0,0,0,0)
        self.input_fe_prompt = QTextEdit()
        self.input_fe_prompt.setPlaceholderText("Prompt using {item}...")
        self.input_fe_prompt.setMaximumHeight(50)
        l_ai.addWidget(self.input_fe_prompt)
        self.stack_foreach.addWidget(w_ai)
        
        w_rag = QWidget()
        l_rag = QVBoxLayout(w_rag)
        l_rag.setContentsMargins(0,0,0,0)
        self.input_fe_query = QLineEdit()
        self.input_fe_query.setPlaceholderText("Search query using {item}...")
        l_rag.addWidget(self.input_fe_query)
        self.stack_foreach.addWidget(w_rag)
        
        w_tool = QWidget()
        l_tool = QVBoxLayout(w_tool)
        l_tool.setContentsMargins(0,0,0,0)
        self.combo_fe_tool = QComboBox()
        self.combo_fe_tool.addItems(self.tool_names)
        l_tool.addWidget(self.combo_fe_tool)
        self.stack_foreach.addWidget(w_tool)
        foreach_lyt.addWidget(self.stack_foreach)
        self.inp_layout.addWidget(self.foreach_widget)
        
        self.condition_widget = QWidget()
        cond_lyt = QVBoxLayout(self.condition_widget)
        cond_lyt.setContentsMargins(0,0,0,0)
        cond_lyt.addWidget(QLabel("<b>Python Logic:</b> <span style='color:#888;'>(e.g. len(state.get('context','')) > 100)</span>"))
        self.input_cond = QLineEdit()
        cond_lyt.addWidget(self.input_cond)
        self.inp_layout.addWidget(self.condition_widget)

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
        self.spin_predict.setValue(2048)
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(0.0, 2.0)
        self.spin_temp.setSingleStep(0.1)
        self.spin_temp.setValue(0.7)
        self.chk_json = QCheckBox("Force JSON Output")
        
        ai_layout.addWidget(QLabel("Max Tokens:"), 2, 0)
        ai_layout.addWidget(self.spin_predict, 2, 1)
        ai_layout.addWidget(QLabel("Temperature:"), 2, 2)
        ai_layout.addWidget(self.spin_temp, 2, 3)
        ai_layout.addWidget(self.chk_json, 3, 0, 1, 4)
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
        
        self.input_html_template = QTextEdit()
        self.input_html_template.setPlaceholderText("HTML Template (Use {variables} to inject state)...")
        self.input_html_template.setMaximumHeight(80)
        out_layout.addWidget(self.input_html_template, 3, 0, 1, 4)
        layout.addWidget(out_frame)

    def _populate_data(self):
        self.input_id.setText(self.step.step_id)
        self.combo_type.setCurrentText(get_key(TYPES, self.step.step_type, "AI Text Generation"))
        
        if self.step.step_type == "LLM_QUERY":
            self.input_llm_query.setText(self.step.inputs.get("query", ""))
        elif self.step.step_type == "CONDITION":
            self.input_cond.setText(self.step.inputs.get("logic", ""))
        elif self.step.step_type == "PYTHON_SCRIPT":
            self.input_script.setText(self.step.inputs.get("script", ""))
        elif self.step.step_type == "FOREACH":
            self.input_foreach_list.setText(self.step.inputs.get("list", ""))
            itype = self.step.inputs.get("inline_type")
            if itype == "LLM_QUERY":
                self.combo_foreach_mode.setCurrentIndex(0)
                self.input_fe_prompt.setText(self.step.inputs.get("inline_prompt", ""))
            elif itype == "RAG_SEARCH":
                self.combo_foreach_mode.setCurrentIndex(1)
                self.input_fe_query.setText(self.step.inputs.get("inline_query", ""))
            else:
                self.combo_foreach_mode.setCurrentIndex(2)
                bp = self.step.inputs.get("sub_blueprint_name", "")
                if bp in self.tool_names: self.combo_fe_tool.setCurrentText(bp)
        else:
            q = self.step.inputs.get("queries", [""])[0] if "queries" in self.step.inputs else ""
            self.input_rag_query.setText(q)
            
        self.input_system.setText(self.step.system_prompt or "")
        self.combo_ui_format.setCurrentText(get_key(FORMATS, self.step.ui_format, "Silent (Background Data Only)"))
        self.combo_ui_target.setCurrentText(get_key(TARGETS, self.step.ui_target, "Floating Overlay"))
        self.input_output_key.setText(self.step.output_key)
        self.input_html_template.setText(getattr(self.step, 'html_template', '') or "")
        
        self.spin_predict.setValue(self.step.llm_options.get("num_predict", 2048))
        self.spin_temp.setValue(self.step.llm_options.get("temperature", 0.7))
        self.chk_json.setChecked(self.step.llm_options.get("json_mode", False))
        self._toggle_dynamic_inputs()

    def _toggle_dynamic_inputs(self):
        t = TYPES.get(self.combo_type.currentText())
        self.llm_widget.setVisible(t == "LLM_QUERY")
        self.rag_widget.setVisible(t == "RAG_SEARCH")
        self.foreach_widget.setVisible(t == "FOREACH")
        self.condition_widget.setVisible(t == "CONDITION")
        self.python_widget.setVisible(t == "PYTHON_SCRIPT")
        
        show_ai = (t == "LLM_QUERY") or (t == "FOREACH" and self.combo_foreach_mode.currentIndex() == 0)
        self.ai_frame.setVisible(show_ai)
        fmt = FORMATS.get(self.combo_ui_format.currentText())
        self.input_html_template.setVisible(fmt == "custom_view")

    def update_step_from_ui(self):
        self.step.step_id = self.input_id.text().strip()
        self.step.step_type = TYPES.get(self.combo_type.currentText(), "LLM_QUERY")
        
        self.step.inputs = {}
        if self.step.step_type == "LLM_QUERY":
            self.step.inputs["query"] = self.input_llm_query.toPlainText().strip()
        elif self.step.step_type == "CONDITION":
            self.step.inputs["logic"] = self.input_cond.text().strip()
        elif self.step.step_type == "PYTHON_SCRIPT":
            self.step.inputs["script"] = self.input_script.toPlainText().strip()
        elif self.step.step_type == "FOREACH":
            self.step.inputs["list"] = self.input_foreach_list.text().strip()
            mode_idx = self.combo_foreach_mode.currentIndex()
            if mode_idx == 0:
                self.step.inputs["inline_type"] = "LLM_QUERY"
                self.step.inputs["inline_prompt"] = self.input_fe_prompt.toPlainText().strip()
            elif mode_idx == 1:
                self.step.inputs["inline_type"] = "RAG_SEARCH"
                self.step.inputs["inline_query"] = self.input_fe_query.text().strip()
            else:
                self.step.inputs["sub_blueprint_name"] = self.combo_fe_tool.currentText()
        else:
            self.step.inputs["queries"] = [self.input_rag_query.text().strip()]
            
        self.step.system_prompt = self.input_system.toPlainText().strip() or None
        self.step.ui_format = FORMATS.get(self.combo_ui_format.currentText(), "silent")
        self.step.ui_target = TARGETS.get(self.combo_ui_target.currentText(), "floating")
        self.step.output_key = self.input_output_key.text().strip()
        self.step.html_template = self.input_html_template.toPlainText().strip() or None
        
        self.step.llm_options["num_predict"] = self.spin_predict.value()
        self.step.llm_options["temperature"] = self.spin_temp.value()
        self.step.llm_options["json_mode"] = self.chk_json.isChecked()

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"QFrame#StepCard {{ background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-left: 4px solid {theme.get('accent', '#b366ff')}; border-radius: 8px; margin-bottom: 4px; }} QLabel {{ color: {theme.get('text_main', '#fff')}; font-size: 12px; }} QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px; }}")

class BlueprintEditorTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = self.main_window.theme_manager.get_theme() if hasattr(main_window, 'theme_manager') else {}
        self.bpm = getattr(self.main_window, 'blueprint_manager', None)
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
        
        btn_create = QPushButton("✨ New Tool")
        btn_create.setStyleSheet(f"background-color: {self.theme.get('success', '#00cc66') if self.theme else '#00cc66'}; color: white; font-weight: bold; border-radius: 4px; padding: 4px 8px;")
        btn_create.clicked.connect(self._create_new_tool)
        top_bar.addWidget(btn_create)

        btn_reset = QPushButton("↺ Reset")
        btn_reset.clicked.connect(self._reset_current_blueprint)
        top_bar.addWidget(btn_reset)
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
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.steps_container = QWidget()
        self.steps_layout = QVBoxLayout(self.steps_container)
        self.steps_layout.setContentsMargins(10, 10, 10, 10) 
        self.steps_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.steps_container)
        
        # --- Right Side Tab Widget (Assistant & Debugger) ---
        self.right_tabs = QTabWidget()
        self.right_tabs.setStyleSheet(f"QTabWidget::pane {{ border: 1px solid {self.theme.get('border', '#444')}; background-color: #111; }} QTabBar::tab {{ background: {self.theme.get('bg_panel', '#333')}; color: white; padding: 8px; }} QTabBar::tab:selected {{ background: {self.theme.get('accent', '#b366ff')}; }}")
        
        # 1. AI Assistant Tab
        self.assistant_tab = QWidget()
        ast_lyt = QVBoxLayout(self.assistant_tab)
        self.txt_ast_chat = QTextEdit()
        self.txt_ast_chat.setReadOnly(True)
        self.txt_ast_chat.setStyleSheet("background: transparent; border: none; color: white;")
        self.txt_ast_chat.append("<i>Hello! I am the Papyrus Blueprint Architect. Describe what you want your new tool to do, or how you want to modify this one, and I will build the JSON pipeline for you automatically!</i><br>")
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

        # 2. X-Ray Debugger Tab
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

        self.main_splitter.addWidget(self.scroll_area)
        self.main_splitter.addWidget(self.right_tabs)
        self.main_splitter.setSizes([700, 400]) 
        
        layout.addWidget(self.main_splitter, 1)

        bottom_bar = QHBoxLayout()
        self.btn_add_step = QPushButton("➕ Add Step")
        self.btn_add_step.clicked.connect(self._add_new_step)
        self.btn_save = QPushButton("💾 Save Blueprint")
        self.btn_save.clicked.connect(self._save_blueprints)
        bottom_bar.addWidget(self.btn_add_step)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.btn_save)
        layout.addLayout(bottom_bar)

        self.update_theme(self.theme)
        if self.bpm: self._populate_combo_box()

    # --- THE NEW MASTER RUNNER DISPATCHER ---
    def _send_chat(self):
        user_text = self.input_ast.text().strip()
        if not user_text: return
        self.input_ast.clear()
        self.btn_ast_send.setEnabled(False)
        self.txt_ast_chat.append(f"<br><b>You:</b> {user_text}<br><b>Architect:</b> ")
        
        if self.current_blueprint:
            for widget in self.step_widgets: widget.update_step_from_ui()
            current_json = json.dumps(dataclasses.asdict(self.current_blueprint), indent=2)
        else:
            current_json = "{}"

        # Fetch the Architect Blueprint from DefaultBlueprints
        architect_bp = DefaultBlueprints.get_blueprint_architect()
        state = {"user_text": user_text, "current_json": current_json}
        
        # Execute using the Master Runner
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
                # 1. Clean up trailing commas before parsing (Classic LLM error)
                json_str = re.sub(r',\s*}', '}', json_str)
                json_str = re.sub(r',\s*\]', ']', json_str)
                
                data = json.loads(json_str)
                
                # 2. Intercept and Auto-Correct Schema Hallucinations
                if "expected_inputs" in data and isinstance(data["expected_inputs"], list):
                    for inp in data["expected_inputs"]:
                        if "name" in inp and "key" not in inp:
                            inp["key"] = inp.pop("name") # Force translation to 'key'
                            
                if "steps" in data and isinstance(data["steps"], list):
                    for step in data["steps"]:
                        # Fix RAG_SEARCH input errors
                        if step.get("step_type") == "RAG_SEARCH" and "inputs" in step:
                            if "query" in step["inputs"] and "queries" not in step["inputs"]:
                                step["inputs"]["queries"] = [step["inputs"].pop("query")]
                            if "document" in step["inputs"] and "allowed_docs" not in step["inputs"]:
                                step["inputs"]["allowed_docs"] = [step["inputs"].pop("document")]
                
                new_bp = AIActionBlueprint.from_dict(data)
                
                if self.bpm:
                    # ---> THE CRITICAL FIX <---
                    # Disconnect the current UI state so it doesn't immediately 
                    # save its blank default settings over the newly generated JSON!
                    self.current_blueprint = None 
                    
                    self.bpm.blueprints[new_bp.name] = new_bp
                    self._populate_combo_box() 
                    self.combo_blueprints.setCurrentText(new_bp.name)
                    self._save_blueprints()
                    
                    self.txt_ast_chat.append("<br><br><b style='color:#00cc66;'>✅ Successfully applied and saved the new blueprint! The UI has been updated automatically.</b>")
            except Exception as e:
                self.txt_ast_chat.append(f"<br><br><b style='color:#ff4444;'>❌ Failed to parse JSON blueprint: {e}</b>")

    def _get_all_tool_names(self):
        if not self.bpm: return []
        core_tools = ["Chat - RAG Assistant", "Chat - Advanced Agent", "Brainstorm - Default", "Search Terms", "Master Outline"]
        return core_tools + [k for k in self.bpm.blueprints.keys() if k not in core_tools]

    def _populate_combo_box(self):
        current_text = self.combo_blueprints.currentText()
        self.combo_blueprints.blockSignals(True)
        self.combo_blueprints.clear()
        core_tools = ["Chat - RAG Assistant", "Chat - Advanced Agent", "Brainstorm - Default", "Search Terms", "Master Outline"]
        custom_tools = [k for k in self.bpm.blueprints.keys() if k not in core_tools]
        if custom_tools: self.combo_blueprints.addItems(core_tools + ["--- Custom Tools ---"] + custom_tools)
        else: self.combo_blueprints.addItems(core_tools)
        self.combo_blueprints.blockSignals(False)
        
        if current_text in [self.combo_blueprints.itemText(i) for i in range(self.combo_blueprints.count())]:
            self.combo_blueprints.setCurrentText(current_text)
        else:
            self._load_selected_blueprint()

    def _create_new_tool(self):
        name, ok = QInputDialog.getText(self, "New Tool", "Enter a name for your custom tool:")
        if ok and name and name not in self.bpm.blueprints:
            new_bp = AIActionBlueprint(name=name, description="A custom user tool.", steps=[
                ActionStep(step_id="query_llm", step_type="LLM_QUERY", inputs={"query": "{user_input}"}, ui_format="live_stream", ui_target="custom_tools_tab", llm_options={"num_predict": 2048, "temperature": 0.7})
            ])
            self.bpm.blueprints[name] = new_bp
            self._populate_combo_box()
            self.combo_blueprints.setCurrentText(name)

    def _render_pipeline(self):
        while self.steps_layout.count():
            item = self.steps_layout.takeAt(0)
            if item.widget() and not isinstance(item.widget(), StepCardWidget): item.widget().deleteLater() 
                
        current_vars = [] 
        for i, card in enumerate(self.step_widgets):
            card.available_vars = list(current_vars)
            self.steps_layout.addWidget(card)
            out_key = card.input_output_key.text().strip()
            if out_key and out_key not in current_vars: current_vars.append(out_key)
            if i < len(self.step_widgets) - 1:
                arrow = QLabel(f"⬇ Passes <b style='color: {self.theme.get('accent', '#b366ff')};'>{{{out_key}}}</b> down the pipeline ⬇")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.steps_layout.addWidget(arrow)

    def _load_selected_blueprint(self):
        if not self.bpm: return
        key = self.combo_blueprints.currentText()
        if key == "--- Custom Tools ---" or not key: return
        
        if self.current_blueprint:
            for widget in self.step_widgets: widget.update_step_from_ui()
            self.bpm.blueprints[self.current_blueprint.name] = self.current_blueprint
            
        core_tools = ["Chat - RAG Assistant", "Chat - Advanced Agent", "Brainstorm - Default", "Search Terms", "Master Outline"]
        if key in core_tools:
            if key == "Chat - RAG Assistant": default_bp = DefaultBlueprints.get_chat_blueprint("RAG Assistant Mode")
            elif key == "Chat - Advanced Agent": default_bp = DefaultBlueprints.get_chat_blueprint("RAG Agent Mode")
            elif key == "Brainstorm - Default": default_bp = DefaultBlueprints.get_brainstorm_blueprint("Brainstorm - Default")
            elif key == "Search Terms": default_bp = DefaultBlueprints.get_search_terms_blueprint()
            else: default_bp = DefaultBlueprints.get_master_outline_blueprint("Project")
            self.current_blueprint = self.bpm.get_blueprint(key, lambda: default_bp)
        else:
            self.current_blueprint = self.bpm.get_blueprint(key, lambda: AIActionBlueprint(name=key, description=""))
            
        self.current_blueprint.name = key 
        self.input_bp_name.setText(self.current_blueprint.name)
        self.input_bp_desc.setText(self.current_blueprint.description)
        
        for w in self.step_widgets: w.setParent(None); w.deleteLater()
        self.step_widgets.clear()
        
        tool_names = self._get_all_tool_names()
        for step in self.current_blueprint.steps:
            card = StepCardWidget(step, [], tool_names, self.theme)
            card.delete_requested.connect(self._remove_step)
            card.move_up_requested.connect(self._move_step_up)
            card.move_down_requested.connect(self._move_step_down)
            card.step_updated.connect(self._render_pipeline)
            self.step_widgets.append(card)
        self._render_pipeline()

    def _add_new_step(self):
        if not self.current_blueprint: return
        new_step = ActionStep(step_id=f"step_{len(self.current_blueprint.steps)+1}", step_type="LLM_QUERY", llm_options={"num_predict": 2048, "temperature": 0.7})
        self.current_blueprint.steps.append(new_step)
        card = StepCardWidget(new_step, [], self._get_all_tool_names(), self.theme)
        card.delete_requested.connect(self._remove_step)
        card.move_up_requested.connect(self._move_step_up)
        card.move_down_requested.connect(self._move_step_down)
        card.step_updated.connect(self._render_pipeline)
        self.step_widgets.append(card)
        self._render_pipeline()

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
            self.current_blueprint.description = self.input_bp_desc.text()
            for widget in self.step_widgets: widget.update_step_from_ui()
            self.bpm.blueprints[self.current_blueprint.name] = self.current_blueprint

        out_data = {k: dataclasses.asdict(v) for k, v in self.bpm.blueprints.items()}
        with open(self.bpm.blueprint_file, 'w', encoding='utf-8') as f:
            json.dump(out_data, f, indent=4)
            
        custom_tab = next((c for c in self.main_window.findChildren(QWidget) if c.__class__.__name__ == "CustomToolsTab"), None)
        if custom_tab and hasattr(custom_tab, 'refresh_tools'): custom_tab.refresh_tools()

    def _reset_current_blueprint(self):
        key = self.combo_blueprints.currentText()
        if key in self.bpm.blueprints: del self.bpm.blueprints[key]
        self._load_selected_blueprint()

    def _run_debugger(self):
        if not self.current_blueprint: return
        self.txt_debugger.clear()
        self.txt_debugger.append("<i>Initializing Test Run...</i>\n")
        
        for widget in self.step_widgets: widget.update_step_from_ui()
            
        from core.engine.master_runner import MasterActionRunner
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
        self.btn_save.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 4px; padding: 6px;")
        self.btn_add_step.setStyleSheet(style)
        self.input_ast.setStyleSheet(style)
        self.btn_ast_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; color: white; padding: 4px;")
        for w in self.step_widgets: w.update_theme(theme)