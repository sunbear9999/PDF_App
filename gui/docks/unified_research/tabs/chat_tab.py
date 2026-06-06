# gui/docks/chat_tab.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QScrollArea, QFrame, QSizePolicy, QMenu
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QAction
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from gui.docks.unified_research.components.dynamic_inputs import DynamicInputWidget
import json
import os 
from gui.docks.unified_research.tabs.base_tab import BaseTab

class ChatTab(BaseTab):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, target_id="chat_dock", parent=parent)
        self.active_blueprint = None
        self._build_ui()
        self._load_blueprint()
        QTimer.singleShot(100, self.safe_load_history) # Inherited from BaseTab

    def _load_blueprint(self):
        from core.engine.default_blueprints import DefaultBlueprints
        self.active_blueprint = self.blueprint_manager.get_blueprint(
            "Chat - Universal Agent", 
            lambda: DefaultBlueprints.get_universal_chat_blueprint(self.prompt_manager)
        )

        while self.dynamic_options_layout.count():
            item = self.dynamic_options_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        self.dynamic_inputs = DynamicInputWidget(self.active_blueprint.expected_inputs, self.theme, self.project_manager)
        self.dynamic_options_layout.addWidget(self.dynamic_inputs)

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
        self.dynamic_options_layout = QHBoxLayout()
        action_layout.addLayout(self.dynamic_options_layout)
        
        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_settings.clicked.connect(self._show_settings_menu)
        
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
        if self.theme: menu.setStyleSheet(f"background-color: {self.theme.get('bg_panel', '#333')}; color: {self.theme.get('text_main', '#fff')};")
            
        filter_action = QAction("🎯 Context & Document Filters...", self)
        filter_action.triggered.connect(self._open_context_filter)
        menu.addAction(filter_action)
        menu.addSeparator()
        clear_action = QAction("🗑️ Clear Chat History", self)
        clear_action.triggered.connect(lambda: self.main_window.unified_dock.clear_tab_history(self, self.target_id))
        menu.addAction(clear_action)
        menu.exec(self.btn_settings.mapToGlobal(self.btn_settings.rect().bottomLeft()))

    def _open_context_filter(self):
        from gui.docks.unified_research.components.context_filter_dialog import ContextFilterDialog
        pm = self.project_manager
        current_docs = self._metadata_json("active_rag_docs", [os.path.basename(p) for p in pm.pdfs])
        current_tags = self._metadata_json("active_rag_tags", [])
        current_logic = pm.get_metadata("active_rag_tag_logic", "OR")
        
        dlg = ContextFilterDialog(pm, current_docs, current_tags, current_logic, self.theme, self)
        if dlg.exec():
            docs, tags, logic = dlg.get_results()
            pm.set_metadata("active_rag_docs", json.dumps(docs))
            pm.set_metadata("active_rag_tags", json.dumps(tags))
            pm.set_metadata("active_rag_tag_logic", logic)
            sys_msg = ChatMessageWidget("System", theme=self.theme, is_user=False)
            sys_msg.append_chunk(f"Context updated. Now targeting **{len(docs)}** documents.")
            self.receive_ai_widget(sys_msg)

    def _metadata_json(self, key, default):
        raw = self.project_manager.get_metadata(key, json.dumps(default))
        if isinstance(raw, list):
            return raw
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else default
        except Exception:
            return default

    def _send_message(self):
        text = self.input_field.toPlainText().strip()
        if not text or not self.active_blueprint: return
        self.input_field.clear()

        pm = self.project_manager
        if pm: pm.save_chat_message(self.target_id, "user", text, "text")

        user_msg = ChatMessageWidget("You", theme=self.theme, is_user=True)
        user_msg.append_chunk(text)
        self.receive_ai_widget(user_msg)

        dynamic_state = self.dynamic_inputs.get_values()
        history_str = ""
        
        if pm:
            history_data = pm.get_chat_history(self.target_id)
            for msg in history_data[-6:]:
                role = "User" if msg["role"] == "user" else "AI"
                if msg["ui_format"] in ["live_stream", "text"]:
                    history_str += f"{role}: {msg['content']}\n\n"

        initial_state = {
            "user_query": text,
            "chat_history": history_str.strip(),
            "chat_persona": "RAG Agent Mode" if dynamic_state.get("use_advanced_rag") else "General Assistant",
            **dynamic_state 
        }

        self.send_to_pipeline(self.active_blueprint, initial_state)

    def update_theme(self, theme):
        super().update_theme(theme)
        self.input_wrapper.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 8px;")
        self.input_field.setStyleSheet(f"background-color: transparent; color: {theme.get('text_main', '#fff')}; border: none;")
        self.btn_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 6px;")
        self.btn_settings.setStyleSheet("background: transparent; border: none;")
