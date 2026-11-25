"""Qt model for normalized archive search results."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..models import ArchiveResult


COLUMNS = ("Title", "Date", "Publication", "Archive", "Match", "Imported")


class ArchiveResultModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._results = []
        self._imported = set()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._results)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._results):
            return None
        result = self._results[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                result.title,
                result.date,
                result.publication,
                result.provider_id,
                "; ".join(result.match_reasons),
                "Yes" if result.provider_key in self._imported else "",
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return result.ocr_snippet or result.description or result.title
        if role == Qt.ItemDataRole.UserRole:
            return result
        return None

    def reset_results(self, results):
        self.beginResetModel()
        self._results = list(results)
        self.endResetModel()

    def append_result(self, result: ArchiveResult):
        if any(item.provider_key == result.provider_key for item in self._results):
            return
        row = len(self._results)
        self.beginInsertRows(QModelIndex(), row, row)
        self._results.append(result)
        self.endInsertRows()

    def result_at(self, row: int):
        return self._results[row] if 0 <= row < len(self._results) else None

    def mark_imported(self, provider_key: str):
        self._imported.add(provider_key)
        if self._results:
            self.dataChanged.emit(
                self.index(0, 5),
                self.index(len(self._results) - 1, 5),
                [Qt.ItemDataRole.DisplayRole],
            )
