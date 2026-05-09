# gui/docks/brainstorm_tab.py
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, 
                             QPushButton, QLabel, QScrollArea, QFrame, QComboBox, QSizePolicy)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from core.engine.action_model import AIActionBlueprint, ActionStep

class BrainstormTab(QWidget):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Strategy Mode:</b>"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Brainstorm - Default", "Brainstorm - RAG Enabled", "Brainstorm - RAG Only"])
        header.addWidget(self.combo_mode, 1)
        layout.addLayout(header)

        layout.addWidget(QLabel("<b>Current Project Goal:</b>"))
        self.goal_edit = QTextEdit()
        self.goal_edit.setPlaceholderText("What are you trying to figure out?")
        self.goal_edit.setMaximumHeight(60)
        layout.addWidget(self.goal_edit)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.addStretch() 
        self.scroll_area.setWidget(self.chat_container)
        layout.addWidget(self.scroll_area, 1)

        self.input_wrapper = QFrame()
        self.input_wrapper.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        input_layout = QHBoxLayout(self.input_wrapper)
        input_layout.setContentsMargins(0, 8, 0, 0)
        
        self.input_field = QTextEdit()
        self.input_field.setPlaceholderText("Brainstorm an idea...")
        self.input_field.setMaximumHeight(50)
        input_layout.addWidget(self.input_field)

        self.btn_send = QPushButton("Send")
        self.btn_send.setFixedSize(60, 40)
        self.btn_send.clicked.connect(self._send_message)
        input_layout.addWidget(self.btn_send)

        layout.addWidget(self.input_wrapper)

    def add_message_widget(self, widget):
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, widget)
        QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))

    def _send_message(self):
        text = self.input_field.toPlainText().strip()
        goal = self.goal_edit.toPlainText().strip()
        if not text: return
        self.input_field.clear()

        user_msg = ChatMessageWidget("You", theme=self.theme, is_user=True)
        user_msg.append_chunk(text)
        self.add_message_widget(user_msg)

        selected_prompt = self.combo_mode.currentText()
        
        from core.engine.default_blueprints import DefaultBlueprints
        
        # --- FIX: Use the dropdown string as the key to fetch user overrides! ---
        blueprint = self.main_window.blueprint_manager.get_blueprint(
            selected_prompt, 
            lambda: DefaultBlueprints.get_brainstorm_blueprint(prompt_key=selected_prompt)
        )
        
        self.main_window.execute_ai_blueprint(blueprint, {"query": text, "project_goal": goal})
    def update_theme(self, theme):
        self.theme = theme
        self.setStyleSheet(f"background-color: {theme.get('bg_main', '#1e1e1e')}; color: {theme.get('text_main', '#fff')};")
        self.scroll_area.setStyleSheet("background: transparent;")
        self.combo_mode.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px;")
        self.goal_edit.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px;")
        self.input_field.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px;")
        self.btn_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 4px;")