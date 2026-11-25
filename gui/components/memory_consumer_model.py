"""Table model for the memory panel's "Loaded components" list."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from core.models.memory_models import ConsumerUsage

COLUMNS = ("Component", "Kind", "Memory", "State")
_STATE_TEXT = {"in_use": "in use", "idle": "idle {minutes} min", "loaded": "loaded", "empty": "not loaded"}


def format_bytes(value: int) -> str:
    if value >= 1024 ** 3:
        return f"{value / 1024 ** 3:.2f} GB"
    if value >= 1024 ** 2:
        return f"{value / 1024 ** 2:.0f} MB"
    return f"{value / 1024:.0f} KB" if value else "—"


class MemoryConsumerModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[ConsumerUsage] = []

    def set_rows(self, rows) -> None:
        ordered = sorted(rows, key=lambda item: -item.bytes)
        if [row.id for row in ordered] == [row.id for row in self._rows]:
            self._rows = ordered
            if ordered:
                self.dataChanged.emit(self.index(0, 0), self.index(len(ordered) - 1, len(COLUMNS) - 1))
            return
        self.beginResetModel()
        self._rows = ordered
        self.endResetModel()

    def row_at(self, row: int) -> Optional[ConsumerUsage]:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = self.row_at(index.row())
        if row is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return (row.label, row.kind, format_bytes(row.bytes), _state(row))[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == 2:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return (f"{row.id}\nAutomatic release: {'allowed' if row.can_release else 'never'}"
                    + (f"\nIdle {int(row.idle_seconds)} s" if row.idle_seconds else ""))
        return None


def _state(row: ConsumerUsage) -> str:
    return _STATE_TEXT.get(row.state, row.state).format(minutes=int(row.idle_seconds // 60))
