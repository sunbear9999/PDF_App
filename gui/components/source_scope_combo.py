"""Project/source selector refreshed from background snapshots and bus events."""
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox


class SourceScopeCombo(QComboBox):
    sources_invalidated = Signal()

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self._connections = []
        self.setModel(QStandardItemModel(self))
        self.set_sources([])
        bus = getattr(app_context, "bus", None)
        for name in ("source_added", "source_renamed", "source_removed", "project_loaded"):
            signal = getattr(bus, name, None)
            if signal is not None:
                signal.connect(self._invalidate, Qt.ConnectionType.QueuedConnection)
                self._connections.append((signal, self._invalidate))
        clearing = getattr(bus, "project_clearing_started", None)
        if clearing is not None:
            clearing.connect(self._clear_project, Qt.ConnectionType.QueuedConnection)
            self._connections.append((clearing, self._clear_project))

    def _invalidate(self, *_):
        self.sources_invalidated.emit()

    def _clear_project(self, *_):
        self.set_sources([])
        self.sources_invalidated.emit()

    def set_sources(self, sources):
        """Update labels and IDs together, preserving a still-existing scope."""
        selected = self.currentData()
        with QSignalBlocker(self):
            model = self.model()
            model.clear()
            whole = QStandardItem("Whole project")
            whole.setData("", Qt.ItemDataRole.UserRole)
            items = [whole]
            for source in sources:
                item = QStandardItem(source.get("title") or source.get("path") or "Untitled source")
                item.setData(source.get("id"), Qt.ItemDataRole.UserRole)
                items.append(item)
            model.appendColumn(items)
            self.setCurrentIndex(max(0, self.findData(selected)))

    def shutdown(self):
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connections.clear()
