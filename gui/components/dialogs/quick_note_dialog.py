# gui/components/dialogs/quick_note_dialog.py
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

class QuickNoteDialog(QDialog):
    def __init__(self, target_annot, annot_id, page_num, pdf_path, main_window, parent=None):
        super().__init__(parent, Qt.WindowType.Tool)
        self.annot_id = annot_id
        self.page_num = page_num
        self.pdf_path = pdf_path
        self.main_window = main_window
        self.pm = main_window.project_manager
        
        # Extract the strings we need for the UI, but DO NOT save target_annot 
        # as a class variable, as it will go stale!
        self.initial_quote = target_annot.info.get("subject", "No quote extracted")
        self.initial_note = target_annot.info.get("content", "")
        
        self.setWindowTitle("📝 Edit Highlight")
        self.setMinimumSize(280, 200) 
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.state = {"is_deleted": False}
        self._build_ui()
        self._apply_theme()

        self.finished.connect(self.save_note_text)

    def _get_fresh_annot(self):
        """Fetches the latest annotation object from the document to avoid stale pointer crashes."""
        doc = self.pm.get_doc(self.pdf_path)
        if not doc: return None, None
        
        page = doc.load_page(self.page_num)
        for annot in page.annots():
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
        if hasattr(self.main_window, 'theme_manager'):
            theme = self.main_window.theme_manager.get_theme()
            self.setStyleSheet(f"""
                QDialog {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; }}
                QTextEdit {{ 
                    background-color: {theme['bg_panel']}; color: {theme['text_main']}; 
                    border: 1px solid {theme['border']}; border-radius: 6px; padding: 6px;
                }}
            """)

    def save_note_text(self):
        if self.state["is_deleted"]: return
        new_text = self.note_editor.toPlainText()
        if self.initial_note == new_text: return
            
        # Update PyMuPDF using a fresh annotation reference
        page, annot = self._get_fresh_annot()
        if annot:
            info = dict(annot.info)
            info["content"] = new_text
            annot.set_info(info=info)
            annot.update()
        
        # Update DB using safe Facade
        self.pm.mark_dirty(self.pdf_path)
        if hasattr(self.pm, 'update_highlight_text'):
            self.pm.update_highlight_text(self.annot_id, new_text)
            
        self._sync_ram_nodes(new_text=new_text)

    def change_highlight_color(self, rgb_tuple, hex_col):
        # Update PyMuPDF using a fresh annotation reference
        page, annot = self._get_fresh_annot()
        if annot:
            annot.set_colors(stroke=rgb_tuple)
            annot.update()
        
        self.pm.mark_dirty(self.pdf_path)
        if hasattr(self.pm, 'update_highlight_color'):
            self.pm.update_highlight_color(self.annot_id, hex_col)

        self._sync_ram_nodes(color=hex_col)
        self.main_window.viewer.reload_page(self.page_num)
        
    def delete_highlight(self):
        self.state["is_deleted"] = True
        page, annot = self._get_fresh_annot()
        if page and annot:
            page.delete_annot(annot)
            
        self.pm.mark_dirty(self.pdf_path)
        self.pm.delete_highlight_record(self.annot_id)
        
        self.main_window.viewer.reload_page(self.page_num)
        for nd in self.main_window.notes_docks: nd.refresh_notes()
        for ws in self.main_window.workspace_docks: ws._sync_workspace()
        self.close()

    def _sync_ram_nodes(self, new_text=None, color=None):
        for ws in self.main_window.workspace_docks:
            for node in ws.nodes.values():
                if getattr(node, 'highlight_id', None) == self.annot_id:
                    if new_text is not None: node.note = new_text
                    if color is not None: node.color = color
                    node.update()
            self.pm.mark_dirty("workspace")
        for nd in self.main_window.notes_docks: nd.refresh_notes()