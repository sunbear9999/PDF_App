"""
gui/components/plugin_widgets.py

Reusable themed widgets for plugin UIs.
All widgets accept an optional ``theme`` dict and expose ``update_theme(theme)``.
No dependency on AppContext or MainWindow — pure PySide6.

Note: PluginCard and PluginForm are thin compatibility wrappers around the
canonical base components (BaseCard, SchemaFormBuilder). Prefer importing
from gui.components.base directly in new code.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from gui.components.base import BaseCard, SchemaFormBuilder


class PluginCard(BaseCard):
    """
    Backward-compatible titled card for plugin UIs.
    Delegates to BaseCard — use BaseCard directly in new code.
    content_layout is provided by BaseCard as an alias for body_layout.
    """

    def __init__(self, title: str = "", theme: Optional[dict] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(theme=theme, parent=parent)
        if title:
            self.add_title(title)
        if theme:
            self.update_theme(theme)


class PluginSearchBar(QWidget):
    """Debounced search input that emits search_changed(str) after 300 ms."""

    search_changed = Signal(str)

    def __init__(self, placeholder: str = "Search…", theme: Optional[dict] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._input = QLineEdit()
        self._input.setPlaceholderText(placeholder)
        layout.addWidget(self._input)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(lambda: self.search_changed.emit(self._input.text()))
        self._input.textChanged.connect(lambda _: self._timer.start())

        if theme:
            self.update_theme(theme)

    def text(self) -> str:
        return self._input.text()

    def update_theme(self, theme: dict) -> None:
        bg = theme.get("bg_input", theme.get("bg_panel", "#1e1e2e"))
        fg = theme.get("text_main", "#e0e0e0")
        border = theme.get("border", "#444")
        self._input.setStyleSheet(
            f"QLineEdit {{ background: {bg}; color: {fg}; border: 1px solid {border};"
            f"  border-radius: 4px; padding: 4px 8px; }}"
        )


class PluginResultList(QWidget):
    """Scrollable list where each item is rendered by render_fn. Emits item_clicked(dict)."""

    item_clicked = Signal(object)

    def __init__(
        self,
        render_fn: Optional[Callable[[dict], QWidget]] = None,
        theme: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._render_fn = render_fn
        self._items: List[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        layout.addWidget(self._list)
        self._list.itemClicked.connect(lambda item: self.item_clicked.emit(item.data(1000)))

        if theme:
            self.update_theme(theme)

    def set_items(self, items: List[dict]) -> None:
        self._items = items
        self._list.clear()
        for data in items:
            text = data.get("label") or data.get("title") or str(data)
            item = QListWidgetItem(text)
            item.setData(1000, data)
            self._list.addItem(item)

    def update_theme(self, theme: dict) -> None:
        bg = theme.get("bg_panel", "#1e1e2e")
        fg = theme.get("text_main", "#e0e0e0")
        accent = theme.get("accent", "#4a4a8a")
        self._list.setStyleSheet(
            f"QListWidget {{ background: {bg}; color: {fg}; border: none; }}"
            f"QListWidget::item:selected {{ background: {accent}; }}"
        )


class PluginButton(QPushButton):
    """Themed button with semantic variants: 'primary', 'secondary', 'danger'."""

    def __init__(
        self,
        label: str,
        variant: str = "primary",
        theme: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(label, parent)
        self._variant = variant
        if theme:
            self.update_theme(theme)

    def update_theme(self, theme: dict) -> None:
        if self._variant == "primary":
            bg = theme.get("accent", "#4a4a8a")
            fg = "#ffffff"
        elif self._variant == "danger":
            bg = theme.get("error", "#cc3333")
            fg = "#ffffff"
        else:
            bg = theme.get("bg_panel", "#2a2a3e")
            fg = theme.get("text_main", "#e0e0e0")
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {fg}; border-radius: 4px;"
            f"  padding: 5px 14px; }}"
        )


class PluginStatusBar(QLabel):
    """Inline status label with semantic states."""

    STATES = {
        "ready":   ("#888888", "Ready"),
        "running": ("#2d7dd2", "Running…"),
        "success": ("#2d8c5a", "Done"),
        "warning": ("#d2832d", "Warning"),
        "error":   ("#cc3333", "Error"),
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.set_state("ready")

    def set_state(self, state: str, message: str = "") -> None:
        color, default_msg = self.STATES.get(state, ("#888888", state))
        self.setText(message or default_msg)
        self.setStyleSheet(f"QLabel {{ color: {color}; font-size: 12px; }}")


class PluginForm(SchemaFormBuilder):
    """
    Backward-compatible auto-generated form for plugin UIs.
    Delegates to SchemaFormBuilder — use SchemaFormBuilder directly in new code.

    Schema entry format (both APIs accepted)::

        {"key": "x", "label": "X", "type": "text"|"int"|"float"|"bool"|"choice"|
         "number"|"boolean"|"select"|"long_text", "default": "", "options"/"choices": [...]}
    """

    def __init__(
        self,
        schema: List[dict],
        theme: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(schema=schema, theme=theme, parent=parent)


class PluginTable(QTableWidget):
    """Themed sortable table. Emits row_clicked(dict) on row selection."""

    row_clicked = Signal(dict)

    def __init__(
        self,
        columns: List[str],
        theme: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(0, len(columns), parent)
        self.setHorizontalHeaderLabels(columns)
        self._columns = columns
        self.setSortingEnabled(True)
        self.setSelectionBehavior(self.SelectionBehavior.SelectRows)
        self.setEditTriggers(self.EditTrigger.NoEditTriggers)
        self.horizontalHeader().setStretchLastSection(True)
        self.cellClicked.connect(self._on_cell_clicked)
        if theme:
            self.update_theme(theme)

    def set_rows(self, rows: List[dict]) -> None:
        self.setRowCount(0)
        for row_data in rows:
            r = self.rowCount()
            self.insertRow(r)
            for c, col in enumerate(self._columns):
                val = row_data.get(col, "")
                item = QTableWidgetItem(str(val))
                item.setData(1000, row_data)
                self.setItem(r, c, item)

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        item = self.item(row, 0)
        if item:
            self.row_clicked.emit(item.data(1000) or {})

    def update_theme(self, theme: dict) -> None:
        bg = theme.get("bg_panel", "#1e1e2e")
        fg = theme.get("text_main", "#e0e0e0")
        header_bg = theme.get("bg_input", "#2a2a3e")
        accent = theme.get("accent", "#4a4a8a")
        self.setStyleSheet(
            f"QTableWidget {{ background: {bg}; color: {fg}; gridline-color: {theme.get('border', '#444')}; }}"
            f"QHeaderView::section {{ background: {header_bg}; color: {fg}; padding: 4px; }}"
            f"QTableWidget::item:selected {{ background: {accent}; }}"
        )
