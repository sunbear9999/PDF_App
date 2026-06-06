# gui/components/dialogs/quick_note_dialog.py
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload

class QuickNoteDialog(QDialog):
    def __init__(self, target_annot, annot_id, page_num, pdf_path, project_manager, event_bus, theme=None, parent=None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.annot_id = annot_id
        self.page_num = page_num
        self.pdf_path = pdf_path
        self.pm = project_manager
        self.bus = event_bus # Inject the global bus
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

    def _get_fresh_annot(self):
        """Fetches the latest annotation object from the document to avoid stale pointer crashes."""
        doc = self.pm.get_doc(self.pdf_path)
        if not doc: return None, None
        
        page = doc.load_page(self.page_num)
        for annot in page.annots() or []:
            if annot.info and annot.info.get("title") == self.annot_id:
                return page, annot
        return page, None

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
            page, annot = self._get_fresh_annot()
            if annot:
                new_info = dict(annot.info)
                new_info["content"] = new_text
                annot.set_info(info=new_info)
                annot.update()
            self.pm.mark_dirty(self.pdf_path)
            self.bus.highlight_updated.emit(
                DocumentEvent.HIGHLIGHT_UPDATED,
                DocumentEventPayload(
                    annot_id=self.annot_id,
                    changes={"note": new_text, "pdf_path": self.pdf_path, "page_num": self.page_num},
                ),
            )
            self.bus.document_action_requested.emit(DocumentIntent.RELOAD_PAGE, DocumentPayload(page_num=self.page_num))

    def delete_highlight(self):
        self.state["is_deleted"] = True
        page, annot = self._get_fresh_annot()
        if page and annot:
            page.delete_annot(annot)
            
        self.pm.mark_dirty(self.pdf_path)
        
        # Shout to the EventBus!
        self.bus.highlight_deleted.emit(
            DocumentEvent.HIGHLIGHT_DELETED,
            DocumentEventPayload(annot_id=self.annot_id),
        )
        self.bus.document_action_requested.emit(DocumentIntent.RELOAD_PAGE, DocumentPayload(page_num=self.page_num))
        self.close()

    def change_highlight_color(self, rgb_tuple, hex_col):
        # Update PyMuPDF using a fresh annotation reference
        page, annot = self._get_fresh_annot()
        if annot:
            annot.set_colors(stroke=rgb_tuple)
            annot.update()
        self.pm.mark_dirty(self.pdf_path)
        self.selected_hex_color = hex_col
        self.bus.highlight_updated.emit(
            DocumentEvent.HIGHLIGHT_UPDATED,
            DocumentEventPayload(
                annot_id=self.annot_id,
                changes={"color": hex_col, "pdf_path": self.pdf_path, "page_num": self.page_num},
            ),
        )
        self.bus.document_action_requested.emit(DocumentIntent.RELOAD_PAGE, DocumentPayload(page_num=self.page_num))
