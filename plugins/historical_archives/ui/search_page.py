"""Resizable query, variant, and saved-search page."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .resizable_sections import PersistentSplitter


class ArchiveSearchPage(QWidget):
    """Search workflow page whose three sections retain user-selected sizes."""

    def __init__(
        self,
        config,
        search_builder,
        variant_builder,
        saved_builder,
        parent=None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.splitter = PersistentSplitter(
            Qt.Orientation.Vertical,
            config,
            "dock_search_section_sizes",
            [250, 580, 90],
        )
        self.splitter.setAccessibleName("Search workflow sections")
        for builder in (search_builder, variant_builder, saved_builder):
            self.splitter.addWidget(self._section(builder))
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.restore_sizes()
        layout.addWidget(self.splitter, 1)

    @staticmethod
    def _section(builder):
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(2, 2, 2, 2)
        builder(layout)
        return section
