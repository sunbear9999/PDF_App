# gui/dock_panels/notes_dock.py
import os
from PyQt6.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QFrame, QComboBox,
                             QColorDialog, QMessageBox)
from PyQt6.QtCore import Qt, QTimer

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

class NotesDockWidget(QDockWidget):
    def __init__(self, viewer=None, main_window=None, parent=None):
        super().__init__("Notes", parent)
        self.viewer = viewer
        self.main_window = main_window

        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable |
                         QDockWidget.DockWidgetFeature.DockWidgetMovable |
                         QDockWidget.DockWidgetFeature.DockWidgetFloatable)

        widget = QWidget()
        self.setWidget(widget)
        layout = QVBoxLayout(widget)
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
        top_layout.addWidget(self.btn_help)

        self.btn_add_node = QPushButton("+ Add Node")
        self.btn_add_node.clicked.connect(self.add_node)
        top_layout.addWidget(self.btn_add_node)

        self.btn_open_workspace = QPushButton("Open Workspace")
        self.btn_open_workspace.clicked.connect(self.focus_workspace)
        top_layout.addWidget(self.btn_open_workspace)

        layout.addLayout(top_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_layout.setContentsMargins(5, 5, 5, 5)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

    def update_theme(self, theme):
        self.lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; padding-left: 5px; color: {theme['text_main']};")

        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubble):
                widget.apply_theme(theme)

    def show_workspace_help(self):
        help_text = (
            "<h3>Workspace Controls</h3>"
            "<ul>"
            "<li><b>Left Click + Drag:</b> Move nodes / Pan canvas</li>"
            "<li><b>Shift + Click & Drag:</b> Select multiple nodes</li>"
            "<li><b>Shift + Scroll:</b> Zoom in/out</li>"
            "<li><b>Right Click:</b> Context menu for nodes</li>"
            "<li><b>Delete Key:</b> Remove selected nodes</li>"
            "<li><b>Ctrl+Z / Ctrl+Y:</b> Undo/Redo</li>"
            "</ul>"
            "<p>Nodes represent ideas, quotes, or concepts. Connect them with edges to build argument maps.</p>"
        )
        QMessageBox.information(self, "Workspace Help", help_text)

    def focus_workspace(self):
        if hasattr(self.main_window, 'workspace_view'):
            self.main_window.workspace_view.setFocus()
            self.main_window.dock_widgets["Notes"].show()

    def add_node(self):
        if hasattr(self.main_window, 'workspace_view'):
            self.main_window.workspace_view.add_custom_bubble()

    def refresh_notes(self):
        # Clear existing notes
        for i in reversed(range(self.scroll_layout.count())):
            widget = self.scroll_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        scope = self.scope_combo.currentText()
        if scope == "Current PDF":
            pdf_paths = [self.main_window.current_file_path] if self.main_window.current_file_path else []
        else:
            pdf_paths = self.main_window.pdf_controller.get_pdf_paths()

        for pdf_path in pdf_paths:
            doc = self.main_window.pdf_controller.get_doc(pdf_path)
            if not doc:
                continue
            for page_num in range(len(doc)):
                try:
                    for annot in doc.load_page(page_num).annots():
                        if annot.info and annot.info.get("title"):
                            title = annot.info.get("title")
                            if title.startswith("UserNote") or title.startswith("AINote"):
                                subject = annot.info.get("subject", "")
                                content = annot.info.get("content", "")
                                color = annot.colors.get("stroke", (1.0, 0.9, 0.0))  # Default yellow
                                is_ai = title.startswith("AINote")
                                bubble = NoteBubble(self, pdf_path, page_num, title, subject, content, color, is_ai)
                                self.scroll_layout.addWidget(bubble)
                except:
                    pass

    def change_note_color(self, pdf_path, page_num, annot_id, new_color):
        doc = self.main_window.pdf_controller.get_doc(pdf_path)
        if doc:
            page = doc.load_page(page_num)
            for annot in page.annots():
                if annot.info.get("title") == annot_id:
                    annot.set_colors(stroke=new_color)
                    annot.update()
                    self.main_window.pdf_controller.mark_dirty(pdf_path)
                    break
        self.refresh_notes()

    def delete_note(self, pdf_path, page_num, annot_id):
        doc = self.main_window.pdf_controller.get_doc(pdf_path)
        if doc:
            page = doc.load_page(page_num)
            for annot in page.annots():
                if annot.info.get("title") == annot_id:
                    page.delete_annot(annot)
                    self.main_window.pdf_controller.mark_dirty(pdf_path)
                    break
        self.refresh_notes()

    def scroll_to_note(self, annot_id):
        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubble) and widget.annot_id == annot_id:
                self.scroll_area.ensureWidgetVisible(widget)
                break