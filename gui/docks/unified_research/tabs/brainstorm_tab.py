# gui/docks/brainstorm_tab.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QScrollArea, QFrame, QSizePolicy, QCheckBox
from PySide6.QtCore import Qt, QTimer
import json
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget

class BrainstormTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # --- The Strict RAG Toggle ---
        top_bar = QHBoxLayout()
        self.chk_rag_only = QCheckBox("🎯 Strict RAG Mode (Answer ONLY from context)")
        self.chk_rag_only.setToolTip("Forces the AI to brainstorm using ONLY your selected documents. Automatically enables RAG globally if disabled.")
        top_bar.addWidget(self.chk_rag_only)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(0) # <--- ADD THIS: 0 for touching, or 4 for a tiny gap
        self.chat_layout.addStretch()
        self.scroll_area.setWidget(self.chat_container)
        layout.addWidget(self.scroll_area, 1)

        self.input_wrapper = QFrame()
        self.input_wrapper.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        input_layout = QHBoxLayout(self.input_wrapper)
        input_layout.setContentsMargins(8, 8, 8, 8)
        
        self.input_field = QTextEdit()
        self.input_field.setPlaceholderText("Brainstorm an idea with the strategy agent...")
        self.input_field.setMaximumHeight(50)
        input_layout.addWidget(self.input_field)

        self.btn_send = QPushButton("Send")
        self.btn_send.setFixedSize(60, 40)
        self.btn_send.clicked.connect(self._send_message)
        input_layout.addWidget(self.btn_send)

        layout.addWidget(self.input_wrapper)

    def add_message_widget(self, widget):
        count = self.chat_layout.count()
        if count > 0:
            self.chat_layout.insertWidget(count - 1, widget)
        else:
            self.chat_layout.addWidget(widget)
        QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))

    def _send_message(self):
        text = self.input_field.toPlainText().strip()
        if not text: return
        self.input_field.clear()

        pm = getattr(self.main_window, 'project_manager', None)
        if pm:
            pm.save_chat_message("brainstorm_dock", "user", text, "text")

        user_msg = ChatMessageWidget("You", theme=self.theme, is_user=True)
        user_msg.append_chunk(text)
        self.add_message_widget(user_msg)

        # 1. Fetch Global Settings & Ensure Variables Exist
        global_settings = {}
        if pm:
            try: global_settings = json.loads(pm.get_metadata("global_ai_settings", "{}"))
            except: pass

        rag_enabled = global_settings.get("rag_enabled", True)
        output_workspace = global_settings.get("output_workspace", False) # <--- Variable defined here
        include_manifest = global_settings.get("include_manifest", True)
        allow_updates = global_settings.get("allow_manifest_updates", True)
        include_nodes = global_settings.get("include_selected_nodes", False)

        # Routing logic based on Checkbox + Global Settings
        if self.chk_rag_only.isChecked():
            bp_key = "Brainstorm - RAG Only"
            if not rag_enabled and pm:
                global_settings["rag_enabled"] = True
                pm.set_metadata("global_ai_settings", json.dumps(global_settings))
                
                sys_msg = ChatMessageWidget("System", theme=self.theme, is_user=False)
                sys_msg.append_chunk("*Note: Global RAG Search was automatically enabled to support Strict Mode.*")
                self.add_message_widget(sys_msg)
        else:
            bp_key = "Brainstorm - RAG Enabled" if rag_enabled else "Brainstorm - Default"

        manifest_data = pm.get_metadata("project_manifest", "{}") if pm and include_manifest else "{}"
        if not manifest_data.strip(): manifest_data = "{}"
        
        selected_nodes = "[]"
        if include_nodes and hasattr(self.main_window, 'workspace_view'):
            try: selected_nodes = self.main_window.workspace_view.get_selected_nodes_json()
            except: pass

        selected_model = "llama3"
        if hasattr(self.main_window, 'unified_dock') and hasattr(self.main_window.unified_dock, 'model_combo'):
            selected_model = self.main_window.unified_dock.model_combo.currentText()

        # 2. Get Blueprint using correct cache key
        from core.engine.default_blueprints import DefaultBlueprints
        
        bp_cache_key = f"{bp_key} - Workspace" if output_workspace else bp_key
        blueprint = self.main_window.blueprint_manager.get_blueprint(
            bp_cache_key, 
            lambda: DefaultBlueprints.get_brainstorm_blueprint(prompt_key=bp_key, output_workspace=output_workspace)
        )

        # 3. Execute
        initial_state = {
            "query": text,
            "selected_model": selected_model, 
            "project_manifest": manifest_data,
            "allow_manifest_updates": allow_updates,
            "selected_nodes": selected_nodes,
            "workspace_data": self.main_window.workspace_view.get_workspace_state_as_json() if output_workspace and hasattr(self.main_window, 'workspace_view') else "{}",
            "context": "" 
        }

        self.main_window.execute_ai_blueprint(blueprint, initial_state)
    
    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')};")
        self.scroll_area.setStyleSheet("background: transparent;")
        
        self.chk_rag_only.setStyleSheet(f"color: {theme.get('text_main', '#fff')}; font-weight: bold;")
        
        self.input_wrapper.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 8px;")
        self.input_field.setStyleSheet("background-color: transparent; color: {0}; border: none;".format(theme.get('text_main', '#fff')))
        self.btn_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 6px;")