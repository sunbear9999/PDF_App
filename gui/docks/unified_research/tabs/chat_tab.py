# gui/docks/chat_tab.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QScrollArea, QFrame, QSizePolicy, QMenu
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QAction
from gui.docks.unified_research.components.chat_streamer import ChatMessageWidget
import json
from core.engine.action_model import AIActionBlueprint, ActionStep
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
        self.btn_autopilot = QPushButton("🧠 Auto-Pilot")
        self.btn_autopilot.setCheckable(True)
        
        # Wired up Settings Menu
        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_settings.clicked.connect(self._show_settings_menu)
        
        action_layout.addWidget(self.btn_autopilot)
        action_layout.addWidget(self.btn_settings)
        action_layout.addStretch()
        
        self.btn_send = QPushButton("Send")
        self.btn_send.setFixedSize(70, 30)
        self.btn_send.clicked.connect(self._send_message)
        action_layout.addWidget(self.btn_send)
        
        input_layout.addLayout(action_layout)
        layout.addWidget(self.input_wrapper)

    def _show_settings_menu(self):
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction
        
        menu = QMenu(self)
        if self.theme:
            menu.setStyleSheet(f"background-color: {self.theme.get('bg_panel', '#333')}; color: {self.theme.get('text_main', '#fff')};")
            
        filter_action = QAction("🎯 Advanced RAG Filters...", self)
        filter_action.triggered.connect(self._open_context_filter)
        menu.addAction(filter_action)
        
        menu.addSeparator()
            
        clear_action = QAction("🗑️ Clear Chat History", self)
        clear_action.triggered.connect(self._clear_chat)
        menu.addAction(clear_action)
        
        menu.exec(self.btn_settings.mapToGlobal(self.btn_settings.rect().bottomLeft()))

    def _open_context_filter(self):
        """Opens the global context filter to restrict RAG."""
        from gui.docks.unified_research.components.context_filter_dialog import ContextFilterDialog
        pm = self.main_window.project_manager
        
        # Load current state (you would store these on self or pull from PM)
        current_docs = pm.get_metadata("active_rag_docs", [os.path.basename(p) for p in pm.pdfs])
        current_tags = pm.get_metadata("active_rag_tags", [])
        
        dlg = ContextFilterDialog(pm, current_docs, current_tags, "OR", self.theme, self)
        if dlg.exec():
            docs, tags, logic = dlg.get_results()
            pm.set_metadata("active_rag_docs", docs)
            pm.set_metadata("active_rag_tags", tags)
            
            # Send a notification to the chat that filters changed
            sys_msg = ChatMessageWidget("System", theme=self.theme, is_user=False)
            sys_msg.append_chunk(f"RAG Filters Updated. Now searching **{len(docs)}** documents.")
            self.add_message_widget(sys_msg)

    def _clear_chat(self):
        while self.chat_layout.count() > 1: # Leave the stretch at the end
            item = self.chat_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def add_message_widget(self, widget):
        """Safely inserts the message ABOVE the stretch to prevent weird spacing."""
        # Find the stretch and insert right before it
        count = self.chat_layout.count()
        if count > 0:
            self.chat_layout.insertWidget(count - 1, widget)
        else:
            self.chat_layout.addWidget(widget)
            
        # Safely scroll to bottom after the UI has a millisecond to draw the widget
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        scrollbar = self.scroll_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _send_message(self):
        text = self.input_field.toPlainText().strip()
        if not text: return
        self.input_field.clear()

        user_msg = ChatMessageWidget("You", theme=self.theme, is_user=True)
        user_msg.append_chunk(text)
        self.add_message_widget(user_msg)

        pm = getattr(self.main_window, 'project_manager', None)
        raw_settings = pm.get_metadata("global_ai_settings", "{}") if pm else "{}"
        import json
        global_settings = json.loads(raw_settings) if raw_settings else {}
        output_workspace = global_settings.get("output_workspace", False)
        
        from core.engine.default_blueprints import DefaultBlueprints
        
        # --- FIX: Fetch the exact Blueprint from the Manager based on the Toggle ---
        if self.btn_autopilot.isChecked():
            bp_key = "Chat - Advanced Agent"
            default_func = lambda: DefaultBlueprints.get_chat_blueprint("RAG Agent Mode")
        else:
            bp_key = "Chat - RAG Assistant"
            default_func = lambda: DefaultBlueprints.get_chat_blueprint("RAG Assistant Mode")

        # Get user overrides if they exist, otherwise build the default
        blueprint = self.main_window.blueprint_manager.get_blueprint(bp_key, default_func)

        initial_state = {
            "user_query": text,
            "project_manifest": pm.get_metadata("project_manifest", "Analyze the documents.") if pm else "",
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
        self.btn_autopilot.setStyleSheet(f"""
            QPushButton {{ background-color: {theme.get('bg_panel', '#333')}; border: 1px solid {theme.get('border', '#444')}; padding: 4px 8px; border-radius: 6px; font-weight: bold; color: {theme.get('text_main', '#fff')}; }}
            QPushButton:checked {{ background-color: {theme.get('accent', '#b366ff')}; color: white; border: none; }}
        """)