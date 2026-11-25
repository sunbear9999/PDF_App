"""Model/view artifact tree; keeps the existing shared projection builders."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QTreeView, QHeaderView, QAbstractItemView
from gui.components.artifact_page_model import ArtifactPageModel

HEADER_SAMPLE_ROWS=32


class ArtifactTreeView(QTreeView):
    currentItemChanged = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModel(QStandardItemModel(self))
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._selected_id = None
        self.selectionModel().currentChanged.connect(self._changed)
        self.header().setSectionsClickable(True)
        self.header().setResizeContentsPrecision(HEADER_SAMPLE_ROWS)
        self.header().setSortIndicatorShown(False)
        self.header().setToolTip("Click to sort this page. Search and paging cover the full selected scope.")
        self.header().sectionClicked.connect(self._sort_page)

    def _sort_page(self, section):
        order = Qt.SortOrder.AscendingOrder
        if self.isSortingEnabled() and self.header().sortIndicatorSection() == section:
            order = Qt.SortOrder.DescendingOrder if self.header().sortIndicatorOrder() == order else order
        self.setSortingEnabled(True)
        self.header().setSortIndicatorShown(True)
        self.sortByColumn(section, order)

    def _changed(self, current, previous):
        self.currentItemChanged.emit(_Item(self, current) if current.isValid() else None,
                                     _Item(self, previous) if previous.isValid() else None)

    def setHeaderLabels(self, labels):
        self.model().setHorizontalHeaderLabels(labels)
        for column in range(len(labels)):
            mode = QHeaderView.ResizeMode.Stretch if column == 1 else QHeaderView.ResizeMode.ResizeToContents
            self.header().setSectionResizeMode(column, mode)

    def clear(self):
        item = self.currentItem()
        if item and isinstance(item.data(0, Qt.ItemDataRole.UserRole), dict):
            record = item.data(0, Qt.ItemDataRole.UserRole)
            self._selected_id = record.get('assertion_id') or record.get("artifact_id")
        if not isinstance(self.model(), QStandardItemModel):
            # A tab can show flat pages and expandable rows (Explore with Kinds = Entity).
            self._swap_model(QStandardItemModel(self))
        self.model().removeRows(0, self.model().rowCount())

    def _swap_model(self, model):
        old, selection = self.model(), self.selectionModel()
        headers = [old.headerData(column, Qt.Orientation.Horizontal) for column in range(old.columnCount())]
        self.setModel(model)
        self.selectionModel().currentChanged.connect(self._changed)
        selection.deleteLater(); old.deleteLater()
        if isinstance(model, QStandardItemModel):
            model.setHorizontalHeaderLabels([str(h or "") for h in headers])
        return headers

    def addTopLevelItem(self, item):
        self.model().appendRow(self._standard_row(item))
        if self._selected_id:
            self._restore_selection(self.model().index(self.model().rowCount() - 1, 0))

    def set_artifact_rows(self,rows,cells):
        """Flat pages avoid the compatibility QTreeWidgetItem conversion path."""
        if not isinstance(self.model(),ArtifactPageModel):
            headers=[self.model().headerData(c,Qt.Orientation.Horizontal) for c in range(self.model().columnCount())]
            self._swap_model(ArtifactPageModel(headers,self))
        self.model().replace(rows,cells)
        if self._selected_id:
            for record in rows:
                if (record.get('assertion_id') or record.get('artifact_id'))==self._selected_id:
                    # Sorting is local to the model; use its post-sort position.
                    self._restore_selection(self.model().index(self.model().rows.index(record),0))
                    break

    def _restore_selection(self, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(row, dict) and (row.get('assertion_id') or row.get("artifact_id")) == self._selected_id:
            self.setCurrentIndex(index)
            return
        for position in range(self.model().rowCount(index)):
            self._restore_selection(self.model().index(position, 0, index))

    @classmethod
    def _standard_row(cls, item):
        values = [QStandardItem(item.text(column)) for column in range(item.columnCount())]
        for value in values:
            value.setEditable(False)
            value.setToolTip(item.text(1))
        values[0].setData(item.data(0, Qt.ItemDataRole.UserRole), Qt.ItemDataRole.UserRole)
        for index in range(item.childCount()):
            values[0].appendRow(cls._standard_row(item.child(index)))
        return values

    def selectedItems(self):
        return [_Item(self, index) for index in self.selectionModel().selectedRows()]

    def currentItem(self):
        index = self.currentIndex()
        return _Item(self, index) if index.isValid() else None

    def topLevelItemCount(self):
        return self.model().rowCount()

    def topLevelItem(self, index):
        return _Item(self, self.model().index(index, 0))



class _Item:
    def __init__(self, tree, index):
        self.tree, self.index = tree, index

    def treeWidget(self):
        return self.tree

    def data(self, column, role):
        return self.index.siblingAtColumn(column).data(role)

    def text(self, column):
        return self.data(column, Qt.ItemDataRole.DisplayRole) or ""

    def childCount(self):
        return self.tree.model().rowCount(self.index)

    def child(self, index):
        return _Item(self.tree, self.tree.model().index(index, 0, self.index))
