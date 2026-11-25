"""Results table and resizable import preview for Historical Archives."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .preview_panel import ArchivePreviewPanel
from .result_model import ArchiveResultModel
from .resizable_sections import PersistentSplitter


class ArchiveResultsPage(QWidget):
    """A roomy, independently resizable result-review workflow page."""

    def __init__(
        self,
        config,
        refine_callback,
        cancel_callback,
        selection_callback,
        open_callback,
        import_callback,
        parent=None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.splitter = PersistentSplitter(
            Qt.Orientation.Vertical,
            config,
            "dock_results_section_sizes",
            [620, 330],
        )
        self.splitter.setAccessibleName("Results and preview sections")
        self.splitter.addWidget(self._table_panel(
            refine_callback, cancel_callback, selection_callback
        ))
        self.preview = ArchivePreviewPanel(
            config, open_callback, import_callback
        )
        self.splitter.addWidget(self.preview)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.restore_sizes()
        layout.addWidget(self.splitter, 1)

    def _table_panel(self, refine_callback, cancel_callback, selection_callback):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(2, 2, 2, 2)
        heading = QHBoxLayout()
        self.summary = QLabel("No results yet")
        self.summary.setObjectName("sectionHeading")
        heading.addWidget(self.summary, 1)
        refine_button = QPushButton("Refine Search")
        refine_button.clicked.connect(refine_callback)
        heading.addWidget(refine_button)
        self.cancel_button = QPushButton("Cancel Search")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(cancel_callback)
        heading.addWidget(self.cancel_button)
        layout.addLayout(heading)
        self.model = ArchiveResultModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setMinimumHeight(220)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setSortingEnabled(False)
        self.table.setAlternatingRowColors(True)
        self._configure_columns()
        self.table.selectionModel().currentRowChanged.connect(selection_callback)
        layout.addWidget(self.table, 1)
        return panel

    def _configure_columns(self):
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (0, 2, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        for column in (1, 3, 5):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.verticalHeader().setDefaultSectionSize(30)
