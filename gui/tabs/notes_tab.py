import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QScrollArea, QFrame, QComboBox, 
                             QStackedWidget, QColorDialog)
from PyQt6.QtCore import Qt, QTimer
from gui.components.workspace_view import WorkspaceView

class NoteBubble(QFrame):
    def __init__(self, tab, pdf_path, page_num, annot_id, subject, content, color, is_ai=False):
        super().__init__()
        self.tab = tab
        self.pdf_path = pdf_path
        self.page_num = page_num
        self.annot_id = annot_id
        
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
        
        header_layout = QHBoxLayout()
        doc_name = os.path.basename(pdf_path)
        lbl_page = QLabel(f"📄 {doc_name} - Pg {page_num + 1}")
        lbl_page.setStyleSheet("font-weight: bold; color: #aaa; border: none;")
        header_layout.addWidget(lbl_page)
        
        if is_ai:
            lbl_ai = QLabel("🤖 AI Note")
            lbl_ai.setStyleSheet("color: #d194ff; font-weight: bold; font-size: 11px; border: none; margin-left: 10px;")
            header_layout.addWidget(lbl_ai)
            
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
            
        btn_del = QPushButton("✖")
        btn_del.setFixedSize(24, 24)
        btn_del.setStyleSheet("""
            QPushButton { background-color: #442222; color: #ff6666; border: 1px solid #662222; border-radius: 4px; font-weight: bold;}
            QPushButton:hover { background-color: #ff4444; color: white; }
        """)
        btn_del.clicked.connect(self.delete_note)
        header_layout.addWidget(btn_del)
        layout.addLayout(header_layout)
        
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 10, 5, 5)
        
        top_layout = QHBoxLayout()
        lbl = QLabel("Notes:")
        lbl.setStyleSheet("font-size: 16px; font-weight: bold; padding-left: 5px;")
        top_layout.addWidget(lbl)
        
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["Current PDF", "Entire Project"])
        self.scope_combo.setStyleSheet("background: #333; border: 1px solid #555; padding: 2px;")
        self.scope_combo.currentIndexChanged.connect(self.refresh_notes)
        top_layout.addWidget(self.scope_combo)
        
        top_layout.addStretch()
        
        # --- Workspace Tool Buttons ---
        self.btn_zoom_out = QPushButton("➖")
        self.btn_zoom_out.setStyleSheet("background-color: #333;")
        self.btn_zoom_out.clicked.connect(lambda: self.workspace_view.zoom_out())
        self.btn_zoom_out.hide()
        
        self.btn_zoom_in = QPushButton("➕")
        self.btn_zoom_in.setStyleSheet("background-color: #333;")
        self.btn_zoom_in.clicked.connect(lambda: self.workspace_view.zoom_in())
        self.btn_zoom_in.hide()
        
        top_layout.addWidget(self.btn_zoom_out)
        top_layout.addWidget(self.btn_zoom_in)
        
        self.btn_add_bubble = QPushButton("+ Main Idea")
        self.btn_add_bubble.setStyleSheet("background-color: #0078D7; font-weight: bold;")
        self.btn_add_bubble.clicked.connect(self.add_bubble)
        self.btn_add_bubble.hide()
        top_layout.addWidget(self.btn_add_bubble)
        
        self.btn_toggle_view = QPushButton("Switch to Workspace")
        self.btn_toggle_view.setStyleSheet("background-color: #444; font-weight: bold;")
        self.btn_toggle_view.clicked.connect(self.toggle_view)
        top_layout.addWidget(self.btn_toggle_view)
        
        layout.addLayout(top_layout)
        
        self.stack = QStackedWidget()
        
        self.list_view_widget = QWidget()
        list_layout = QVBoxLayout(self.list_view_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setContentsMargins(5, 5, 5, 5)
        self.scroll_area.setWidget(self.scroll_content)
        list_layout.addWidget(self.scroll_area)
        
        self.stack.addWidget(self.list_view_widget)
        
        self.workspace_view = WorkspaceView(self.main_window)
        self.stack.addWidget(self.workspace_view)
        
        layout.addWidget(self.stack)

    def toggle_view(self):
        if self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)
            self.btn_toggle_view.setText("Switch to List")
            self.btn_add_bubble.show()
            self.btn_zoom_in.show()
            self.btn_zoom_out.show()
            self.scope_combo.hide()
            self._sync_workspace()
        else:
            self.stack.setCurrentIndex(0)
            self.btn_toggle_view.setText("Switch to Workspace")
            self.btn_add_bubble.hide()
            self.btn_zoom_in.hide()
            self.btn_zoom_out.hide()
            self.scope_combo.show()

    def add_bubble(self):
        self.workspace_view.add_custom_bubble()

    def save_workspace_state(self):
        if hasattr(self, 'workspace_view'):
            data = self.workspace_view.serialize_workspace()
            self.main_window.project_manager.workspace_data = data

    def _get_all_project_annotations_for_workspace(self):
        annots = []
        for path in self.main_window.project_manager.pdfs:
            try:
                doc = self.main_window.project_manager.get_doc(path)
                for i in range(len(doc)):
                    page = doc.load_page(i)
                    for annot in page.annots():
                        title = annot.info.get("title", "")
                        if title.startswith("UserNote") or title.startswith("AINote"):
                            annots.append({
                                "id": title,
                                "subject": annot.info.get("subject", ""),
                                "content": annot.info.get("content", ""),
                                "pdf_path": path,     # Passed to node so it can edit the backend
                                "page_num": i         # Passed to node so it can edit the backend
                            })
            except: pass
        return annots

    def _sync_workspace(self):
        if not self.main_window.project_manager.project_filepath: return
        workspace_data = self.main_window.project_manager.workspace_data
        all_annots = self._get_all_project_annotations_for_workspace()
        self.workspace_view.sync_with_project(workspace_data, all_annots)

    def refresh_notes(self):
        for i in reversed(range(self.scroll_layout.count())): 
            widget = self.scroll_layout.itemAt(i).widget()
            if widget: widget.deleteLater()
            
        scope = self.scope_combo.currentText()
        paths_to_check = []
        
        if scope == "Current PDF" and self.main_window.current_file_path:
            paths_to_check = [self.main_window.current_file_path]
        elif scope == "Entire Project":
            paths_to_check = self.main_window.project_manager.pdfs
            
        for path in paths_to_check:
            self._load_notes_from_pdf(path)
            
        if self.stack.currentIndex() == 1:
            self._sync_workspace()

    def _load_notes_from_pdf(self, path):
        try:
            doc = self.main_window.project_manager.get_doc(path)
            for i in range(len(doc)):
                page = doc.load_page(i)
                for annot in page.annots():
                    title = annot.info.get("title", "")
                    if title.startswith("UserNote") or title.startswith("AINote"):
                        is_ai = title.startswith("AINote")
                        bubble = NoteBubble(self, path, i, title, annot.info.get("subject", ""), annot.info.get("content", ""), annot.colors.get("stroke"), is_ai=is_ai)
                        self.scroll_layout.addWidget(bubble)
        except: pass

    def scroll_to_note(self, annot_id):
        if self.stack.currentIndex() == 1:
            self.toggle_view()
            
        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubble) and widget.annot_id == annot_id:
                self.scroll_area.ensureWidgetVisible(widget)
                original_style = widget.styleSheet()
                widget.setStyleSheet(original_style + "\nNoteBubble { border: 2px solid white; background-color: #444; }")
                QTimer.singleShot(1500, lambda w=widget, s=original_style: w.setStyleSheet(s))
                break

    def delete_note(self, pdf_path, page_num, annot_id):
        self._modify_note(pdf_path, page_num, annot_id, action="delete")

    def change_note_color(self, pdf_path, page_num, annot_id, color_tuple):
        self._modify_note(pdf_path, page_num, annot_id, action="color", color=color_tuple)

    def _modify_note(self, pdf_path, page_num, annot_id, action, color=None, content=None):
        doc = self.main_window.project_manager.get_doc(pdf_path)
        is_active = (pdf_path == self.main_window.current_file_path)
        
        page = doc.load_page(page_num)
        for annot in page.annots():
            if annot.info.get("title") == annot_id:
                if action == "delete": 
                    page.delete_annot(annot)
                elif action == "color":
                    annot.set_colors(stroke=color)
                    annot.update()
                elif action == "edit_content":
                    # Updates the underlying note text in the PDF
                    info = annot.info
                    info["content"] = content
                    annot.set_info(info)
                    annot.update()
                break
                
        if is_active: self.viewer.reload_page(page_num)
        
        self.main_window.project_manager.mark_dirty(pdf_path)
        self.refresh_notes()