from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QComboBox, QPushButton, QToolButton, QMenu, QSizePolicy, QLabel
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

class MainToolbar(QWidget):
    def __init__(self, main_window, theme_manager):
        super().__init__()
        self.main_window = main_window
        self.theme_manager = theme_manager

        self.setObjectName("MainToolbar")
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(12, 6, 12, 6)
        self.layout.setSpacing(10)

        # File dropdown
        self.file_btn = QPushButton("File")
        self.file_btn.setMenu(self._build_file_menu())
        self.layout.addWidget(self.file_btn)

        # Project dropdown
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(180)
        self.layout.addWidget(QLabel("Project:"))
        self.layout.addWidget(self.project_combo)

        # Zoom controls
        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setText("🔍−")
        self.zoom_out_btn.setToolTip("Zoom Out")
        self.layout.addWidget(self.zoom_out_btn)

        self.fit_width_btn = QToolButton()
        self.fit_width_btn.setText("↔️ Fit Width")
        self.fit_width_btn.setToolTip("Fit Width")
        self.layout.addWidget(self.fit_width_btn)

        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setText("🔍+")
        self.zoom_in_btn.setToolTip("Zoom In")
        self.layout.addWidget(self.zoom_in_btn)

        # Tool buttons
        self.notes_btn = QToolButton()
        self.notes_btn.setText("📝")
        self.notes_btn.setToolTip("Notes")
        self.layout.addWidget(self.notes_btn)

        self.ocr_btn = QToolButton()
        self.ocr_btn.setText("🔤")
        self.ocr_btn.setToolTip("OCR")
        self.layout.addWidget(self.ocr_btn)

        self.audio_btn = QToolButton()
        self.audio_btn.setText("🔊")
        self.audio_btn.setToolTip("Audio (TTS)")
        self.layout.addWidget(self.audio_btn)

        self.llm_btn = QToolButton()
        self.llm_btn.setText("🤖")
        self.llm_btn.setToolTip("LLM Chat")
        self.layout.addWidget(self.llm_btn)

        # Spacer
        self.layout.addStretch(1)

        # Help button
        self.help_btn = QToolButton()
        self.help_btn.setText("❓")
        self.help_btn.setToolTip("Help")
        self.layout.addWidget(self.help_btn)

        # Theme selector
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(self.theme_manager.themes.keys())
        self.layout.addWidget(self.theme_combo)

        self.apply_theme()

    def _build_file_menu(self):
        menu = QMenu()
        menu.addAction("New Project", self.main_window.project_handler.new_project)
        menu.addAction("Open Project", self.main_window.project_handler.open_project)
        menu.addAction("Save As", self.main_window.project_handler.save_project_as)
        menu.addAction("Add PDF", self.main_window.project_handler.add_pdf)
        return menu

    def apply_theme(self):
        theme = self.theme_manager.get_theme()
        # Fallbacks for button_bg, button_fg, button_hover
        button_bg = theme.get('bg_panel', '#2b2b2b')
        button_fg = theme.get('text_main', '#ffffff')
        button_hover = theme.get('accent', '#0078D7')
        self.setStyleSheet(f"""
QWidget#MainToolbar {{
    background: {theme['bg_panel']};
    border-bottom: 1px solid {theme['border']};
}}
QPushButton, QComboBox, QToolButton {{
    background: {button_bg};
    color: {button_fg};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 16px;
}}
QPushButton::menu-indicator {{ image: none; }}
QToolButton:hover, QPushButton:hover, QComboBox:hover {{
    background: {button_hover};
}}
        """)
