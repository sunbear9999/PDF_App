"""A bounded item model and one selected detail, shared by Notes and history."""
from PySide6.QtCore import Qt, QAbstractListModel, QModelIndex, Signal, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QListView, QSplitter, QLabel
from gui.components.cursor_pager import CursorPager

SELECTION_DELAY_MS = 50


class CollectionModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        row = self.items[index.row()]
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole}:
            return row.get('title', '') + '\n' + row.get('preview', '')
        return None

    def replace(self, items):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()


class PagedCollection(QWidget):
    selected = Signal(int)

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.view = QListView(self)
        self.view.setWordWrap(True)
        self.view.setUniformItemSizes(True)
        self.view.setAccessibleName('Paged records; use arrow keys to choose a record')
        self.model = CollectionModel(self)
        self.selection_timer = QTimer(self)
        self.selection_timer.setSingleShot(True)
        self.selection_timer.setInterval(SELECTION_DELAY_MS)
        self.selection_timer.timeout.connect(self._publish_selection)
        self.pending_selection = -1
        self.view.setModel(self.model)
        self.view.selectionModel().currentChanged.connect(self._selected)
        self.splitter.addWidget(self.view)
        self.detail = QLabel('Select a record to inspect it.', self)
        self.persistent_detail = False
        self.splitter.addWidget(self.detail)
        layout.addWidget(self.splitter, 1)
        self.pager = CursorPager(app_context, self)
        layout.addWidget(self.pager)
        self.status = QLabel('', self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setAccessibleName('Collection loading and source diagnostics')
        layout.addWidget(self.status)
        self.setWhatsThis('Browse a bounded page with arrow keys, then read the selected detail. Previous/Next visits all available pages. F1 opens the browsing guide.')
        registry = getattr(getattr(app_context, 'help_service', None), 'ui_target_registry', None)
        if registry is not None:
            identity = f'intelligence.collection.{id(self)}'
            registry.register_widget(identity, self, 'core.intelligence.browsing')
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def set_detail(self, widget, *, persistent=False):
        old = self.detail
        self.detail = widget
        self.persistent_detail = persistent
        self.splitter.replaceWidget(1, widget)
        old.hide()
        old.deleteLater()

    def apply(self, snapshot):
        self.selection_timer.stop()
        self.pending_selection = -1
        self.model.replace(snapshot.get('items') or [])
        self.pager.apply(snapshot)
        self.status.setText('\n'.join(snapshot.get('diagnostics') or []) or 'Select a record. More pages may contain additional matches.')
        if self.model.rowCount():
            self.view.setCurrentIndex(self.model.index(0))
        else:
            if self.persistent_detail:
                self.selected.emit(-1)
            else:
                self.set_detail(QLabel('No matching records on this page. Use Next page if available.', self))

    def _selected(self, current, previous):
        if current.isValid():
            self.pending_selection = current.row()
            self.detail.setEnabled(False)
            self.selection_timer.start()

    def _publish_selection(self):
        if 0 <= self.pending_selection < self.model.rowCount():
            self.selected.emit(self.pending_selection)
            self.detail.setEnabled(True)
