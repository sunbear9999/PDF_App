import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QVBoxLayout, QWidget, QLabel
from gui.components.base.forms import SchemaFormBuilder
from gui.components.base.layouts import BaseToolDock, BasePromptWorkspace
from gui.components.base.core import SOLARIZED_THEME

class Phase1TestHarness(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Phase 1 - Solarized Modern UI")
        self.resize(800, 600)
        
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        # --- Test 1: Schema Form Builder ---
        test_schema = [
            {"key": "title", "label": "Project Title", "type": "text", "default": "Untitled"},
            {"key": "speed", "label": "Voice Speed", "type": "number", "default": 1.2},
            {"key": "model", "label": "AI Model", "type": "select", "choices": ["Gemma", "Llama", "Mistral"]},
            {"key": "is_active", "label": "Enable Auto-Save", "type": "boolean", "default": True},
            {"key": "notes", "label": "System Prompt", "type": "long_text", "default": "You are a helpful assistant."}
        ]
        
        form_wrapper = QWidget()
        form_layout = QVBoxLayout(form_wrapper)
        form_layout.setContentsMargins(24, 24, 24, 24)
        self.form_builder = SchemaFormBuilder(test_schema, SOLARIZED_THEME)
        form_layout.addWidget(self.form_builder)
        form_layout.addStretch()
        tabs.addTab(form_wrapper, "Schema Builder")

        # --- Test 2: Base Tool Dock ---
        self.tool_dock = BaseToolDock("Test Tool", SOLARIZED_THEME)
        self.tool_dock.header_layout.addWidget(QLabel("<span style='font-size:14px; font-weight:bold;'>Dock Settings</span>"))
        self.tool_dock.header_layout.addWidget(SchemaFormBuilder([{"key": "lang", "label": "Language", "default": "English"}], SOLARIZED_THEME))
        self.tool_dock.content_layout.addWidget(QLabel("This is the scrollable results area..."))
        self.tool_dock.action_button.setText("Run Extraction")
        self.tool_dock.action_button.clicked.connect(lambda: self.tool_dock.set_status("running", "Extracting text..."))
        tabs.addTab(self.tool_dock, "Tool Dock")

        # --- Test 3: Base Prompt Workspace ---
        self.workspace = BasePromptWorkspace(SOLARIZED_THEME)
        self.workspace.toolbar_layout.addWidget(QLabel("<span style='font-size:14px; font-weight:bold;'>Mode: Agentic Chat</span>"))
        self.workspace.send_requested.connect(self._handle_mock_chat)
        tabs.addTab(self.workspace, "Prompt Workspace")

        # Apply Global Solarized Styling for the Main Window & Tabs
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {SOLARIZED_THEME['bg_main']}; }}
            QWidget {{ color: {SOLARIZED_THEME['text_main']}; }}
            QTabWidget::pane {{
                border: none;
                border-top: 1px solid {SOLARIZED_THEME['border']};
                background: {SOLARIZED_THEME['bg_main']};
            }}
            QTabBar::tab {{
                background: transparent;
                color: {SOLARIZED_THEME['text_muted']};
                padding: 10px 20px;
                margin-right: 4px;
                font-weight: bold;
                font-size: 13px;
                border-bottom: 2px solid transparent;
            }}
            QTabBar::tab:selected {{
                color: {SOLARIZED_THEME['accent']};
                border-bottom: 2px solid {SOLARIZED_THEME['accent']};
            }}
            QTabBar::tab:hover:!selected {{
                color: {SOLARIZED_THEME['text_main']};
            }}
        """)

    def _handle_mock_chat(self, text):
        lbl = QLabel(f"<span style='font-weight:bold; color:{SOLARIZED_THEME['accent']}'>You</span><br><br>{text}")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"""
            background-color: {SOLARIZED_THEME['bg_panel']}; 
            padding: 12px; 
            border-radius: 8px;
            border: 1px solid {SOLARIZED_THEME['border']};
        """)
        self.workspace.add_widget_to_feed(lbl)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Phase1TestHarness()
    window.show()
    sys.exit(app.exec())