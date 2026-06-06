import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
                               QTableWidgetItem, QPushButton, QComboBox, QLabel,
                               QHeaderView, QApplication, QMessageBox)
from PySide6.QtCore import Qt
from core.events.event_bus import EventBus
from core.events.domains.tool_events import CitationEvent, CitationEventPayload, CitationIntent, CitationPayload

class CitationDock(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bus = EventBus.get_instance()

        self._build_ui()

        self.bus.citation_table_data_ready.connect(self._populate_table)
        self.bus.citation_status_updated.connect(self._handle_status)

        self.bus.citation_action_requested.emit(CitationIntent.REFRESH_TABLE, CitationPayload())

    def _build_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Citation Style:"))
        self.style_combo = QComboBox()
        self.style_combo.addItems(["APA", "MLA", "Chicago"])
        toolbar.addWidget(self.style_combo)
        toolbar.addStretch()

        self.btn_refresh = QPushButton("🔄 Refresh Data")
        elf.btn_refresh.clicked.connect(lambda: self.bus.citation_action_requested.emit(CitationIntent.REFRESH_TABLE, CitationPayload()))
        toolbar.addWidget(self.btn_refresh)
        layout.addLayout(toolbar)

        # Expanded columns for accuracy
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Use", "File Name", "Title", "Author(s)", "Year", "Journal", "Vol/Issue", "DOI/URL"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._save_edits)
        layout.addWidget(self.table)

        self.btn_generate = QPushButton("📝 Generate Works Cited")
        self.btn_generate.clicked.connect(self._request_generation)
        layout.addWidget(self.btn_generate)

    def _populate_table(self, event: CitationEvent, payload: CitationEventPayload):
        if event != CitationEvent.TABLE_DATA_READY:
            return
        data_list = payload.data
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        for doc_path, data in data_list:
            row = self.table.rowCount()
            self.table.insertRow(row)

            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk_item.setCheckState(Qt.CheckState.Checked)
            chk_item.setData(Qt.ItemDataRole.UserRole, doc_path)
            self.table.setItem(row, 0, chk_item)

            file_item = QTableWidgetItem(os.path.basename(doc_path))
            file_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 1, file_item)

            self.table.setItem(row, 2, QTableWidgetItem(data.get("title", "")))
            self.table.setItem(row, 3, QTableWidgetItem(data.get("authors", "")))
            self.table.setItem(row, 4, QTableWidgetItem(data.get("year", "")))
            self.table.setItem(row, 5, QTableWidgetItem(data.get("journal", "")))
            self.table.setItem(row, 6, QTableWidgetItem(data.get("vol_issue", "")))
            self.table.setItem(row, 7, QTableWidgetItem(data.get("doi", "")))

        self.table.blockSignals(False)

    def _save_edits(self, item):
        row = item.row()
        doc_path = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)

        payload = {
            "doc_id": doc_path,
            "title": self.table.item(row, 2).text(),
            "authors": self.table.item(row, 3).text(),
            "year": self.table.item(row, 4).text(),
            "journal": self.table.item(row, 5).text(),
            "vol_issue": self.table.item(row, 6).text(),
            "doi": self.table.item(row, 7).text()
        }
        self.bus.citation_action_requested.emit(CitationIntent.UPDATE_ENTRY, CitationPayload(data=payload))

    def _request_generation(self):
        selected_docs = []
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                selected_docs.append(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))

        if not selected_docs:
            QMessageBox.warning(self, "No Docs Selected", "Please select at least one document.")
            return

        self.bus.citation_action_requested.emit(
            CitationIntent.GENERATE_WORKS_CITED,
            CitationPayload(doc_ids=selected_docs, style=self.style_combo.currentText())
        )
    def _handle_status(self, event: CitationEvent, payload: CitationEventPayload):
        if event == CitationEvent.WORKS_CITED_GENERATED:
            works = payload.get("works", [])
            style = self.style_combo.currentText()
            clipboard_text = f"Works Cited ({style})\n\n" + "\n\n".join(works)
            QApplication.clipboard().setText(clipboard_text)
            QMessageBox.information(self, "Copied!", f"Works Cited page copied to clipboard in {style} format.")
    def update_theme(self, theme):
        self.setStyleSheet(f"CitationDock {{ background-color: {theme['bg_main']}; color: {theme['text_main']}; }}")

        self.style_combo.setStyleSheet(f"background-color: {theme['bg_input']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 4px; border-radius: 4px;")
        self.btn_refresh.setStyleSheet(f"background-color: {theme['bg_panel']}; color: {theme['text_main']}; border: 1px solid {theme['border']}; padding: 6px; border-radius: 4px;")
        self.btn_generate.setStyleSheet(f"background-color: {theme['accent']}; color: #ffffff; font-weight: bold; padding: 8px; border: none; border-radius: 4px;")

        # Target internal QTableWidget components to overwrite hard-coded defaults
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {theme['bg_main']};
                color: {theme['text_main']};
                gridline-color: {theme['border']};
                border: 1px solid {theme['border']};
            }}
            QTableWidget::item {{
                background-color: {theme['bg_input']};
                color: {theme['text_main']};
            }}
            QTableWidget::item:selected {{
                background-color: {theme['accent']};
                color: #ffffff;
            }}
            QHeaderView::section {{
                background-color: {theme['bg_panel']};
                color: {theme['text_main']};
                border: 1px solid {theme['border']};
                border-top: none;
                border-left: none;
                padding: 4px;
                font-weight: bold;
            }}
            QTableCornerButton::section {{
                background-color: {theme['bg_panel']};
                border: 1px solid {theme['border']};
            }}
        """)
