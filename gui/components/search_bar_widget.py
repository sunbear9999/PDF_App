# gui/components/search_bar_widget.py
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox

class SearchBarWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find in document...")
        self.search_input.setFixedWidth(200)
        
        self.chk_match_case = QCheckBox("Match Case")
        
        self.hit_label = QLabel("0 / 0")
        
        self.btn_prev = QPushButton("▲")
        self.btn_next = QPushButton("▼")
        
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["Current PDF", "Entire Project"])
        
        self.btn_close = QPushButton("✖")
        
        layout.addWidget(self.search_input)
        layout.addWidget(self.chk_match_case)
        layout.addWidget(self.hit_label)
        layout.addWidget(self.btn_prev)
        layout.addWidget(self.btn_next)
        layout.addWidget(self.scope_combo)
        layout.addWidget(self.btn_close)

    def update_theme(self, theme):
        self.setStyleSheet(f"""
            QFrame {{ background-color: {theme['bg_panel']}; border: 1px solid {theme['border']}; border-radius: 8px; }}
            QLineEdit {{ background-color: {theme['bg_input']}; border: 1px solid {theme['border']}; padding: 6px; color: {theme['text_main']}; border-radius: 4px; }}
            QLabel {{ color: {theme['text_muted']}; font-weight: bold; border: none; }}
            QCheckBox {{ color: {theme['text_main']}; font-weight: bold; border: none; padding-right: 5px; }}
            QPushButton {{ background-color: {theme['bg_input']}; color: {theme['text_main']}; border: none; padding: 6px 10px; border-radius: 4px; font-weight: bold; }}
            QPushButton:hover {{ background-color: {theme['border']}; }}
            QComboBox {{ background-color: {theme['bg_input']}; border: 1px solid {theme['border']}; color: {theme['text_main']}; padding: 4px; border-radius: 4px; }}
        """)
        self.btn_close.setStyleSheet(f"QPushButton {{ background-color: {theme['error']}; color: #ffffff; }} QPushButton:hover {{ background-color: #ff6666; }}")

    def update_hits(self, current, total):
        self.hit_label.setText(f"{current} / {total}")