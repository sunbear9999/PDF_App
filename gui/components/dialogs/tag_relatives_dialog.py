import os
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QWidget, QFrame, QInputDialog, QMessageBox, QSizePolicy) 
from PySide6.QtCore import Qt, QTimer
from core.events.event_bus import EventBus
from core.events.domains.document_events import AnnotationIntent, AnnotationPayload, DocumentIntent, DocumentPayload

class AIResultsDialog(QDialog):
    # Added 'window_title' to handle both Tags and Opposing Views dynamically
    def __init__(self, window_title, matches, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.matches = matches
        self.bus = EventBus.get_instance()
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

        self.bus.document_action_requested.emit(
            DocumentIntent.OPEN,
            DocumentPayload(path=pdf_path),
        )
        QTimer.singleShot(
            150,
            lambda: self.bus.annotation_action_requested.emit(
                AnnotationIntent.JUMP_TO_PAGE,
                AnnotationPayload(page_num=match["page"], pdf_path=pdf_path),
            ),
        )

    def _save_as_note(self, match):
        note, ok = QInputDialog.getText(self, "Save Note", "Enter a note for this highlight:")
        if not ok: return

        pdf_path = next((p for p in self.main_window.project_manager.pdfs if os.path.basename(p) == match['doc_name']), None)
        if not pdf_path:
            QMessageBox.warning(self, "Error", "Document not found in project.")
            return

        self.bus.document_action_requested.emit(
            DocumentIntent.CREATE_HIGHLIGHT_FROM_TEXT,
            DocumentPayload(
                path=pdf_path,
                page_num=match["page"],
                text=match["text"],
                note=note,
                color=(0.7, 0.4, 1.0),
            ),
        )
