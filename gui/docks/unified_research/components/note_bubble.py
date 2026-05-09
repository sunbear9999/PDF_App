from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCursor

class NoteBubbleWidget(QFrame):
    save_requested = Signal(str, str, str) # quote, note, doc_name
    jump_requested = Signal(str, str)      # doc_name, quote
    search_requested = Signal(str)         # quote (for finding similar context)

    def __init__(self, doc_name, quote, note="", theme=None, parent=None):
        super().__init__(parent)
        self.doc_name = doc_name
        self.quote = quote
        self.current_note = note
        
        self.setObjectName("UniversalNoteBubble")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header: Document Name
        self.lbl_doc = QLabel(f"<b>📄 {doc_name}</b>")
        layout.addWidget(self.lbl_doc)

        # Body: The Exact Quote (Styled as a blockquote)
        self.lbl_quote = QLabel(f"<i>\"{quote}\"</i>")
        self.lbl_quote.setWordWrap(True)
        self.lbl_quote.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.lbl_quote)

        # Editable Note Area
        layout.addWidget(QLabel("<b>AI Note / Summary:</b>"))
        self.note_edit = QTextEdit()
        self.note_edit.setPlainText(note)
        self.note_edit.setMaximumHeight(60)
        layout.addWidget(self.note_edit)

        # Action Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        
        self.btn_save = QPushButton("💾 Save Highlight")
        self.btn_save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_save.clicked.connect(lambda: self.save_requested.emit(self.quote, self.note_edit.toPlainText(), self.doc_name))
        
        self.btn_jump = QPushButton("🔗 Jump to Source")
        self.btn_jump.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_jump.clicked.connect(lambda: self.jump_requested.emit(self.doc_name, self.quote))
        
        self.btn_similar = QPushButton("🔍 Find Similar")
        self.btn_similar.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_similar.clicked.connect(lambda: self.search_requested.emit(self.quote))
        
        toolbar.addWidget(self.btn_save)
        toolbar.addWidget(self.btn_jump)
        toolbar.addWidget(self.btn_similar)
        toolbar.addStretch()
        
        layout.addLayout(toolbar)
        
        if theme:
            self.update_theme(theme)

    def update_theme(self, theme):
        bg_color = theme.get('bg_input', '#2b2b2b')
        border_color = theme.get('accent', '#b366ff')
        text_color = theme.get('text_main', '#ffffff')
        muted_color = theme.get('text_muted', '#aaaaaa')
        
        self.setStyleSheet(f"""
            QFrame#UniversalNoteBubble {{
                background-color: {bg_color};
                border: 1px solid {theme.get('border', '#444')};
                border-left: 4px solid {border_color};
                border-radius: 6px;
                margin-top: 4px; margin-bottom: 4px;
            }}
        """)
        self.lbl_doc.setStyleSheet(f"color: {text_color};")
        self.lbl_quote.setStyleSheet(f"color: {muted_color}; padding-left: 8px;")
        self.note_edit.setStyleSheet(f"background: transparent; color: {text_color}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px;")
        
        btn_style = f"background-color: {theme.get('bg_panel', '#333')}; color: {text_color}; border: 1px solid {theme.get('border', '#444')}; padding: 4px 8px; border-radius: 4px;"
        for btn in [self.btn_save, self.btn_jump, self.btn_similar]:
            btn.setStyleSheet(btn_style)