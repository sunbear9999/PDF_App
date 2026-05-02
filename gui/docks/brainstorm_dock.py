# gui/docks/brainstorm_dock.py
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, 
                             QPushButton, QComboBox, QLabel)
from PySide6.QtCore import QThread, Signal, Qt
from core.brainstorm_manager import BrainstormManager

class BrainstormWorker(QThread):
    chunk_received = Signal(str)
    finished_with_data = Signal(str, str)

    def __init__(self, manager, query, mode, model, current_goal):
        super().__init__()
        self.manager = manager
        self.query = query
        self.mode = mode
        self.model = model
        self.current_goal = current_goal

    def run(self):
        cleaned_resp, new_goal = self.manager.generate_response(
            self.query, 
            self.mode, 
            self.model, 
            self.current_goal,
            callback=lambda chunk: self.chunk_received.emit(chunk)
        )
        self.finished_with_data.emit(cleaned_resp, new_goal or "")

class BrainstormDock(QWidget):
    def __init__(self, llm_manager, project_manager, parent=None):
        super().__init__(parent)
        self.llm_manager = llm_manager
        self.project_manager = project_manager
        self.manager = BrainstormManager(llm_manager, llm_manager.prompt_manager)
        self._build_ui()
        self._load_initial_goal()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # --- Top Controls ---
        ctrl_layout = QHBoxLayout()
        
        self.model_combo = QComboBox()
        self.model_combo.addItems(self.llm_manager.get_available_models())
        ctrl_layout.addWidget(QLabel("Model:"))
        ctrl_layout.addWidget(self.model_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Logical/Neutral (Default)", "RAG Enabled", "RAG Only"])
        ctrl_layout.addWidget(QLabel("Mode:"))
        ctrl_layout.addWidget(self.mode_combo)

        self.btn_clear = QPushButton("🗑️ Clear Context")
        self.btn_clear.clicked.connect(self._clear_chat)
        ctrl_layout.addWidget(self.btn_clear)
        
        layout.addLayout(ctrl_layout)

        # --- Project Goal Memory Area ---
        goal_label = QLabel("🧠 <b>Current Project Goal</b> (AI will update this automatically):")
        layout.addWidget(goal_label)
        
        self.goal_edit = QTextEdit()
        self.goal_edit.setMaximumHeight(80)
        self.goal_edit.setPlaceholderText("No goal set. Chat with the AI to develop one, or type it here manually.")
        self.goal_edit.textChanged.connect(self._save_manual_goal_edit)
        layout.addWidget(self.goal_edit, 1)

        # --- Chat Display ---
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText("Welcome to the Brainstorming Assistant. \n\nDescribe a topic you want to research, a dead end you've hit, or an argument you are trying to structure.")
        layout.addWidget(self.chat_display, 6)

        # --- Input Area ---
        input_layout = QHBoxLayout()
        self.input_field = QTextEdit()
        self.input_field.setMaximumHeight(80)
        self.input_field.setPlaceholderText("Type your thoughts here...")
        input_layout.addWidget(self.input_field)

        self.btn_send = QPushButton("Send")
        self.btn_send.setMinimumHeight(80)
        self.btn_send.clicked.connect(self._send_message)
        input_layout.addWidget(self.btn_send)

        layout.addLayout(input_layout, 2)

    def _load_initial_goal(self):
        saved_goal = self.project_manager.get_metadata("project_description", "")
        self.goal_edit.blockSignals(True)
        self.goal_edit.setPlainText(saved_goal)
        self.goal_edit.blockSignals(False)

    def _save_manual_goal_edit(self):
        self.project_manager.set_metadata("project_description", self.goal_edit.toPlainText().strip())

    def _clear_chat(self):
        self.manager.clear_history()
        self.chat_display.clear()
        self.chat_display.append("<i>Context cleared. The assistant has forgotten previous messages.</i><br><br>")

    def _send_message(self):
        query = self.input_field.toPlainText().strip()
        if not query: return

        self.input_field.clear()
        self.chat_display.append(f"<b>You:</b> {query}<br><br><b>Assistant:</b> ")
        self.btn_send.setEnabled(False)

        mode_text = self.mode_combo.currentText()
        if "Default" in mode_text: mode = "Default"
        elif "Enabled" in mode_text: mode = "RAG Enabled"
        else: mode = "RAG Only"

        # SAFELY fetch the goal on the main thread BEFORE starting the worker
        current_goal = self.project_manager.get_metadata("project_description", "No project goal defined yet. Discuss your ideas to set one.")

        self.worker = BrainstormWorker(
            self.manager, 
            query, 
            mode, 
            self.model_combo.currentText(),
            current_goal
        )
        self.worker.chunk_received.connect(self._on_chunk)
        self.worker.finished_with_data.connect(self._on_finished)
        self.worker.start()

    def _on_chunk(self, chunk):
        cursor = self.chat_display.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.insertPlainText(chunk)
        self.chat_display.ensureCursorVisible()

    def _on_finished(self, cleaned_resp, new_goal):
        # 1. SAFELY update the DB on the main thread
        if new_goal:
            self.project_manager.set_metadata("project_description", new_goal)
            
            self.goal_edit.blockSignals(True)
            self.goal_edit.setPlainText(new_goal)
            self.goal_edit.blockSignals(False)
            
            original_style = self.goal_edit.styleSheet()
            self.goal_edit.setStyleSheet(original_style + "border: 2px solid #a8ff9d;")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1500, lambda: self.goal_edit.setStyleSheet(original_style))

        # 2. Rebuild the chat display cleanly
        self.chat_display.clear()
        for turn in self.manager.history:
            self.chat_display.append(f"<b>You:</b> {turn['user']}<br><br><b>Assistant:</b> {turn['ai']}<br><br>")

        self.btn_send.setEnabled(True)
        self.input_field.setFocus()

    def update_theme(self, theme):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {theme['bg_main']};
                color: {theme['text_main']};
            }}
            QTextEdit {{
                background-color: {theme['bg_input']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
            }}
            QPushButton {{
                background-color: {theme['bg_panel']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background-color: {theme['accent_hover']};
            }}
            QComboBox {{
                background-color: {theme['bg_input']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-radius: 4px;
                padding: 4px;
            }}
            QLabel {{
                background: transparent;
            }}
        """)
        # Force the send button to pop
        self.btn_send.setStyleSheet(f"background-color: {theme['accent']}; color: #ffffff; font-weight: bold; border: none; border-radius: 4px;")