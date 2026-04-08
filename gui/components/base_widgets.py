# gui/components/base_widgets.py
from PyQt6.QtWidgets import QPushButton, QFrame, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt


class PrimaryButton(QPushButton):
    """Solid accent button for primary actions."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("PrimaryButton")


class GhostButton(QPushButton):
    """Transparent button with border for secondary actions."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("GhostButton")


class NotificationBanner(QFrame):
    """Banner for notifications like OCR or argument map prompts."""
    def __init__(self, message="", parent=None):
        super().__init__(parent)
        self.setObjectName("NotificationBanner")
        self.setFixedHeight(50)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 10, 0)

        self.label = QLabel(message)
        layout.addWidget(self.label)

        layout.addStretch()

        self.close_button = GhostButton("Dismiss")
        self.close_button.clicked.connect(self.hide)
        layout.addWidget(self.close_button)