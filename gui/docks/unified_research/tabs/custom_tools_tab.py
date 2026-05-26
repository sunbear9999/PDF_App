import os
import re
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                             QComboBox, QFrame, QTextEdit, QScrollArea, QSizePolicy)
from PySide6.QtCore import Qt, QTimer
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget

class CustomToolsTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = self.main_window.theme_manager.get_theme() if hasattr(main_window, 'theme_manager') else {}
        self.bpm = getattr(self.main_window, 'blueprint_manager', None)
        self.dynamic_widgets = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("<b>Run Tool:</b>"))
        self.combo_tools = QComboBox()
        self.btn_refresh = QPushButton("🔄")
        self.btn_refresh.setFixedSize(28, 28)
        self.btn_refresh.clicked.connect(self.refresh_tools)
        
        top_layout.addWidget(self.combo_tools, 1)
        top_layout.addWidget(self.btn_refresh)
        layout.addLayout(top_layout)
        
        self.lbl_desc = QLabel("<i>Select a tool to view its description.</i>")
        self.lbl_desc.setWordWrap(True)
        layout.addWidget(self.lbl_desc)

        self.param_frame = QFrame()
        self.param_frame.setStyleSheet(f"background-color: rgba(0,0,0,0.1); border: 1px solid {self.theme.get('border', '#444')}; border-radius: 6px;")
        self.param_layout = QVBoxLayout(self.param_frame)
        layout.addWidget(self.param_frame)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.console_container = QWidget()
        self.console_layout = QVBoxLayout(self.console_container)
        self.console_layout.setContentsMargins(0, 0, 0, 0)
        self.console_layout.addStretch() 
        self.scroll_area.setWidget(self.console_container)
        layout.addWidget(self.scroll_area, 1)

        self.btn_run = QPushButton("▶ Run Tool")
        self.btn_run.setFixedHeight(40)
        self.btn_run.clicked.connect(self._execute_tool)
        layout.addWidget(self.btn_run)

        self.combo_tools.currentIndexChanged.connect(self._update_description)
        self.update_theme(self.theme)
        self.refresh_tools()

    def _update_description(self):
        if not self.bpm: return
        tool_name = self.combo_tools.currentText()
        bp = self.bpm.blueprints.get(tool_name)
        if bp:
            self.lbl_desc.setText(f"<i>{bp.description}</i>" if bp.description else "<i>No description provided.</i>")
            self._build_dynamic_form(bp)

    def _build_dynamic_form(self, blueprint):
        while self.param_layout.count():
            item = self.param_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.dynamic_widgets.clear()

        style = f"background-color: {self.theme.get('bg_input', '#2b2b2b')}; border: 1px solid {self.theme.get('border', '#444')}; padding: 4px;"

        inputs_to_render = blueprint.expected_inputs.copy() if blueprint.expected_inputs else []

        # If user didn't explicitly define expected_inputs, auto-discover variables in the blueprint
        if not inputs_to_render:
            discovered_vars = set()
            for step in blueprint.steps:
                texts_to_check = [step.system_prompt, getattr(step, 'html_template', None)] + list(step.inputs.values())
                for txt in texts_to_check:
                    if isinstance(txt, str):
                        matches = re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', txt)
                        discovered_vars.update(matches)
            
            system_vars = {'selected_model', 'item', 'workspace_data', 'context', 'rag_context', 'result'}
            custom_vars = discovered_vars - system_vars

            for var in custom_vars:
                inputs_to_render.append({
                    "key": var, 
                    "type": "doc_selector" if "doc" in var.lower() else "text_long", 
                    "label": var.replace('_', ' ').title()
                })

        if not inputs_to_render:
            self.param_layout.addWidget(QLabel("<i>No inputs required. Simply click run.</i>"))
            return

        for inp in inputs_to_render:
            # Safely grab the key whether the LLM architect generated 'key', 'name', or 'id'
            inp_key = inp.get('key') or inp.get('name') or inp.get('id') or "unknown_input"
            inp_label = inp.get('label') or inp.get('description') or inp_key.replace('_', ' ').title()
            
            lbl = QLabel(f"<b>{inp_label}:</b> <span style='color:#888;'>(Variable: {{{inp_key}}})</span>")
            self.param_layout.addWidget(lbl)
            
            if inp.get('type') == 'doc_selector':
                cb = QComboBox()
                cb.setStyleSheet(style)
                pm = getattr(self.main_window, 'project_manager', None)
                if pm:
                    for pdf in pm.pdfs: cb.addItem(os.path.basename(pdf), pdf)
                self.param_layout.addWidget(cb)
                self.dynamic_widgets[inp_key] = cb

            elif inp.get('type') == 'dropdown':
                cb = QComboBox()
                cb.setStyleSheet(style)
                cb.addItems(inp.get('options', []))
                self.param_layout.addWidget(cb)
                self.dynamic_widgets[inp_key] = cb

            else: 
                te = QTextEdit()
                te.setStyleSheet(style)
                te.setMaximumHeight(60)
                if 'default' in inp: te.setText(str(inp['default']))
                self.param_layout.addWidget(te)
                self.dynamic_widgets[inp_key] = te

    def refresh_tools(self):
        self.combo_tools.clear()
        if not self.bpm: return
        core_tools = ["Chat - RAG Assistant", "Chat - Advanced Agent", "Brainstorm - Default", "Search Terms", "Master Outline", "Keyword Density Analyzer (Python)"]
        custom_tools = [k for k in self.bpm.blueprints.keys() if k not in core_tools]
        if custom_tools:
            self.combo_tools.addItems(custom_tools)
        else:
            self.combo_tools.addItem("No custom tools built yet.")

    def add_message_widget(self, widget):
        self.console_layout.insertWidget(self.console_layout.count() - 1, widget)
        QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))

    def _execute_tool(self):
        tool_name = self.combo_tools.currentText()
        if not self.bpm or tool_name not in self.bpm.blueprints: return
        blueprint = self.bpm.get_blueprint(tool_name, lambda: None)
        if not blueprint: return

        state_dict = {}
        for key, widget in self.dynamic_widgets.items():
            if isinstance(widget, QComboBox):
                state_dict[key] = widget.currentData() or widget.currentText()
                # Crucial for RAG searches: populate both the path and the readable basename
                if "doc" in key.lower(): 
                    state_dict[f"{key}_name"] = os.path.basename(widget.currentData() or widget.currentText())
            elif isinstance(widget, QTextEdit):
                state_dict[key] = widget.toPlainText().strip()

        ui_target = blueprint.steps[-1].ui_target if blueprint.steps else "custom_tools_tab"
        
        msg = ChatMessageWidget(f"Running Tool: {tool_name}", theme=self.theme, is_user=True)
        msg.append_chunk("Data dispatched to pipeline...")
        
        # Only inject the starting notification here if the tool outputs to this tab
        if ui_target == "custom_tools_tab":
            self.add_message_widget(msg)
            
        self.main_window.execute_ai_blueprint(blueprint, state_dict)

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")
        self.scroll_area.setStyleSheet("background: transparent;")
        
        style = f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px;"
        
        self.combo_tools.setStyleSheet(style)
        self.btn_refresh.setStyleSheet(style)
        self.btn_run.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 4px;")
        
        for widget in self.dynamic_widgets.values():
            widget.setStyleSheet(style)