"""Virtualized, typed multiple-choice input shared by registry-driven forms."""
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QListView, QVBoxLayout, QWidget


class CheckableChoiceModel(QAbstractListModel):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.choices, self.checked = [], set()

    def set_choices(self, choices, selected=None):
        selected = self.checked if selected is None else set(selected)
        choices = [dict(choice) for choice in choices]
        known = {choice['id'] for choice in choices}
        choices.extend({'id': value, 'label': value, 'available': False,
                        'description': 'Restore its provider or explicitly remove this selection.'}
                       for value in sorted(selected - known))
        self.beginResetModel()
        self.choices, self.checked = choices, set(selected)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.choices)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.choices):
            return None
        choice = self.choices[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return ('' if choice.get('available', True) else 'Unavailable: ') + choice['label']
        if role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if choice['id'] in self.checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ToolTipRole:
            return choice.get('description') or choice['id']
        if role == Qt.ItemDataRole.UserRole:
            return choice['id']

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role != Qt.ItemDataRole.CheckStateRole or not index.isValid():
            return False
        choice = self.choices[index.row()]
        checked = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        if checked and not choice.get('available', True) and choice['id'] not in self.checked:
            return False  # An unavailable saved choice may only be explicitly removed.
        if checked:
            self.checked.add(choice['id'])
        else:
            self.checked.discard(choice['id'])
        self.dataChanged.emit(index, index, [role])
        self.changed.emit()
        return True


class CheckableChoiceList(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QListView(self)
        self.model = CheckableChoiceModel(self.view)
        self.view.setModel(self.model)
        self.model.changed.connect(self.changed)
        layout.addWidget(self.view)

    def set_choices(self, choices, selected=None):
        self.model.set_choices(choices, selected)

    def selected(self):
        return [choice['id'] for choice in self.model.choices if choice['id'] in self.model.checked]

    def unavailable(self):
        return [choice['id'] for choice in self.model.choices
                if choice['id'] in self.model.checked and not choice.get('available', True)]
