# gui/components/dialogs/extract_pages_dialog.py
import os
import fitz
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QMessageBox, QFileDialog)
from PySide6.QtCore import Qt

class ExtractPagesDialog(QDialog):
    def __init__(self, source_pdf_path, project_manager, parent=None):
        super().__init__(parent)
        self.source_pdf_path = source_pdf_path
        self.project_manager = project_manager
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
        default_name = os.path.basename(source_pdf_path).replace(".pdf", "_extracted.pdf")
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

    def _parse_page_string(self, page_str, max_pages):
        """Converts strings like '1-3, 5' into a sorted list of 0-indexed integers [0, 1, 2, 4]"""
        pages = set()
        try:
            for part in page_str.split(','):
                part = part.strip()
                if not part: continue
                
                if '-' in part:
                    start, end = part.split('-')
                    start_idx = int(start.strip()) - 1
                    end_idx = int(end.strip()) - 1
                    pages.update(range(start_idx, end_idx + 1))
                else:
                    pages.add(int(part.strip()) - 1)
                    
            # Filter out out-of-bounds pages and sort
            valid_pages = sorted([p for p in pages if 0 <= p < max_pages])
            return valid_pages
        except Exception:
            return None

    def _perform_extraction(self):
        page_str = self.page_input.text().strip()
        new_name = self.name_input.text().strip()

        if not page_str or not new_name:
            QMessageBox.warning(self, "Input Error", "Please provide both a page range and a file name.")
            return
            
        if not new_name.lower().endswith(".pdf"):
            new_name += ".pdf"

        try:
            src_doc = fitz.open(self.source_pdf_path)
            max_pages = len(src_doc)
            
            pages_to_extract = self._parse_page_string(page_str, max_pages)
            
            if not pages_to_extract:
                QMessageBox.warning(self, "Invalid Range", f"Could not parse pages. Ensure they are between 1 and {max_pages}.")
                src_doc.close()
                return

            # Ask user where to save the new PDF
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save Extracted PDF", 
                os.path.join(os.path.dirname(self.source_pdf_path), new_name), 
                "PDF Files (*.pdf)"
            )

            if not save_path:
                src_doc.close()
                return

            # Execute the extraction
            dest_doc = fitz.open()
            for p_num in pages_to_extract:
                dest_doc.insert_pdf(src_doc, from_page=p_num, to_page=p_num)
                
            dest_doc.save(save_path)
            dest_doc.close()
            src_doc.close()

            # Add the newly created PDF to the project
            if self.project_manager:
                self.project_manager.add_pdf(save_path)
                
            QMessageBox.information(self, "Success", f"Successfully extracted {len(pages_to_extract)} pages!")
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Extraction Failed", f"An error occurred:\n{str(e)}")