import os
import uuid
import fitz 
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QWidget, QFrame, QInputDialog, QMessageBox, QSizePolicy) 
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

class AIResultsDialog(QDialog):
    # Added 'window_title' to handle both Tags and Opposing Views dynamically
    def __init__(self, window_title, matches, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.matches = matches
        self.setWindowTitle(window_title)
        self.resize(650, 600)

        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        content_widget = QWidget()
        self.content_layout = QVBoxLayout(content_widget)
        self.content_layout.setSpacing(15)

        theme = getattr(main_window, 'theme_manager', None)
        theme_dict = theme.get_theme() if theme else {'bg_panel': '#2b2b2b', 'border': '#444', 'text_main': '#fff', 'accent': '#0078D7'}

        for match in matches:
            self._build_bubble(match, theme_dict)

        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(content_widget)
        layout.addWidget(scroll)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_close.setStyleSheet(f"background-color: {theme_dict.get('bg_panel')}; padding: 8px; border-radius: 4px; color: {theme_dict.get('text_main')};")
        layout.addWidget(btn_close)

    def _build_bubble(self, match, theme):
        bubble = QFrame()
        bubble.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.get('bg_panel', '#2b2b2b')};
                border: 1px solid {theme.get('border', '#444')};
                border-radius: 8px;
                padding: 10px;
            }}
        """)
        b_layout = QVBoxLayout(bubble)

        header = QLabel(f"📄 <b>{match['doc_name']}</b> (Page {match['page'] + 1})")
        header.setStyleSheet("border: none; background: transparent;")
        b_layout.addWidget(header)

        text_lbl = QLabel(f"<i>\"{match['text']}\"</i>")
        text_lbl.setWordWrap(True)
        text_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum) 
        text_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_lbl.setStyleSheet("border: none; background: transparent; color: #ccc; margin-top: 5px; margin-bottom: 10px;")
        b_layout.addWidget(text_lbl)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_jump = QPushButton("🔗 Jump to Page")
        btn_jump.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_jump.setStyleSheet("background-color: #444; color: white; border: none; padding: 6px 12px; border-radius: 4px;")
        btn_jump.clicked.connect(lambda _, m=match: self._jump_to_match(m))

        btn_save = QPushButton("🖍️ Highlight & Note")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.setStyleSheet(f"background-color: {theme.get('accent', '#0078D7')}; color: white; border: none; padding: 6px 12px; border-radius: 4px;")
        btn_save.clicked.connect(lambda _, m=match: self._save_as_note(m))

        btn_layout.addWidget(btn_jump)
        btn_layout.addWidget(btn_save)
        b_layout.addLayout(btn_layout)

        self.content_layout.addWidget(bubble)

    def _jump_to_match(self, match):
        pdf_path = next((p for p in self.main_window.project_manager.pdfs if os.path.basename(p) == match['doc_name']), None)
        if not pdf_path:
            QMessageBox.warning(self, "Error", "Document not found in project.")
            return

        self.main_window.switch_to_pdf(pdf_path)
        viewer = self.main_window.viewer
        doc = self.main_window.project_manager.get_doc(pdf_path)
        if not doc: return
        
        page = doc.load_page(match['page'])
        words = match['text'].split()
        quads = []
        
        full_quads = page.search_for(match['text'], quads=True)
        if full_quads:
            quads.extend(full_quads)
        else:
            for i in range(0, len(words), 4):
                chunk = " ".join(words[i:i+6])
                if chunk.strip():
                    hits = page.search_for(chunk, quads=True)
                    if hits: quads.extend(hits)

        viewer.jump_to_page(match['page'])

        if quads:
            rects = [q.rect for q in quads]
            viewer.search_hits = [{'pdf': pdf_path, 'page': match['page'], 'rect': r} for r in rects]
            viewer.current_hit_index = 0
            viewer.clear_search_highlights()
            viewer.render_search_highlights()
            
            union_rect = rects[0]
            for r in rects[1:]:
                union_rect = union_rect | r 
                
            viewer._execute_search_jump({'pdf': pdf_path, 'page': match['page'], 'rect': union_rect})
            
            if viewer.search_bar.isVisible():
                viewer.search_bar.hide()
        else:
            QMessageBox.information(self, "Approximate Jump", "Jumped to the page, but couldn't highlight the exact text boundaries.")

    def _save_as_note(self, match):
        note, ok = QInputDialog.getText(self, "Save Note", "Enter a note for this highlight:")
        if not ok: return

        pdf_path = next((p for p in self.main_window.project_manager.pdfs if os.path.basename(p) == match['doc_name']), None)
        if not pdf_path:
            QMessageBox.warning(self, "Error", "Document not found in project.")
            return

        doc = self.main_window.project_manager.get_doc(pdf_path)
        if not doc: return
        page = doc.load_page(match['page'])
        
        words = match['text'].split()
        quads = []
        
        full_quads = page.search_for(match['text'], quads=True)
        if full_quads:
            quads.extend(full_quads)
        else:
            for i in range(0, len(words), 4):
                chunk = " ".join(words[i:i+6])
                if chunk.strip():
                    hits = page.search_for(chunk, quads=True)
                    if hits: quads.extend(hits)

        if not quads:
            QMessageBox.warning(self, "Failed", "Could not locate the exact text bounds.")
            return

        new_annot_id = f"AINote|{uuid.uuid4()}"
        annot = page.add_highlight_annot(quads)
        annot.set_colors(stroke=(0.7, 0.4, 1.0)) 
        
        annot.set_info(info={"title": new_annot_id, "content": note, "subject": match['text']})
        annot.update()
        
        self.main_window.project_manager.mark_dirty(pdf_path)
        if pdf_path == self.main_window.current_file_path:
            self.main_window.viewer.reload_page(match['page'])
            
        self.main_window.viewer.annot_manager.highlight_created.emit({
            "id": new_annot_id, "subject": match['text'], "content": note,
            "pdf_path": pdf_path, "page_num": match['page'],
            "rect_coords": repr(list(annot.rect)), "color": QColor(179, 102, 255).name(),
        })
        self.main_window.viewer.annot_manager.note_added.emit()