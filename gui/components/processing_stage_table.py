"""Typed stage choices and readable plan details; no GUI readiness heuristics."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QAbstractItemView


class ProcessingStageTable(QTableWidget):
    choice_changed = Signal(str, bool)
    stage_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = {}
        self.setColumnCount(4)
        self.setHorizontalHeaderLabels(["In saved plan", "Stage", "Last outcome", "Readiness"])
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.itemChanged.connect(self._changed)
        self.currentCellChanged.connect(self._selected)
        self.setAccessibleName("Source processing stages and readiness")

    def set_plan(self, rows):
        selected = self.current_stage()
        self.blockSignals(True)
        self.rows = {row["stage"]: row for row in rows}
        self.setRowCount(len(rows))
        for position, row in enumerate(rows):
            choice = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            if row.get("readiness") != "unavailable":
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            choice.setFlags(flags)
            choice.setCheckState(Qt.CheckState.Checked if row.get("selected") else Qt.CheckState.Unchecked)
            choice.setData(Qt.ItemDataRole.UserRole, row["stage"])
            choice.setToolTip(row.get("reason") or "Choose this stage for the source's saved plan.")
            self.setItem(position, 0, choice)
            for column, text in enumerate((row["label"], row["status"].replace("_", " "), row.get("readiness", "").replace("_", " ")), 1):
                item = QTableWidgetItem(text)
                item.setToolTip(row.get("readiness_detail") or row.get("description") or "")
                self.setItem(position, column, item)
            if row["stage"] == selected:
                self.setCurrentCell(position, 1)
        self.resizeColumnsToContents()
        self.blockSignals(False)
        if self.currentRow() < 0 and rows:
            self.setCurrentCell(0, 1)
        elif self.current_stage():
            self.stage_selected.emit(self.current_stage())

    def current_stage(self):
        item = self.item(self.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _changed(self, item):
        if item.column() == 0:
            self.choice_changed.emit(item.data(Qt.ItemDataRole.UserRole), item.checkState() == Qt.CheckState.Checked)

    def _selected(self, *_):
        self.stage_selected.emit(self.current_stage())
