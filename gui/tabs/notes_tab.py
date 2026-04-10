# gui/tabs/notes_tab.py
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QScrollArea, QFrame, QComboBox, 
                             QStackedWidget, QColorDialog, QMessageBox)
from PyQt6.QtCore import Qt, QTimer
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
        layout.addWidget(self.lbl_subj)
        
        if content:
            self.lbl_content = QLabel(content)
            self.lbl_content.setWordWrap(True)
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
        self.lbl_subj.setStyleSheet(f"font-style: italic; color: {theme['text_muted']}; border: none;")
        
        if hasattr(self, 'lbl_content'):
            self.lbl_content.setStyleSheet(f"font-weight: bold; color: {theme['text_main']}; margin-top: 5px; border: none;")
            
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 10, 5, 5)
        
        top_layout = QHBoxLayout()
        self.lbl = QLabel("Notes:")
        top_layout.addWidget(self.lbl)
        
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["Current PDF", "Entire Project"])
        self.scope_combo.currentIndexChanged.connect(self.refresh_notes)
        top_layout.addWidget(self.scope_combo)
        
        top_layout.addStretch()
        
        self.btn_help = QPushButton("❓")
        self.btn_help.clicked.connect(self.show_workspace_help)
        self.btn_help.hide()
        
        self.btn_undo = QPushButton("↩️ Undo")
        self.btn_undo.hide()
        
        self.btn_redo = QPushButton("↪️ Redo")
        self.btn_redo.hide()

        self.btn_zoom_out = QPushButton("➖")
        self.btn_zoom_out.hide()
        
        self.btn_zoom_in = QPushButton("➕")
        self.btn_zoom_in.hide()
        
        top_layout.addWidget(self.btn_help)
        top_layout.addWidget(self.btn_undo)
        top_layout.addWidget(self.btn_redo)
        top_layout.addWidget(self.btn_zoom_out)
        top_layout.addWidget(self.btn_zoom_in)
        
        self.btn_add_bubble = QPushButton("+ Main Idea")
        self.btn_add_bubble.clicked.connect(self.add_bubble)
        self.btn_add_bubble.hide()
        top_layout.addWidget(self.btn_add_bubble)
        
        self.btn_toggle_view = QPushButton("Switch to Workspace")
        self.btn_toggle_view.clicked.connect(self.toggle_view)
        top_layout.addWidget(self.btn_toggle_view)
        
        layout.addLayout(top_layout)
        
        self.stack = QStackedWidget()
        
        self.list_view_widget = QWidget()
        list_layout = QVBoxLayout(self.list_view_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.scroll_area.viewport().update)
        self.scroll_area.horizontalScrollBar().valueChanged.connect(self.scroll_area.viewport().update)
        
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setContentsMargins(5, 5, 5, 5)
        self.scroll_area.setWidget(self.scroll_content)
        list_layout.addWidget(self.scroll_area)
        
        self.stack.addWidget(self.list_view_widget)
        
        self.workspace_view = WorkspaceView(self.main_window)
        self.stack.addWidget(self.workspace_view)
        
        self.btn_undo.clicked.connect(self.workspace_view.undo)
        self.btn_redo.clicked.connect(self.workspace_view.redo)
        self.btn_zoom_out.clicked.connect(lambda: self.workspace_view.zoom_out())
        self.btn_zoom_in.clicked.connect(lambda: self.workspace_view.zoom_in())
        
        layout.addWidget(self.stack)

    def update_theme(self, theme):
        self.lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; padding-left: 5px; color: {theme['text_main']};")
        self.btn_add_bubble.setStyleSheet(f"background-color: {theme['accent']}; color: #ffffff; font-weight: bold; border: none; padding: 6px 12px; border-radius: 4px;")
        
        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubble):
                widget.apply_theme(theme)
                
        if hasattr(self, 'workspace_view'):
            self.workspace_view.update_theme(theme)

    def show_workspace_help(self):
        self.help_dialog = HelpDialog(self,initial_tab_index=3)
        self.help_dialog.show()

    def update_undo_redo_buttons(self):
        if hasattr(self, 'workspace_view'):
            self.btn_undo.setEnabled(len(self.workspace_view.undo_stack) > 0)
            self.btn_redo.setEnabled(len(self.workspace_view.redo_stack) > 0)

    def toggle_view(self):
        try:
            if self.stack.currentIndex() == 0:
                self.stack.setCurrentIndex(1)
                self.btn_toggle_view.setText("Switch to List")
                self.btn_add_bubble.show()
                self.btn_undo.show()
                self.btn_redo.show()
                self.btn_zoom_in.show()
                self.btn_zoom_out.show()
                self.btn_help.show()
                self.scope_combo.hide()
                self.update_undo_redo_buttons()
                self._sync_workspace()
            else:
                self.save_workspace_state() 
                self.stack.setCurrentIndex(0)
                self.btn_toggle_view.setText("Switch to Workspace")
                self.btn_add_bubble.hide()
                self.btn_undo.hide()
                self.btn_redo.hide()
                self.btn_zoom_in.hide()
                self.btn_zoom_out.hide()
                self.btn_help.hide()
                self.scope_combo.show()
                self.refresh_notes()
        except Exception as e:
            print(f"Error toggling view: {e}")

    def add_bubble(self):
        self.workspace_view.add_custom_bubble()

    def save_workspace_state(self):
        if hasattr(self, 'workspace_view'):
            data = self.workspace_view.serialize_workspace()
            self.main_window.project_manager.save_workspace_data(data)
            self.main_window.project_manager.mark_dirty("workspace")

    def _get_all_project_annotations_for_workspace(self):
        annots = []
        for path in self.main_window.project_manager.pdfs:
            try:
                doc = self.main_window.project_manager.get_doc(path)
                if not doc: continue
                for i in range(len(doc)):
                    page = doc.load_page(i)
                    for annot in page.annots():
                        info = annot.info
                        if info:
                            title = info.get("title", "")
                            if title.startswith("UserNote") or title.startswith("AINote"):
                                annots.append({
                                    "id": title,
                                    "subject": info.get("subject", ""),
                                    "content": info.get("content", ""),
                                    "pdf_path": path,     
                                    "page_num": i         
                                })
            except Exception as e: 
                print(f"Error extracting annotations from {path}: {e}")
        return annots

    def _sync_workspace(self, force_reload=False):
        try:
            if not self.main_window.project_manager.project_filepath: return
            workspace_data = self.main_window.project_manager.get_workspace_data()
            all_annots = self._get_all_project_annotations_for_workspace()
            self.workspace_view.sync_with_project(workspace_data, all_annots)
        except Exception as e:
            print(f"Error syncing workspace: {e}")

    def refresh_notes(self):
        try:
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
        except Exception as e:
            print(f"Error refreshing notes: {e}")


    def _load_notes_from_pdf(self, path):
        try:
            doc = self.main_window.project_manager.get_doc(path)
            if not doc: return
            for i in range(len(doc)):
                page = doc.load_page(i)
                for annot in page.annots():
                    info = annot.info
                    if info:
                        title = info.get("title", "")
                        if title.startswith("UserNote") or title.startswith("AINote"):
                            is_ai = title.startswith("AINote")
                            bubble = NoteBubble(self, path, i, title, info.get("subject", ""), info.get("content", ""), annot.colors.get("stroke"), is_ai=is_ai)
                            self.scroll_layout.addWidget(bubble)
        except Exception as e:
            print(f"Error loading notes from {path}: {e}")

    def scroll_to_note(self, annot_id):
        if self.stack.currentIndex() == 1:
            self.toggle_view()
            
        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubble) and widget.annot_id == annot_id:
                self.scroll_area.ensureWidgetVisible(widget)
                
                # Dynamic border focus based on theme
                theme = self.main_window.theme_manager.get_theme()
                original_style = widget.styleSheet()
                widget.setStyleSheet(original_style + f"\nNoteBubble {{ border: 2px solid {theme['accent']}; background-color: {theme['bg_input']}; }}")
                
                def revert_style(w=widget, s=original_style):
                    try:
                        w.setStyleSheet(s)
                    except RuntimeError:
                        pass
                        
                QTimer.singleShot(1500, revert_style)
                break

    def delete_note(self, pdf_path, page_num, annot_id):
        self._modify_note(pdf_path, page_num, annot_id, action="delete")

    def change_note_color(self, pdf_path, page_num, annot_id, color_tuple):
        self._modify_note(pdf_path, page_num, annot_id, action="color", color=color_tuple)

    def _modify_note(self, pdf_path, page_num, annot_id, action, color=None, content=None, refresh=True):
        try:
            doc = self.main_window.project_manager.get_doc(pdf_path)
            if not doc: return
            
            is_active = (pdf_path == self.main_window.current_file_path)
            page = doc.load_page(page_num)
            
            for annot in page.annots():
                info = annot.info
                if info and info.get("title") == annot_id:
                    if action == "delete": 
                        page.delete_annot(annot)
                    elif action == "color":
                        annot.set_colors(stroke=color)
                        annot.update()
                    elif action == "edit_content":
                        new_info = dict(info)
                        new_info["content"] = str(content)
                        annot.set_info(info=new_info)
                        annot.update()
                    break
                    
            if is_active and self.viewer:
                self.viewer.reload_page(page_num)
            
            self.main_window.project_manager.mark_dirty(pdf_path)
            
            if refresh:
                self.refresh_notes()
        except Exception as e:
            print(f"Error applying annotation modification: {e}")