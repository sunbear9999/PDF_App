"""Draft prerequisite groups; validation remains owned by the core contract."""
from copy import deepcopy
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal


class AnalysisRequirementModel(QAbstractListModel):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.groups)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.groups):
            return None
        group = self.groups[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            if not isinstance(group, dict):
                return 'Invalid saved group — remove to replace'
            values = group.get('artifact_types')
            types = (', '.join(str(kind).replace('_', ' ') for kind in values)
                     if isinstance(values, list) else 'invalid saved artifact types')
            scope = 'each source' if group.get('scope') == 'each_source' else 'selected sources'
            return f"{str(group.get('match', 'Choose policy')).title()}: {types or 'choose artifact types'} · {scope}"

    def load(self, groups):
        self.beginResetModel()
        self.groups = deepcopy(groups if isinstance(groups, list) else [groups])
        self.endResetModel()

    def append(self):
        row = len(self.groups)
        self.beginInsertRows(QModelIndex(), row, row)
        self.groups.append({'artifact_types': [], 'match': 'any', 'scope': 'selection'})
        self.endInsertRows()
        self.changed.emit()
        return self.index(row)

    def remove(self, row):
        if not 0 <= row < len(self.groups):
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        del self.groups[row]
        self.endRemoveRows()
        self.changed.emit()

    def update(self, row, values):
        if not 0 <= row < len(self.groups) or not isinstance(self.groups[row], dict):
            return
        self.groups[row] = {**self.groups[row], **values}
        self.dataChanged.emit(self.index(row), self.index(row))
        self.changed.emit()
