# gui/docks/brainstorm_tab.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QScrollArea, QFrame, QSizePolicy, QCheckBox
from PySide6.QtCore import Qt, QTimer
import json
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from gui.docks.unified_research.tabs.base_tab import BaseTab

class BrainstormTab(BaseTab):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

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
        self.chat_layout.setSpacing(0)
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

        pm = self.project_manager
        if pm:
            pm.save_chat_message("brainstorm_dock", "user", text, "text")

        user_msg = ChatMessageWidget("You", theme=self.theme, is_user=True)
        user_msg.append_chunk(text)
        self.add_message_widget(user_msg)

        global_settings = {}
        if pm:
            try: global_settings = json.loads(pm.get_metadata("global_ai_settings", "{}"))
            except: pass

        rag_enabled = global_settings.get("rag_enabled", True)
        output_workspace = global_settings.get("output_workspace", False) 

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

        selected_model = "llama3"
        if hasattr(self.main_window, 'unified_dock') and hasattr(self.main_window.unified_dock, 'model_combo'):
            selected_model = self.main_window.unified_dock.model_combo.currentText()

        from core.engine.default_blueprints import DefaultBlueprints
        
        blueprint = self.blueprint_manager.get_blueprint(
            bp_key, 
            lambda: DefaultBlueprints.get_brainstorm_blueprint(self.prompt_manager, prompt_key=bp_key)
        )

        initial_state = {
            "query": text,
            "selected_model": selected_model, 
            "context": "" 
        }

        self.send_to_pipeline(blueprint, initial_state, output_workspace=output_workspace)
        
    def update_theme(self, theme):
        super().update_theme(theme)
        self.scroll_area.setStyleSheet("background: transparent;")
        self.chk_rag_only.setStyleSheet(f"color: {theme.get('text_main', '#fff')}; font-weight: bold;")
        self.input_wrapper.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 8px;")
        self.input_field.setStyleSheet("background-color: transparent; color: {0}; border: none;".format(theme.get('text_main', '#fff')))
        self.btn_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 6px;")