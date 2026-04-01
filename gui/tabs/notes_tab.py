from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QScrollArea, QFrame)
from PyQt6.QtCore import Qt, QTimer
import fitz

class NoteBubble(QFrame):
    def __init__(self, tab, page_num, annot_id, subject, content, color, is_ai=False):
        super().__init__()
        self.tab = tab
        self.page_num = page_num
        self.annot_id = annot_id
        
        # Apply special Cyber-Purple styling if it's an AI note
        if is_ai:
            self.setStyleSheet("""
                NoteBubble { background-color: #2d2238; border: 1px solid #b57edc; border-radius: 8px; margin-bottom: 5px; }
                NoteBubble:hover { border: 1px solid #d194ff; background-color: #38274a; }
            """)
        else:
            self.setStyleSheet("""
                NoteBubble { background-color: #2b2b2b; border: 1px solid #444; border-radius: 8px; margin-bottom: 5px; }
                NoteBubble:hover { border: 1px solid #0078D7; background-color: #333333; }
            """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Header
        header_layout = QHBoxLayout()
        lbl_page = QLabel(f"📄 Page {page_num + 1}")
        lbl_page.setStyleSheet("font-weight: bold; color: #aaa; border: none;")
        header_layout.addWidget(lbl_page)
        
        # Add special AI badge
        if is_ai:
            lbl_ai = QLabel("🤖 AI Generated")
            lbl_ai.setStyleSheet("color: #d194ff; font-weight: bold; font-size: 11px; border: none; margin-left: 10px;")
            header_layout.addWidget(lbl_ai)
            
        header_layout.addStretch()
        
        colors = [
            ("Yellow", (1.0, 0.9, 0.0)), 
            ("Green", (0.0, 0.8, 0.4)), 
            ("Blue", (0.2, 0.6, 1.0)), 
            ("Purple", (0.7, 0.4, 1.0)),
            ("Red", (1.0, 0.3, 0.3))
        ]
        
        def is_close(c1, c2):
            if not c1 or not c2 or len(c1) != len(c2): return False
            return all(abs(c1[i] - c2[i]) < 0.05 for i in range(len(c1)))

        for c_name, c_val in colors:
            btn_c = QPushButton()
            btn_c.setFixedSize(16, 16)
            border = "2px solid white" if is_close(c_val, color) else "none"
            btn_c.setStyleSheet(f"background-color: rgb({int(c_val[0]*255)}, {int(c_val[1]*255)}, {int(c_val[2]*255)}); border-radius: 8px; border: {border};")
            
            btn_c.setToolTip(f"Change to {c_name}")
            btn_c.clicked.connect(lambda checked, c=c_val: self.tab.change_note_color(self.page_num, self.annot_id, c))
            header_layout.addWidget(btn_c)
            
        header_layout.addSpacing(10)
            
        btn_del = QPushButton("✖ Delete")
        btn_del.setFixedSize(70, 24)
        btn_del.setStyleSheet("""
            QPushButton { background-color: #442222; color: #ff6666; border: 1px solid #662222; border-radius: 4px; font-weight: bold; font-size: 11px; }
            QPushButton:hover { background-color: #ff4444; color: white; }
        """)
        btn_del.clicked.connect(self.delete_note)
        header_layout.addWidget(btn_del)
        
        layout.addLayout(header_layout)
        
        # Content
        lbl_subj = QLabel(f'"{subject}"')
        lbl_subj.setWordWrap(True)
        lbl_subj.setStyleSheet("font-style: italic; color: #ddd; border: none;")
        layout.addWidget(lbl_subj)
        
        if content:
            lbl_content = QLabel(content)
            lbl_content.setWordWrap(True)
            lbl_content.setStyleSheet("font-weight: bold; color: white; margin-top: 5px; border: none;")
            layout.addWidget(lbl_content)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.tab.viewer.jump_to_page(self.page_num)
        super().mousePressEvent(event)

    def delete_note(self):
        self.tab.delete_note(self.page_num, self.annot_id)


class NotesTab(QWidget):
    def __init__(self, parent=None, viewer=None):
        super().__init__(parent)
        self.viewer = viewer
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 10, 5, 5)
        
        lbl = QLabel("Project Notes & Highlights")
        lbl.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 5px; padding-left: 5px;")
        layout.addWidget(lbl)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setContentsMargins(5, 5, 5, 5)
        
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)
        
    def refresh_notes(self):
        if not self.viewer.doc: return
        
        for i in reversed(range(self.scroll_layout.count())): 
            widget = self.scroll_layout.itemAt(i).widget()
            if widget: widget.deleteLater()
        
        for i in range(len(self.viewer.doc)):
            page = self.viewer.doc.load_page(i)
            for annot in page.annots():
                title = annot.info.get("title", "")
                
                # Check for either Human notes or AI notes
                if title.startswith("UserNote") or title.startswith("AINote"):
                    is_ai = title.startswith("AINote")
                    subject = annot.info.get("subject", "")
                    content = annot.info.get("content", "")
                    
                    colors = annot.colors
                    stroke = colors.get("stroke") if colors else None
                    
                    bubble = NoteBubble(self, i, title, subject, content, stroke, is_ai=is_ai)
                    self.scroll_layout.addWidget(bubble)

    def scroll_to_note(self, annot_id):
        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubble) and widget.annot_id == annot_id:
                self.scroll_area.ensureWidgetVisible(widget)
                original_style = widget.styleSheet()
                widget.setStyleSheet(original_style + "\nNoteBubble { border: 2px solid white; background-color: #444; }")
                QTimer.singleShot(1500, lambda w=widget, s=original_style: w.setStyleSheet(s))
                break

    def delete_note(self, page_num, annot_id):
        page = self.viewer.doc.load_page(page_num)
        for annot in page.annots():
            if annot.info.get("title") == annot_id:
                page.delete_annot(annot)
                break
        self.viewer.reload_page(page_num)
        self.refresh_notes()

    def change_note_color(self, page_num, annot_id, color_tuple):
        page = self.viewer.doc.load_page(page_num)
        for annot in page.annots():
            if annot.info.get("title") == annot_id:
                annot.set_colors(stroke=color_tuple)
                annot.update()
                break
        self.viewer.reload_page(page_num)
        self.refresh_notes()