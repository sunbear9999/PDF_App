# gui/components/search_bar_widget.py
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox

class SearchBarWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame { background-color: #2b2b2b; border: 1px solid #555; border-radius: 8px; }
            QLineEdit { background-color: #1e1e1e; border: 1px solid #444; padding: 6px; color: white; border-radius: 4px; }
            QLabel { color: #ccc; font-weight: bold; border: none; }
            QCheckBox { color: white; font-weight: bold; border: none; padding-right: 5px; }
            QPushButton { background-color: #444; color: white; border: none; padding: 6px 10px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #555; }
            QComboBox { background-color: #1e1e1e; border: 1px solid #444; color: white; padding: 4px; border-radius: 4px; }
        """)
        
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
        self.btn_close.setStyleSheet("QPushButton { background-color: #662222; } QPushButton:hover { background-color: #ff4444; }")
        
        layout.addWidget(self.search_input)
        layout.addWidget(self.chk_match_case)
        layout.addWidget(self.hit_label)
        layout.addWidget(self.btn_prev)
        layout.addWidget(self.btn_next)
        layout.addWidget(self.scope_combo)
        layout.addWidget(self.btn_close)

    def update_hits(self, current, total):
        self.hit_label.setText(f"{current} / {total}")