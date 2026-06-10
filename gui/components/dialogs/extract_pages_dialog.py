# gui/components/dialogs/extract_pages_dialog.py
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QMessageBox, QFileDialog)
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentIntent, DocumentPayload

class ExtractPagesDialog(QDialog):
    def __init__(self, source_pdf_path, project_manager, parent=None):
        super().__init__(parent)
        self.source_pdf_path = source_pdf_path
        self.bus = EventBus.get_instance()
        self.setWindowTitle("Extract Pages to New PDF")
        self.setMinimumWidth(400)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #ddd; }
            QLabel { color: #ddd; font-weight: bold; }
            QLineEdit { background-color: #2b2b2b; color: #fff; border: 1px solid #444; padding: 6px; border-radius: 4px; }
            QPushButton { background-color: #0078D7; color: white; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #005A9E; }
            QPushButton#cancel { background-color: #444; }
            QPushButton#cancel:hover { background-color: #555; }
        """)

        layout = QVBoxLayout(self)

        # 1. Page Range Input
        layout.addWidget(QLabel("Pages to Extract (e.g., 1-5, 8, 11-13):"))
        self.page_input = QLineEdit()
        self.page_input.setPlaceholderText("Enter page numbers...")
        layout.addWidget(self.page_input)

        # 2. File Name Input
        layout.addWidget(QLabel("New PDF Name:"))
        self.name_input = QLineEdit()
        source = Path(source_pdf_path)
        default_name = f"{source.stem}_extracted.pdf"
        self.name_input.setText(default_name)
        layout.addWidget(self.name_input)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_extract = QPushButton("Extract && Save")
        self.btn_extract.clicked.connect(self._perform_extraction)
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("cancel")
        self.btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_extract)
        layout.addLayout(btn_layout)

    def _perform_extraction(self):
        page_str = self.page_input.text().strip()
        new_name = self.name_input.text().strip()

        if not page_str or not new_name:
            QMessageBox.warning(self, "Input Error", "Please provide both a page range and a file name.")
            return
            
        if not new_name.lower().endswith(".pdf"):
            new_name += ".pdf"

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Extracted PDF",
            str(Path(self.source_pdf_path).with_name(new_name)),
            "PDF Files (*.pdf)",
        )
        if not save_path:
            return

        self.bus.document_action_requested.emit(
            DocumentIntent.EXTRACT_PAGES,
            DocumentPayload(path=self.source_pdf_path, save_path=save_path, page_range=page_str),
        )
        self.accept()
