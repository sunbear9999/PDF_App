from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea, QFrame, QTextEdit
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from gui.components.base.core import ThemedMixin

class BaseToolDock(QWidget, ThemedMixin):
    def __init__(self, title: str, theme: dict = None, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16) # Expanded margins
        self.layout.setSpacing(16)

        self.header_layout = QVBoxLayout()
        self.layout.addLayout(self.header_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.content_widget)
        self.layout.addWidget(self.scroll_area, 1)

        self.footer_layout = QHBoxLayout()
        self.status_label = QLabel("Ready.")
        self.action_button = QPushButton("Run")
        self.action_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        
        self.footer_layout.addWidget(self.status_label, 1)
        self.footer_layout.addWidget(self.action_button)
        self.layout.addLayout(self.footer_layout)

        self.apply_base_theme(theme)

    def set_status(self, status_type: str, message: str):
        self.status_label.setText(message)
        color = self.theme.get(status_type, self.theme['text_muted'])
        self.status_label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 13px;")
        self.action_button.setEnabled(status_type != "running")

    def update_theme(self, theme: dict):
        super().update_theme(theme)
        self.setStyleSheet(f"background-color: {self.theme['bg_main']}; color: {self.theme['text_main']};")
        self.scroll_area.setStyleSheet("background: transparent;")
        self.content_widget.setStyleSheet("background: transparent;")
        self.action_button.setStyleSheet(self.get_button_style(is_primary=True))
        self.set_status("ready", self.status_label.text())


class BasePromptWorkspace(QWidget, ThemedMixin):
    send_requested = Signal(str)

    def __init__(self, theme: dict = None, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.toolbar_widget = QWidget()
        self.toolbar_layout = QHBoxLayout(self.toolbar_widget)
        self.toolbar_layout.setContentsMargins(16, 12, 16, 12)
        self.layout.addWidget(self.toolbar_widget)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.feed_widget = QWidget()
        self.feed_layout = QVBoxLayout(self.feed_widget)
        self.feed_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.feed_layout.setSpacing(16) # Better chat spacing
        self.feed_layout.setContentsMargins(16, 16, 16, 16)
        self.scroll_area.setWidget(self.feed_widget)
        self.layout.addWidget(self.scroll_area, 1)

        # Unified, rounded input area
        self.input_wrapper = QWidget()
        wrapper_layout = QVBoxLayout(self.input_wrapper)
        wrapper_layout.setContentsMargins(16, 8, 16, 16)
        
        self.input_container = QFrame()
        self.input_layout = QHBoxLayout(self.input_container)
        self.input_layout.setContentsMargins(12, 8, 8, 8)
        
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("Type a message...")
        self.text_input.setMaximumHeight(80)
        
        self.btn_send = QPushButton("Send")
        self.btn_send.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_send.clicked.connect(self._on_send)
        
        self.input_layout.addWidget(self.text_input, 1)
        self.input_layout.addWidget(self.btn_send)
        wrapper_layout.addWidget(self.input_container)
        self.layout.addWidget(self.input_wrapper)

        self.apply_base_theme(theme)

    def _on_send(self):
        text = self.text_input.toPlainText().strip()
        if text:
            self.send_requested.emit(text)
            self.text_input.clear()

    def add_widget_to_feed(self, widget: QWidget):
        self.feed_layout.addWidget(widget)
        self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())

    def update_theme(self, theme: dict):
        super().update_theme(theme)
        self.setStyleSheet(f"background-color: {self.theme['bg_main']}; color: {self.theme['text_main']};")
        self.toolbar_widget.setStyleSheet(f"background-color: {self.theme['bg_panel']}; border-bottom: 1px solid {self.theme['border']};")
        self.scroll_area.setStyleSheet("background: transparent;")
        self.feed_widget.setStyleSheet("background: transparent;")
        
        # Modern seamless input styling
        self.input_wrapper.setStyleSheet(f"background-color: {self.theme['bg_main']};")
        self.input_container.setStyleSheet(f"QFrame {{ background-color: {self.theme['bg_input']}; border: 1px solid {self.theme['border']}; border-radius: 8px; }}")
        self.text_input.setStyleSheet(f"background: transparent; color: {self.theme['text_main']}; border: none; font-size: 13px;")
        self.btn_send.setStyleSheet(self.get_button_style(is_primary=True))