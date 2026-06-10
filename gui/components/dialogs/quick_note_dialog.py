# gui/components/dialogs/quick_note_dialog.py
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel
from PySide6.QtCore import Qt
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentIntent, DocumentPayload

class QuickNoteDialog(QDialog):
    def __init__(self, target_annot, annot_id, page_num, pdf_path, theme=None, parent=None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.annot_id = annot_id
        self.page_num = page_num
        self.pdf_path = pdf_path
        self.bus = EventBus.get_instance()
        self.theme_dict = theme
        self.selected_hex_color = None
        
        # Safely extract text, DO NOT store target_annot as a class var
        self.initial_quote = target_annot.info.get("subject", "No quote extracted")
        self.initial_note = target_annot.info.get("content", "")
        
        self.setWindowTitle("📝 Edit Highlight")
        self.setMinimumSize(280, 200) 
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.state = {"is_deleted": False}
        self._build_ui()
        if self.theme_dict:
            self._apply_theme() # Use self.theme_dict in your existing _apply_theme method

        self.finished.connect(self.save_note_text)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 1. Quote Display
        lbl_quote = QTextEdit()
        lbl_quote.setPlainText(f'"{self.initial_quote}"')
        lbl_quote.setReadOnly(True)
        lbl_quote.setMaximumHeight(45)
        lbl_quote.setStyleSheet("background: transparent; border: none; font-style: italic; color: #888;")
        layout.addWidget(lbl_quote)

        # 2. Note Box
        layout.addWidget(QLabel("<b>Your Note:</b>"))
        self.note_editor = QTextEdit()
        self.note_editor.setPlainText(self.initial_note)
        self.note_editor.setPlaceholderText("Type your thoughts here...")
        layout.addWidget(self.note_editor)

        # 3. Toolbar (Colors + Delete)
        toolbar = QHBoxLayout()
        colors = [
            ("#ffe16b", (1.0, 0.88, 0.42)), ("#ff9d9d", (1.0, 0.61, 0.61)), 
            ("#a8ff9d", (0.66, 1.0, 0.61)), ("#9de1ff", (0.61, 0.88, 1.0)), 
            ("#d89dff", (0.84, 0.61, 1.0))  
        ]
        
        color_layout = QHBoxLayout()
        color_layout.setSpacing(6)
        for hex_col, rgb_tuple in colors:
            btn_col = QPushButton()
            btn_col.setFixedSize(22, 22)
            btn_col.setStyleSheet(f"background-color: {hex_col}; border-radius: 11px; border: 1px solid #555;")
            btn_col.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_col.clicked.connect(lambda checked, c=rgb_tuple, h=hex_col: self.change_highlight_color(c, h))
            color_layout.addWidget(btn_col)
            
        toolbar.addLayout(color_layout)
        toolbar.addStretch()
        
        btn_delete = QPushButton("🗑️ Delete")
        btn_delete.setStyleSheet("background-color: transparent; border: none; color: #ff4444; font-weight: bold;")
        btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_delete.clicked.connect(self.delete_highlight)
        toolbar.addWidget(btn_delete)
        layout.addLayout(toolbar)

    def _apply_theme(self):
        theme = self.theme_dict or {}
        self.setStyleSheet(f"""
            QDialog {{ background-color: {theme.get('bg_panel', '#2b2b2b')}; color: {theme.get('text_main', '#ffffff')}; }}
            QTextEdit {{
                background-color: {theme.get('bg_input', theme.get('bg_panel', '#2b2b2b'))}; color: {theme.get('text_main', '#ffffff')};
                border: 1px solid {theme.get('border', '#555555')}; border-radius: 6px; padding: 6px;
            }}
            QLabel {{ color: {theme.get('text_main', '#ffffff')}; }}
        """)

    def save_note_text(self):
        if self.state["is_deleted"]: return
            
        new_text = self.note_editor.toPlainText().strip()
        
        if new_text != self.initial_note:
            self.bus.document_action_requested.emit(
                DocumentIntent.UPDATE_HIGHLIGHT_NOTE,
                DocumentPayload(
                    path=self.pdf_path,
                    page_num=self.page_num,
                    annot_id=self.annot_id,
                    note=new_text,
                ),
            )

    def delete_highlight(self):
        self.state["is_deleted"] = True
        self.bus.document_action_requested.emit(
            DocumentIntent.DELETE_HIGHLIGHT,
            DocumentPayload(path=self.pdf_path, page_num=self.page_num, annot_id=self.annot_id),
        )
        self.close()

    def change_highlight_color(self, rgb_tuple, hex_col):
        self.selected_hex_color = hex_col
        self.bus.document_action_requested.emit(
            DocumentIntent.UPDATE_HIGHLIGHT_COLOR,
            DocumentPayload(
                path=self.pdf_path,
                page_num=self.page_num,
                annot_id=self.annot_id,
                color=rgb_tuple,
            ),
        )
