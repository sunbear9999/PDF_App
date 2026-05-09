from PySide6.QtWidgets import QFrame, QVBoxLayout, QPushButton, QLabel, QScrollArea, QWidget
from PySide6.QtCore import Qt

class UniversalInternalOverlay(QFrame):
    """A frameless overlay that darkens the app and displays dynamic AI results natively."""
    def __init__(self, main_window, theme):
        super().__init__(main_window)
        self.main_window = main_window
        self.theme = theme
        self.setObjectName("UniversalInternalOverlay")
        
        # Dim the rest of the application
        self.setStyleSheet("QFrame#UniversalInternalOverlay { background-color: rgba(0, 0, 0, 180); }")
        self.hide()

        # Center Panel
        self.panel = QFrame(self)
        self.panel.setStyleSheet(f"background-color: {theme['bg_main']}; border-radius: 8px; border: 1px solid {theme['border']};")
        self.panel_layout = QVBoxLayout(self.panel)

        self.lbl_title = QLabel("AI Result")
        self.lbl_title.setStyleSheet(f"color: {theme['text_main']}; font-size: 18px; font-weight: bold; border: none;")
        self.panel_layout.addWidget(self.lbl_title)

        # Dynamic Content Area (Scrollable)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content_container = QWidget()
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.content_container)
        self.panel_layout.addWidget(self.scroll)

        # Close Button
        self.btn_close = QPushButton("Close")
        self.btn_close.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; padding: 8px; border-radius: 4px;")
        self.btn_close.clicked.connect(self.hide)
        self.panel_layout.addWidget(self.btn_close, alignment=Qt.AlignmentFlag.AlignRight)

    def clear_content(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def resizeEvent(self, event):
        # Always stretch to fill the main window exactly
        self.resize(self.main_window.size())
        # Keep panel centered, occupying 60% of width, 80% of height
        pw, ph = int(self.width() * 0.6), int(self.height() * 0.8)
        self.panel.setFixedSize(pw, ph)
        self.panel.move((self.width() - pw) // 2, (self.height() - ph) // 2)