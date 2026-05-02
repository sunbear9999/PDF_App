# gui/docks/citation_dock.py
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, 
                               QTableWidgetItem, QPushButton, QComboBox, QLabel, 
                               QHeaderView, QApplication, QMessageBox)
from PySide6.QtCore import Qt

class CitationDock(QWidget):
    def __init__(self, citation_manager, project_manager, parent=None):
        super().__init__(parent)
        self.cm = citation_manager
        self.pm = project_manager
        
        layout = QVBoxLayout(self)
        
        # --- Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Citation Style:"))
        self.style_combo = QComboBox()
        self.style_combo.addItems(["APA", "MLA", "Chicago"])
        self.style_combo.currentTextChanged.connect(self.cm.set_style)
        toolbar.addWidget(self.style_combo)
        toolbar.addStretch()
        
        self.btn_refresh = QPushButton("🔄 Refresh Data")
        self.btn_refresh.clicked.connect(self.populate_table)
        toolbar.addWidget(self.btn_refresh)
        layout.addLayout(toolbar)

        # --- Table ---
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Use", "File Name", "Title", "Author(s)", "Year", "Journal/DOI"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.save_edits)
        layout.addWidget(self.table)

        # --- Bottom Toolbar ---
        self.btn_generate = QPushButton("📝 Generate Works Cited")
        self.btn_generate.setStyleSheet("background-color: #0078D7; color: white; font-weight: bold; padding: 8px;")
        self.btn_generate.clicked.connect(self.generate_works_cited)
        layout.addWidget(self.btn_generate)

        self.populate_table()

    def populate_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        
        for doc_path in self.pm.pdfs:
            # 1. Try to get existing from DB
            data = self.pm.get_citation(doc_path)
            
            # 2. If it doesn't exist, auto-extract and save
            if not data or not data.get("title"):
                data = self.cm.extract_metadata(doc_path)
                self.pm.upsert_citation(data)

            row = self.table.rowCount()
            self.table.insertRow(row)

            # Checkbox
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Checked)
            chk_item.setData(Qt.ItemDataRole.UserRole, doc_path) # Store doc path silently
            self.table.setItem(row, 0, chk_item)

            # Read-only File Name
            file_item = QTableWidgetItem(os.path.basename(doc_path))
            file_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 1, file_item)

            # Editable fields
            self.table.setItem(row, 2, QTableWidgetItem(data.get("title", "")))
            self.table.setItem(row, 3, QTableWidgetItem(data.get("authors", "")))
            self.table.setItem(row, 4, QTableWidgetItem(data.get("year", "")))
            self.table.setItem(row, 5, QTableWidgetItem(data.get("doi", "") or data.get("journal", "")))

        self.table.blockSignals(False)

    def save_edits(self, item):
        row = item.row()
        doc_path = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        
        data = {
            "doc_id": doc_path,
            "title": self.table.item(row, 2).text(),
            "authors": self.table.item(row, 3).text(),
            "year": self.table.item(row, 4).text(),
            "journal": self.table.item(row, 5).text(), # Storing journal/DOI in same field for brevity
            "doi": self.table.item(row, 5).text()
        }
        self.pm.upsert_citation(data)

    def generate_works_cited(self):
        selected_docs = []
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                selected_docs.append(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
                
        if not selected_docs:
            QMessageBox.warning(self, "No Docs Selected", "Please select at least one document.")
            return

        works_cited = self.cm.format_works_cited(selected_docs)
        clipboard_text = f"Works Cited ({self.cm.current_style})\n\n" + "\n\n".join(works_cited)
        
        QApplication.clipboard().setText(clipboard_text)
        QMessageBox.information(self, "Copied!", f"Works Cited page copied to clipboard in {self.cm.current_style} format!")