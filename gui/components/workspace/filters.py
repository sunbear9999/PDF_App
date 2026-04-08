from PyQt6.QtWidgets import QComboBox
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItemModel, QStandardItem


class CheckableComboBox(QComboBox):
    """
    QComboBox with checkable items.

    Used by the workspace filter bar to include/exclude PDFs.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view().pressed.connect(self.handle_item_pressed)
        self._changed = False

    def handle_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        if not item:
            return

        clicked_data = item.data(Qt.ItemDataRole.UserRole)
        current_state = item.checkState()
        new_state = (
            Qt.CheckState.Unchecked
            if current_state == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

        # 'All PDFs' is always at index 0
        all_item = self.model().item(0)

        # Block signals so we can update multiple checkboxes without firing filter repeatedly
        self.model().blockSignals(True)

        if clicked_data == "ALL":
            # If clicking ALL, force it checked and uncheck everything else
            item.setCheckState(Qt.CheckState.Checked)
            for i in range(1, self.model().rowCount()):
                self.model().item(i).setCheckState(Qt.CheckState.Unchecked)
        else:
            item.setCheckState(new_state)

            if new_state == Qt.CheckState.Checked:
                # If transitioning from "ALL" to a specific PDF, clear everything else for a fresh start
                if all_item and all_item.checkState() == Qt.CheckState.Checked:
                    all_item.setCheckState(Qt.CheckState.Unchecked)
                    for i in range(1, self.model().rowCount()):
                        other_item = self.model().item(i)
                        if other_item != item:
                            other_item.setCheckState(Qt.CheckState.Unchecked)
            else:
                # If we unchecked a specific PDF, verify if we need to auto-fallback to "ALL"
                any_checked = False
                for i in range(1, self.model().rowCount()):
                    if self.model().item(i).checkState() == Qt.CheckState.Checked:
                        any_checked = True
                        break
                if not any_checked and all_item:
                    all_item.setCheckState(Qt.CheckState.Checked)

        self._changed = True
        self.model().blockSignals(False)

        # Emit dataChanged once for the whole list to trigger the workspace filter/UI update
        top_left = self.model().index(0, 0)
        bottom_right = self.model().index(self.model().rowCount() - 1, 0)
        self.model().dataChanged.emit(top_left, bottom_right)

    def hidePopup(self):
        if not self._changed:
            super().hidePopup()
        self._changed = False

    def addItem(self, text, userData=None, checked=False):
        # Force a QStandardItemModel if it isn't one already
        if not isinstance(self.model(), QStandardItemModel):
            self.setModel(QStandardItemModel(self))

        item = QStandardItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setData(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked,
            Qt.ItemDataRole.CheckStateRole,
        )
        item.setData(userData, Qt.ItemDataRole.UserRole)
        self.model().appendRow(item)

    def get_checked_items(self):
        checked = []
        model = self.model()
        if isinstance(model, QStandardItemModel):
            for i in range(model.rowCount()):
                item = model.item(i)
                if item and item.checkState() == Qt.CheckState.Checked:
                    checked.append(item.data(Qt.ItemDataRole.UserRole))
        return checked

    def clear(self):
        model = self.model()
        if isinstance(model, QStandardItemModel):
            model.clear()
            # model.clear() wipes columns; restore column count so text renders
            model.setColumnCount(1)
        else:
            super().clear()

