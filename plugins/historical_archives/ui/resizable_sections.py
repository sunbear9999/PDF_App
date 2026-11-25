"""Reusable, persisted splitters for plugin-owned dock sections."""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QSplitter


class PersistentSplitter(QSplitter):
    """A user-resizable splitter whose section sizes survive dock recreation."""

    def __init__(
        self,
        orientation: Qt.Orientation,
        config,
        config_key: str,
        default_sizes: Iterable[int],
        parent=None,
    ) -> None:
        super().__init__(orientation, parent)
        self._config = config
        self._config_key = config_key
        self._default_sizes = [int(size) for size in default_sizes]
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save_sizes)
        self.splitterMoved.connect(lambda *_args: self._save_timer.start())
        self.setChildrenCollapsible(True)
        self.setHandleWidth(7)
        self.setOpaqueResize(True)

    def restore_sizes(self) -> None:
        """Apply persisted sizes when valid, otherwise use readable defaults."""
        stored = self._config.get(self._config_key, self._default_sizes)
        sizes = self._valid_sizes(stored)
        self.setSizes(sizes or self._default_sizes)

    def _save_sizes(self) -> None:
        setter = getattr(self._config, "set", None)
        if callable(setter):
            setter(self._config_key, self.sizes())

    def _valid_sizes(self, values):
        if not isinstance(values, (list, tuple)) or len(values) != self.count():
            return []
        try:
            sizes = [max(0, int(value)) for value in values]
        except (TypeError, ValueError):
            return []
        return sizes if any(sizes) else []
