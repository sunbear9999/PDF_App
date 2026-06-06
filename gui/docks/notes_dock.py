import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QFrame, QComboBox,
                             QMenu)
from PySide6.QtCore import Qt, QTimer
from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
from core.events.event_bus import EventBus
from core.events.domains.document_events import AnnotationIntent, AnnotationPayload, DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload
from core.events.domains.metadata_events import NotesEvent, NotesEventPayload, NotesIntent, NotesPayload

class NoteBubble(QFrame):
    def __init__(self, tab, data_dict):
        super().__init__()
        self.tab = tab
        self.pdf_path = data_dict["pdf_path"]
        self.page_num = data_dict["page_num"]
        self.annot_id = data_dict["annot_id"]
        self.is_ai = data_dict["is_ai"]
        color = data_dict["color"]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header_layout = QHBoxLayout()
        doc_name = os.path.basename(self.pdf_path)
        self.lbl_page = QLabel(f"📄 {doc_name} - Pg {self.page_num + 1}")
        header_layout.addWidget(self.lbl_page)

        # Render Tag Dots from passed data
        for t in data_dict.get("tags", []):
            tag_name = t.get("name", "")
            tag_color = t.get("color", "#808080")
            btn_tag = QPushButton()
            btn_tag.setFixedSize(12, 12)
            btn_tag.setStyleSheet(f"background-color: {tag_color}; border-radius: 6px; border: none;")
            btn_tag.setToolTip(f"Filter by tag: {tag_name}")
            btn_tag.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_tag.clicked.connect(lambda checked, name=tag_name: self.tab.apply_tag_filter(name))
            header_layout.addWidget(btn_tag)

        if self.is_ai:
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

            # Emit Intent instead of calling backend
            btn_c.clicked.connect(lambda checked, c=c_val: self.tab.bus.notes_action_requested.emit(
                NotesIntent.CHANGE_COLOR,
                NotesPayload(pdf_path=self.pdf_path, page_num=self.page_num, annot_id=self.annot_id, color=c),
            ))
            header_layout.addWidget(btn_c)

        header_layout.addSpacing(10)

        self.btn_del = QPushButton("✖")
        self.btn_del.setFixedSize(24, 24)

        # Emit Intent instead of calling backend
        self.btn_del.clicked.connect(lambda: self.tab.bus.notes_action_requested.emit(
            NotesIntent.DELETE,
            NotesPayload(pdf_path=self.pdf_path, page_num=self.page_num, annot_id=self.annot_id),
        ))

        header_layout.addWidget(self.btn_del)
        layout.addLayout(header_layout)

        self.lbl_subj = QLabel(f'"{data_dict["subject"]}"')
        self.lbl_subj.setWordWrap(True)
        self.lbl_subj.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.lbl_subj)

        if data_dict["content"]:
            self.lbl_content = QLabel(data_dict["content"])
            self.lbl_content.setWordWrap(True)
            self.lbl_content.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            layout.addWidget(self.lbl_content)

        if hasattr(self.tab, 'main_window') and hasattr(self.tab.main_window, 'theme_manager'):
            self.apply_theme(self.tab.main_window.theme_manager.get_theme())

    def apply_theme(self, theme):
        # [KEEP EXISTING THEME LOGIC EXACTLY AS IS]
        pass

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Emit Intent to open PDF and jump to page
            self.tab.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=self.pdf_path))
            self.tab.bus.annotation_action_requested.emit(AnnotationIntent.JUMP_TO_PAGE, AnnotationPayload(page_num=self.page_num))
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        if hasattr(self.tab, 'main_window') and hasattr(self.tab.main_window, 'theme_manager'):
            theme = self.tab.main_window.theme_manager.get_theme()
            menu.setStyleSheet(f"QMenu {{ background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 5px; font-weight: bold; }} QMenu::item:selected {{ background-color: {theme['accent']}; color: #ffffff; }}")

        tag_action = menu.addAction("🏷️ Manage Tags")
        if menu.exec(event.globalPos()) == tag_action:
            self.manage_tags()

    def manage_tags(self):
        dlg = TagAssignmentDialog(self.annot_id, "node", self)
        if dlg.exec():
            # Emit Intent: Tell the service to sync these new tags to the workspace
            self.tab.bus.notes_action_requested.emit(NotesIntent.SYNC_TAGS, NotesPayload(annot_id=self.annot_id))


class NotesTab(QWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.main_window = main_window
        self.bus = EventBus.get_instance()

        self._build_ui()

        # Listen to Event Bus
        self.bus.notes_data_ready.connect(self._handle_notes_event)
        self.bus.highlight_created.connect(self._handle_document_event)
        self.bus.highlight_updated.connect(self._handle_document_event)
        self.bus.highlight_deleted.connect(self._handle_document_event)
        self.bus.pdf_switched.connect(self._handle_document_event)

    def _handle_notes_event(self, event: NotesEvent, payload: NotesEventPayload):
        if event == NotesEvent.DATA_READY:
            self._render_notes(payload.notes)

    def _handle_document_event(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event in {
            DocumentEvent.HIGHLIGHT_CREATED,
            DocumentEvent.HIGHLIGHT_UPDATED,
            DocumentEvent.HIGHLIGHT_DELETED,
            DocumentEvent.PDF_SWITCHED,
        }:
            self.refresh_notes()

    def _build_ui(self):
        # [KEEP ALL UI LAYOUT CODE EXACTLY AS IS]
        pass

    def apply_tag_filter(self, tag_name):
        index = self.tag_combo.findData(tag_name)
        if index >= 0:
            self.tag_combo.setCurrentIndex(index)

    def refresh_tag_list(self):
        current_tag = self.tag_combo.currentData()
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem("All Tags", None)

        if self.main_window and hasattr(self.main_window, 'project_manager'):
            tags = self.main_window.project_manager.get_all_tags()
            for t in tags:
                self.tag_combo.addItem(t["name"], t["name"])

        index = self.tag_combo.findData(current_tag)
        if index >= 0: self.tag_combo.setCurrentIndex(index)
        self.tag_combo.blockSignals(False)

    def refresh_notes(self):
        self.refresh_tag_list()

        # Emit Intent to background service
        self.bus.notes_action_requested.emit(
            NotesIntent.FETCH,
            NotesPayload(
                scope=self.scope_combo.currentText(),
                tag=self.tag_combo.currentData(),
                active_pdf=self.main_window.current_file_path if self.main_window else None,
            ),
        )

    def _render_notes(self, notes_data_list):
        """Called by the EventBus when the background service finishes parsing the PDFs."""
        for i in reversed(range(self.scroll_layout.count())):
            widget = self.scroll_layout.itemAt(i).widget()
            if widget: widget.deleteLater()

        for data in notes_data_list:
            bubble = NoteBubble(self, data)
            self.scroll_layout.addWidget(bubble)

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
        self.tag_combo.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']};")
        for i in range(self.scroll_layout.count()):
            widget = self.scroll_layout.itemAt(i).widget()
            if isinstance(widget, NoteBubble):
                widget.apply_theme(theme)
