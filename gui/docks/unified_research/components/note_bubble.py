from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextEdit
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCursor
from gui.components.base import BaseCard


class NoteBubbleWidget(BaseCard):
    save_requested = Signal(str, str, str) # quote, note, doc_name
    jump_requested = Signal(str, str)      # doc_name, quote
    search_requested = Signal(str)         # quote (for finding similar context)

    def __init__(self, doc_name, quote, note="", theme=None, parent=None):
        super().__init__(theme=theme, accent_color=(theme or {}).get("accent", "#b366ff"), parent=parent)
        self.doc_name = doc_name
        self.quote = quote
        self.current_note = note

        self.setObjectName("UniversalNoteBubble")

        # Header: Document Name
        self.lbl_doc = self.add_title(f"<b>📄 {doc_name}</b>")

        # Body: The Exact Quote (Styled as a blockquote)
        self.lbl_quote = self.add_body_text(f"<i>\"{quote}\"</i>", muted=True)
        self.lbl_quote.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        # Editable Note Area
        self.body_layout.addWidget(QLabel("<b>AI Note / Summary:</b>"))
        self.note_edit = QTextEdit()
        self.note_edit.setPlainText(note)
        self.note_edit.setMaximumHeight(60)
        self.body_layout.addWidget(self.note_edit)

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

        self.body_layout.addLayout(toolbar)
        self.update_theme(self.theme)

    def update_theme(self, theme):
        super().update_theme(theme)
        text_color = theme.get('text_main', '#ffffff')
        self.note_edit.setStyleSheet(f"background: transparent; color: {text_color}; border: 1px solid {theme.get('border', '#444')}; border-radius: 4px;")
        btn_style = self.button_style()
        for btn in [self.btn_save, self.btn_jump, self.btn_similar]:
            btn.setStyleSheet(btn_style)
