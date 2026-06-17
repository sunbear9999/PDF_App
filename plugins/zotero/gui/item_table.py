"""
plugins/zotero/gui/item_table.py

QTableWidget showing a filtered list of Zotero items.
Emits item_selected(item_dict) when the user clicks a row.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


_COLUMNS = ["Title", "Author(s)", "Year", "Type", "📎"]
_COL_TITLE = 0
_COL_AUTHORS = 1
_COL_YEAR = 2
_COL_TYPE = 3
_COL_ATTACH = 4


class ItemTable(QWidget):
    """
    Item list widget with an inline search bar.

    Signals:
      item_selected(item_dict | None)
    """

    item_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_items: List[Dict] = []
        self._attachment_ids: set = set()  # item_ids that have PDF attachments
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Search bar
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("🔍"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search title, author, year…")
        self.search_edit.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search_edit)
        self.clear_btn = QPushButton("✕")
        self.clear_btn.setFixedWidth(28)
        self.clear_btn.clicked.connect(self.search_edit.clear)
        search_row.addWidget(self.clear_btn)
        layout.addLayout(search_row)

        # Table
        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 32)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        # Item count label
        self.count_label = QLabel("0 items")
        self.count_label.setObjectName("countLabel")
        layout.addWidget(self.count_label)

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def set_items(self, items: List[Dict], attachment_ids: Optional[set] = None) -> None:
        """Replace the full item list and re-apply the current filter."""
        self._all_items = items
        self._attachment_ids = attachment_ids or set()
        self._apply_filter(self.search_edit.text())

    def _apply_filter(self, text: str) -> None:
        q = text.strip().lower()
        filtered = self._all_items
        if q:
            filtered = [
                i for i in filtered
                if q in i.get("title", "").lower()
                or q in i.get("authors_display", "").lower()
                or q in str(i.get("year", ""))
            ]
        self._populate_table(filtered)

    def _populate_table(self, items: List[Dict]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for item in items:
            row = self.table.rowCount()
            self.table.insertRow(row)

            title_item = QTableWidgetItem(item.get("title", ""))
            title_item.setData(Qt.ItemDataRole.UserRole, item)
            self.table.setItem(row, _COL_TITLE, title_item)

            self.table.setItem(row, _COL_AUTHORS, QTableWidgetItem(item.get("authors_display", "")))
            self.table.setItem(row, _COL_YEAR, QTableWidgetItem(item.get("year", "")))

            type_label = _format_type(item.get("item_type", ""))
            self.table.setItem(row, _COL_TYPE, QTableWidgetItem(type_label))

            has_pdf = item.get("item_id") in self._attachment_ids
            attach_item = QTableWidgetItem("📄" if has_pdf else "")
            attach_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, _COL_ATTACH, attach_item)

        self.table.blockSignals(False)
        self.count_label.setText(f"{len(items)} item{'s' if len(items) != 1 else ''}")

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        rows = self.table.selectedItems()
        if not rows:
            self.item_selected.emit(None)
            return
        item_data = self.table.item(rows[0].row(), _COL_TITLE).data(Qt.ItemDataRole.UserRole)
        self.item_selected.emit(item_data)

    def get_selected_items(self) -> List[Dict]:
        """Return all selected item dicts (may be empty)."""
        result = []
        for row in set(i.row() for i in self.table.selectedItems()):
            d = self.table.item(row, _COL_TITLE).data(Qt.ItemDataRole.UserRole)
            if d:
                result.append(d)
        return result

    def apply_theme(self, theme: dict) -> None:
        bg = theme.get("bg_input", "#00212b")
        text = theme.get("text_main", "#93a1a1")
        accent = theme.get("accent", "#268bd2")
        border = theme.get("border", "#586e75")
        panel = theme.get("bg_panel", "#073642")
        muted = theme.get("text_muted", "#586e75")

        self.table.setStyleSheet(f"""
            QTableWidget {{
                background: {bg}; color: {text};
                gridline-color: {border}; border: 1px solid {border};
            }}
            QTableWidget::item:selected {{ background: {accent}; color: #fff; }}
            QHeaderView::section {{
                background: {panel}; color: {text};
                border: 1px solid {border}; padding: 4px; font-weight: bold;
            }}
        """)
        self.search_edit.setStyleSheet(
            f"background: {bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 4px;"
        )
        self.clear_btn.setStyleSheet(
            f"background: {panel}; color: {text}; border: 1px solid {border}; border-radius: 4px;"
        )
        self.count_label.setStyleSheet(f"color: {muted}; background: transparent; font-size: 11px;")


def _format_type(ztype: str) -> str:
    labels = {
        "journalArticle": "Article",
        "book": "Book",
        "bookSection": "Book Ch.",
        "conferencePaper": "Conf. Paper",
        "thesis": "Thesis",
        "report": "Report",
        "webpage": "Webpage",
        "magazineArticle": "Magazine",
        "newspaperArticle": "News",
        "patent": "Patent",
        "dataset": "Dataset",
        "preprint": "Preprint",
    }
    return labels.get(ztype, ztype.replace("_", " ").title())
