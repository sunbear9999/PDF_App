# gui/docks/brainstorm_tab.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QScrollArea, QFrame, QSizePolicy, QComboBox, QLabel
from PySide6.QtCore import Qt, QTimer
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
from gui.docks.unified_research.components.dynamic_inputs import DynamicInputWidget
from gui.docks.unified_research.tabs.base_tab import BaseTab

class BrainstormTab(BaseTab):
    def __init__(self, main_window, parent=None):
        super().__init__(main_window, target_id="brainstorm_dock", parent=parent)
        self.active_blueprint = None
        self._build_ui()
        self._load_blueprint()
        QTimer.singleShot(100, self.safe_load_history) # Inherited from BaseTab

    def _load_blueprint(self):
        # Dynamically load blueprint based on user's selected Strictness level
        mode_key = self.combo_mode.currentData()
        
        from core.engine.default_blueprints import DefaultBlueprints
        self.active_blueprint = self.blueprint_manager.get_blueprint(
            f"Brainstorm - {mode_key}", 
            lambda: DefaultBlueprints.get_brainstorm_blueprint(self.prompt_manager, prompt_key=mode_key)
        )

        while self.dynamic_options_layout.count():
            item = self.dynamic_options_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        self.dynamic_inputs = DynamicInputWidget(self.active_blueprint.expected_inputs, self.theme, self.project_manager)
        self.dynamic_options_layout.addWidget(self.dynamic_inputs)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Top Bar: Mode Selector
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("<b>Strategy Mode:</b>"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("Creative Synthesis (Default)", "Brainstorm System - Default")
        self.combo_mode.addItem("RAG + Creative Expansion", "Brainstorm System - RAG Enabled")
        self.combo_mode.addItem("Strict Document Evidence Only", "Brainstorm System - RAG Only")
        self.combo_mode.currentIndexChanged.connect(self._load_blueprint)
        top_bar.addWidget(self.combo_mode)
        
        self.dynamic_options_layout = QHBoxLayout()
        top_bar.addLayout(self.dynamic_options_layout)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container) # BaseTab routing targets this layout
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
        self.input_field.setPlaceholderText("Describe your thesis or ideas you want to explore...")
        self.input_field.setMaximumHeight(50)
        input_layout.addWidget(self.input_field)

        self.btn_send = QPushButton("Send")
        self.btn_send.setFixedSize(60, 40)
        self.btn_send.clicked.connect(self._send_message)
        input_layout.addWidget(self.btn_send)

        layout.addWidget(self.input_wrapper)

    def _send_message(self):
        text = self.input_field.toPlainText().strip()
        if not text or not self.active_blueprint: return
        self.input_field.clear()

        if self.project_manager:
            self.project_manager.save_chat_message("brainstorm_dock", "user", text, "text")

        user_msg = ChatMessageWidget("You", theme=self.theme, is_user=True)
        user_msg.append_chunk(text)
        self.receive_ai_widget(user_msg) # Inherited from BaseTab

        dynamic_state = self.dynamic_inputs.get_values()

        initial_state = {
            "query": text,
            "context": "",
            **dynamic_state
        }

        self.send_to_pipeline(self.active_blueprint, initial_state)
        
    def update_theme(self, theme):
        super().update_theme(theme)
        self.scroll_area.setStyleSheet("background: transparent;")
        self.combo_mode.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px; padding: 4px;")
        self.input_wrapper.setStyleSheet(f"background-color: {theme.get('bg_input', '#2b2b2b')}; border: 1px solid {theme.get('border', '#444')}; border-radius: 8px;")
        self.input_field.setStyleSheet("background-color: transparent; color: {0}; border: none;".format(theme.get('text_main', '#fff')))
        self.btn_send.setStyleSheet(f"background-color: {theme.get('accent', '#b366ff')}; font-weight: bold; color: white; border: none; border-radius: 6px;")