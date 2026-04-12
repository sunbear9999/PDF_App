# gui/tabs/notes_tab.py
import json
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QScrollArea, QFrame, QComboBox, 
                             QStackedWidget, QColorDialog, QMessageBox)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from gui.components.workspace_view import WorkspaceView
from gui.components.help_dialog import HelpDialog

class NoteBubble(QFrame):
    def __init__(self, tab, pdf_path, page_num, annot_id, subject, content, color, is_ai=False):
        super().__init__()
        self.tab = tab
        self.pdf_path = pdf_path
        self.page_num = page_num
        self.annot_id = annot_id
        self.is_ai = is_ai
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        header_layout = QHBoxLayout()
        doc_name = os.path.basename(pdf_path)
        self.lbl_page = QLabel(f"📄 {doc_name} - Pg {page_num + 1}")
        header_layout.addWidget(self.lbl_page)
        
        if is_ai:
            self.lbl_ai = QLabel("🤖 AI Note")
            header_layout.addWidget(self.lbl_ai)
            
        header_layout.addStretch()
        
        colors = [("Yellow", (1.0, 0.9, 0.0)), ("Green", (0.0, 0.8, 0.4)), ("Blue", (0.2, 0.6, 1.0)), ("Purple", (0.7, 0.4, 1.0)), ("Red", (1.0, 0.3, 0.3))]
        def is_close(c1, c2):
            if not c1 or not c2 or len(c1) != len(c2): return False
            return all(abs(c1[i] - c2[i]) < 0.05 for i in range(len(c1)))

        for c_name, c_val in colors:
            btn_c = QPushButton()
            btn_c.setFixedSize(16, 16)
            border = "2px solid white" if is_close(c_val, color) else "none"
            btn_c.setStyleSheet(f"background-color: rgb({int(c_val[0]*255)}, {int(c_val[1]*255)}, {int(c_val[2]*255)}); border-radius: 8px; border: {border};")
            btn_c.setToolTip(f"Change to {c_name}")
            btn_c.clicked.connect(lambda checked, c=c_val: self.tab.change_note_color(self.pdf_path, self.page_num, self.annot_id, c))
            header_layout.addWidget(btn_c)
            
        header_layout.addSpacing(10)
            
        self.btn_del = QPushButton("✖")
        self.btn_del.setFixedSize(24, 24)
        self.btn_del.clicked.connect(self.delete_note)
        header_layout.addWidget(self.btn_del)
        layout.addLayout(header_layout)
        
        self.lbl_subj = QLabel(f'"{subject}"')
        self.lbl_subj.setWordWrap(True)
        self.lbl_subj.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.lbl_subj)
        
        if content:
            self.lbl_content = QLabel(content)
            self.lbl_content.setWordWrap(True)
            self.lbl_content.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            layout.addWidget(self.lbl_content)

        # Apply theme dynamically based on main window's theme manager
        if hasattr(self.tab, 'main_window') and hasattr(self.tab.main_window, 'theme_manager'):
            self.apply_theme(self.tab.main_window.theme_manager.get_theme())

    def apply_theme(self, theme):
        if self.is_ai:
            self.setStyleSheet(f"""
                NoteBubble {{ background-color: {theme['ai_bubble']}; border: 1px solid {theme['ai_bubble_border']}; border-radius: 8px; margin-bottom: 5px; }}
                NoteBubble:hover {{ border: 1px solid {theme['accent']}; background-color: {theme['ai_bubble_hover']}; }}
            """)
            if hasattr(self, 'lbl_ai'):
                self.lbl_ai.setStyleSheet(f"color: {theme['ai_bubble_border']}; font-weight: bold; font-size: 11px; border: none; margin-left: 10px;")
        else:
            self.setStyleSheet(f"""
                NoteBubble {{ background-color: {theme['user_bubble']}; border: 1px solid {theme['user_bubble_border']}; border-radius: 8px; margin-bottom: 5px; }}
                NoteBubble:hover {{ border: 1px solid {theme['accent']}; background-color: {theme['user_bubble_hover']}; }}
            """)
        
        self.lbl_page.setStyleSheet(f"font-weight: bold; color: {theme['text_muted']}; border: none;")
        self.lbl_subj.setStyleSheet(
            f"font-style: italic; color: {theme['text_muted']}; border: none; background: transparent; padding: 0px;"
        )
        
        if hasattr(self, 'lbl_content'):
            self.lbl_content.setStyleSheet(
                f"font-weight: bold; color: {theme['text_main']}; margin-top: 5px; border: none; background: transparent; padding: 0px;"
            )
            
        self.btn_del.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {theme['error']}; border: 1px solid {theme['error']}; border-radius: 4px; font-weight: bold; }}
            QPushButton:hover {{ background-color: {theme['error']}; color: #ffffff; }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.pdf_path != self.tab.main_window.current_file_path:
                self.tab.main_window.switch_to_pdf(self.pdf_path)
            self.tab.viewer.jump_to_page(self.page_num)
        super().mousePressEvent(event)

    def delete_note(self):
        self.tab.delete_note(self.pdf_path, self.page_num, self.annot_id)

class NotesTab(QWidget):
    def __init__(self, parent=None, viewer=None, main_window=None):
        super().__init__(parent)
        self.viewer = viewer
        self.main_window = main_window
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self.tab_scroll_area = QScrollArea(self)
        self.tab_scroll_area.setWidgetResizable(True)
        self.tab_scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.content_widget = QWidget()
        layout = QVBoxLayout(self.content_widget)
        layout.setContentsMargins(5, 10, 5, 5)
        
        top_layout = QHBoxLayout()
        self.lbl = QLabel("Linear Notes List:")
        top_layout.addWidget(self.lbl)
        
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["Current PDF", "Entire Project"])
        self.scope_combo.currentIndexChanged.connect(self.refresh_notes)
        top_layout.addWidget(self.scope_combo)
        top_layout.addStretch()
        layout.addLayout(top_layout)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setContentsMargins(5, 5, 5, 5)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        self.tab_scroll_area.setWidget(self.content_widget)
        outer_layout.addWidget(self.tab_scroll_area)

    def update_theme(self, theme):
        self.setStyleSheet(f"background-color: {theme['bg_main']};")
        self.tab_scroll_area.setStyleSheet("background: transparent; border: none;")
        self.tab_scroll_area.viewport().setStyleSheet(f"background-color: {theme['bg_main']};")
        self.content_widget.setStyleSheet(f"background-color: {theme['bg_main']};")
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.scroll_area.viewport().setStyleSheet(f"background-color: {theme['bg_main']};")
        self.scroll_content.setStyleSheet(f"background-color: {theme['bg_main']};")
        self.lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; padding-left: 5px; color: {theme['text_main']};")
        self.scope_combo.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};")
        
        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubble):
                widget.apply_theme(theme)

    def refresh_notes(self):
        try:
            for i in reversed(range(self.scroll_layout.count())): 
                widget = self.scroll_layout.itemAt(i).widget()
                if widget: widget.deleteLater()

            scope = self.scope_combo.currentText()
            paths_to_check = [self.main_window.current_file_path] if scope == "Current PDF" and self.main_window.current_file_path else self.main_window.project_manager.pdfs

            for path in paths_to_check:
                self._load_notes_from_pdf(path)
        except Exception as e:
            print(f"Error refreshing notes: {e}")

    def _load_notes_from_pdf(self, path):
        try:
            doc = self.main_window.project_manager.get_doc(path)
            if not doc: return
            for i in range(len(doc)):
                page = doc.load_page(i)
                for annot in page.annots():
                    if annot.info and (annot.info.get("title", "").startswith("UserNote") or annot.info.get("title", "").startswith("AINote")):
                        bubble = NoteBubble(self, path, i, annot.info.get("title"), annot.info.get("subject", ""), annot.info.get("content", ""), annot.colors.get("stroke"), is_ai=annot.info.get("title").startswith("AINote"))
                        self.scroll_layout.addWidget(bubble)
        except Exception as e:
            print(f"Error loading notes from {path}: {e}")

    def scroll_to_note(self, annot_id):
        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubble) and widget.annot_id == annot_id:
                self.scroll_area.ensureWidgetVisible(widget)
                theme = self.main_window.theme_manager.get_theme()
                original_style = widget.styleSheet()
                widget.setStyleSheet(original_style + f"\nNoteBubble {{ border: 2px solid {theme['accent']}; background-color: {theme['bg_input']}; }}")
                QTimer.singleShot(1500, lambda w=widget, s=original_style: w.setStyleSheet(s) if hasattr(w, 'setStyleSheet') else None)
                break

    def delete_note(self, pdf_path, page_num, annot_id):
        self._modify_note(pdf_path, page_num, annot_id, action="delete")

    def change_note_color(self, pdf_path, page_num, annot_id, color_tuple):
        self._modify_note(pdf_path, page_num, annot_id, action="color", color=color_tuple)

    def _modify_note(self, pdf_path, page_num, annot_id, action, color=None, content=None, refresh=True):
        try:
            doc = self.main_window.project_manager.get_doc(pdf_path)
            if not doc: return
            page = doc.load_page(page_num)
            for annot in page.annots():
                if annot.info and annot.info.get("title") == annot_id:
                    if action == "delete": page.delete_annot(annot)
                    elif action == "color": annot.set_colors(stroke=color); annot.update()
                    elif action == "edit_content":
                        new_info = dict(annot.info)
                        new_info["content"] = str(content)
                        annot.set_info(info=new_info)
                        annot.update()
                    break
            if pdf_path == self.main_window.current_file_path and self.viewer:
                self.viewer.reload_page(page_num)
            self.main_window.project_manager.mark_dirty(pdf_path)
            if refresh: self.refresh_notes()
        except Exception as e: print(f"Error applying annotation modification: {e}")