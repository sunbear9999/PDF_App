# gui/docks/chat_tab.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QScrollArea, QFrame, QSizePolicy, QMenu
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QAction
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
import json
import os 

class ChatTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(0)
        self.chat_layout.addStretch() 
        self.scroll_area.setWidget(self.chat_container)
        layout.addWidget(self.scroll_area, 1)

        self.input_wrapper = QFrame()
        self.input_wrapper.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        input_layout = QVBoxLayout(self.input_wrapper)
        input_layout.setContentsMargins(8, 8, 8, 8)
        
        self.input_field = QTextEdit()
        self.input_field.setPlaceholderText("Ask a question about your documents...")
        self.input_field.setMaximumHeight(70) 
        input_layout.addWidget(self.input_field)

        action_layout = QHBoxLayout()
        
        # --- NEW: Advanced RAG Button ---
        self.btn_advanced_rag = QPushButton("🔍 Advanced RAG")
        self.btn_advanced_rag.setCheckable(True)
        self.btn_advanced_rag.setToolTip("Performs a multi-pass RAG search for deep context extraction.")
        
        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_settings.clicked.connect(self._show_settings_menu)
        
        action_layout.addWidget(self.btn_advanced_rag)
        action_layout.addWidget(self.btn_settings)
        action_layout.addStretch()
        
        self.btn_send = QPushButton("Send")
        self.btn_send.setFixedSize(70, 30)
        self.btn_send.clicked.connect(self._send_message)
        action_layout.addWidget(self.btn_send)
        
        input_layout.addLayout(action_layout)
        layout.addWidget(self.input_wrapper)

    def _show_settings_menu(self):
        menu = QMenu(self)
        if self.theme:
            menu.setStyleSheet(f"background-color: {self.theme.get('bg_panel', '#333')}; color: {self.theme.get('text_main', '#fff')};")
            
        filter_action = QAction("🎯 Context & Auto-Pilot Settings...", self)
        filter_action.triggered.connect(self._open_context_filter)
        menu.addAction(filter_action)
        
        menu.addSeparator()
            
        clear_action = QAction("🗑️ Clear Chat History", self)
        clear_action.triggered.connect(lambda: self.main_window.unified_dock.clear_tab_history(self, "chat_dock"))
        menu.addAction(clear_action)
        
        menu.exec(self.btn_settings.mapToGlobal(self.btn_settings.rect().bottomLeft()))

    def _open_context_filter(self):
        from gui.docks.unified_research.components.context_filter_dialog import ContextFilterDialog
        pm = self.main_window.project_manager
        current_docs = pm.get_metadata("active_rag_docs", [os.path.basename(p) for p in pm.pdfs])
        current_tags = pm.get_metadata("active_rag_tags", [])
        
        dlg = ContextFilterDialog(pm, current_docs, current_tags, "OR", self.theme, self)
        if dlg.exec():
            docs, tags, logic = dlg.get_results()
            pm.set_metadata("active_rag_docs", docs)
            pm.set_metadata("active_rag_tags", tags)
            
            sys_msg = ChatMessageWidget("System", theme=self.theme, is_user=False)
            sys_msg.append_chunk(f"Context updated. Now targeting **{len(docs)}** documents.")
            self.add_message_widget(sys_msg)

    def add_message_widget(self, widget):
        count = self.chat_layout.count()
        if count > 0:
            self.chat_layout.insertWidget(count - 1, widget)
        else:
            self.chat_layout.addWidget(widget)

    def _send_message(self):
        text = self.input_field.toPlainText().strip()
        if not text: return
        self.input_field.clear()

        pm = getattr(self.main_window, 'project_manager', None)
        if pm:
            pm.save_chat_message("chat_dock", "user", text, "text")

        user_msg = ChatMessageWidget("You", theme=self.theme, is_user=True)
        user_msg.append_chunk(text)
        self.add_message_widget(user_msg)

        # 1. Fetch Global Settings & Ensure Variables Exist
        global_settings = {}
        if pm:
            try:
                global_settings = json.loads(pm.get_metadata("global_ai_settings", "{}"))
            except:
                pass

        include_manifest = global_settings.get("include_manifest", True)
        allow_updates = global_settings.get("allow_manifest_updates", True)
        include_nodes = global_settings.get("include_selected_nodes", False)
        output_workspace = global_settings.get("output_workspace", False) # <--- Variable defined here

        manifest_data = pm.get_metadata("project_manifest", "{}") if pm and include_manifest else "{}"
        if not manifest_data.strip(): manifest_data = "{}"
        
        selected_nodes = "[]"
        if include_nodes and hasattr(self.main_window, 'workspace_view'):
            try:
                selected_nodes = self.main_window.workspace_view.get_selected_nodes_json()
            except Exception:
                selected_nodes = "[]"

        selected_model = "llama3"
        if hasattr(self.main_window, 'unified_dock') and hasattr(self.main_window.unified_dock, 'model_combo'):
            selected_model = self.main_window.unified_dock.model_combo.currentText()
        
        # 2. Get Blueprint using correct cache key
        from core.engine.default_blueprints import DefaultBlueprints
        
        if self.btn_autopilot.isChecked():
            bp_key = "Chat - Advanced Agent"
            default_func = lambda: DefaultBlueprints.get_chat_blueprint("RAG Agent Mode", output_workspace=output_workspace)
        else:
            bp_key = "Chat - RAG Assistant"
            default_func = lambda: DefaultBlueprints.get_chat_blueprint("RAG Assistant Mode", output_workspace=output_workspace)

        bp_cache_key = f"{bp_key} - Workspace" if output_workspace else bp_key
        blueprint = self.main_window.blueprint_manager.get_blueprint(bp_cache_key, default_func)

        # 3. Execute
        initial_state = {
            "user_query": text,
            "selected_model": selected_model, 
            "project_manifest": manifest_data,
            "allow_manifest_updates": allow_updates,
            "selected_nodes": selected_nodes,
            "workspace_data": self.main_window.workspace_view.get_workspace_state_as_json() if output_workspace and hasattr(self.main_window, 'workspace_view') else "{}"
        }

        self.main_window.execute_ai_blueprint(blueprint, initial_state)

    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')};")
        self.input_wrapper.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 8px;")
        self.input_field.setStyleSheet(f"background-color: transparent; color: {theme.get('text_main', '#fff')}; border: none;")
        self.btn_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 6px;")
        self.btn_settings.setStyleSheet("background: transparent; border: none;")
        self.btn_advanced_rag.setStyleSheet(f"""
            QPushButton {{ background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px 8px; border-radius: 6px; font-weight: bold; color: {theme.get('text_main', '#fff')}; }}
            QPushButton:checked {{ background-color: {theme.get('accent', '#b366ff')}; color: white; border: none; }}
        """)