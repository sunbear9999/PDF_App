"""
plugins/locallaws/gui/tag_panel.py

Reusable tag widgets for the Local Laws plugin:
  - TagChip          — colored pill with optional ×-remove button
  - TagFilterWidget  — AND/OR toggle + chip filter bar (emits filter_changed)
  - TagManagerDialog — full CRUD for tags (name + color)
"""
from __future__ import annotations

from typing import List, Dict, Optional, Callable

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QPainter, QBrush, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QDialog, QDialogButtonBox, QLineEdit, QListWidget, QListWidgetItem,
    QFrame, QMenu, QSizePolicy, QScrollArea, QColorDialog, QInputDialog,
    QMessageBox, QRadioButton, QButtonGroup, QToolButton, QAbstractItemView,
)


# ---------------------------------------------------------------------------
# TagChip
# ---------------------------------------------------------------------------

class TagChip(QFrame):
    """A colored pill that optionally has a ×-remove button."""

    remove_clicked = Signal(int)   # tag_id

    def __init__(
        self,
        tag_id: int,
        name: str,
        color: str = "#607d8b",
        removable: bool = True,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.tag_id = tag_id
        self.name = name
        self.color = color

        self.setObjectName("TagChip")
        self._apply_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(2)

        lbl = QLabel(name)
        lbl.setFont(QFont("", 8, QFont.Weight.Medium))
        lbl.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(lbl)

        if removable:
            btn = QToolButton()
            btn.setText("×")
            btn.setFixedSize(14, 14)
            btn.setStyleSheet("background: transparent; border: none; font-size: 11px;")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda: self.remove_clicked.emit(self.tag_id))
            layout.addWidget(btn)

        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _apply_style(self):
        c = QColor(self.color)
        # Choose dark or light text based on luminance
        lum = 0.299 * c.redF() + 0.587 * c.greenF() + 0.114 * c.blueF()
        fg = "#111111" if lum > 0.55 else "#f0f0f0"
        self.setStyleSheet(
            f"TagChip {{ background-color: {self.color}; border-radius: 9px; }}"
            f"TagChip QLabel {{ color: {fg}; }}"
            f"TagChip QToolButton {{ color: {fg}; }}"
        )


# ---------------------------------------------------------------------------
# TagFilterWidget
# ---------------------------------------------------------------------------

class TagFilterWidget(QWidget):
    """
    Horizontal filter bar: AND/OR toggle + a row of active tag chips.
    Emits filter_changed(tag_ids: list[int], logic: str) whenever the
    selection or logic changes.
    """

    filter_changed = Signal(list, str)   # (tag_ids, logic)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._active: Dict[int, Dict] = {}   # tag_id → {name, color}
        self._all_tags: List[Dict] = []

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(4, 2, 4, 2)
        root.setSpacing(4)

        # Logic toggle
        self._logic_and = QRadioButton("AND")
        self._logic_or = QRadioButton("OR")
        self._logic_and.setChecked(True)
        self._logic_and.setStyleSheet("font-size: 9px;")
        self._logic_or.setStyleSheet("font-size: 9px;")
        self._logic_group = QButtonGroup(self)
        self._logic_group.addButton(self._logic_and)
        self._logic_group.addButton(self._logic_or)
        self._logic_and.toggled.connect(lambda: self._emit())
        root.addWidget(self._logic_and)
        root.addWidget(self._logic_or)

        # Chip scroll area
        scroll = QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedHeight(30)

        self._chip_container = QWidget()
        self._chip_layout = QHBoxLayout(self._chip_container)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(3)
        self._chip_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._chip_container.setLayout(self._chip_layout)
        scroll.setWidget(self._chip_container)
        root.addWidget(scroll, 1)

        # "+" add button
        self._add_btn = QToolButton()
        self._add_btn.setText("+")
        self._add_btn.setToolTip("Add tag filter")
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.clicked.connect(self._show_tag_menu)
        root.addWidget(self._add_btn)

        # Clear all button
        self._clear_btn = QToolButton()
        self._clear_btn.setText("✕")
        self._clear_btn.setToolTip("Clear all filters")
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(self.clear_all)
        root.addWidget(self._clear_btn)

    def set_available_tags(self, tags: List[Dict]) -> None:
        self._all_tags = tags

    @property
    def logic(self) -> str:
        return "AND" if self._logic_and.isChecked() else "OR"

    @property
    def active_tag_ids(self) -> List[int]:
        return list(self._active.keys())

    @property
    def active_tag_names(self) -> List[str]:
        return [t["name"] for t in self._active.values()]

    def add_tag(self, tag_id: int, name: str, color: str = "#607d8b") -> None:
        if tag_id in self._active:
            return
        self._active[tag_id] = {"name": name, "color": color}
        chip = TagChip(tag_id, name, color, removable=True, parent=self._chip_container)
        chip.remove_clicked.connect(self.remove_tag)
        self._chip_layout.addWidget(chip)
        self._emit()

    def remove_tag(self, tag_id: int) -> None:
        if tag_id not in self._active:
            return
        del self._active[tag_id]
        # Find and remove chip from layout
        for i in range(self._chip_layout.count()):
            item = self._chip_layout.itemAt(i)
            if item and isinstance(item.widget(), TagChip) and item.widget().tag_id == tag_id:
                w = item.widget()
                self._chip_layout.removeWidget(w)
                w.deleteLater()
                break
        self._emit()

    def clear_all(self) -> None:
        self._active.clear()
        while self._chip_layout.count():
            item = self._chip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._emit()

    def _show_tag_menu(self) -> None:
        if not self._all_tags:
            return
        menu = QMenu(self)
        for tag in self._all_tags:
            if tag["id"] not in self._active:
                action = menu.addAction(tag["name"])
                action.setData(tag)
        if menu.isEmpty():
            return
        chosen = menu.exec(self._add_btn.mapToGlobal(self._add_btn.rect().bottomLeft()))
        if chosen:
            tag = chosen.data()
            self.add_tag(tag["id"], tag["name"], tag.get("color", "#607d8b"))

    def _emit(self) -> None:
        self.filter_changed.emit(self.active_tag_ids, self.logic)


# ---------------------------------------------------------------------------
# TagManagerDialog
# ---------------------------------------------------------------------------

class TagManagerDialog(QDialog):
    """
    Dialog for creating, renaming, recoloring, and deleting plugin-local law tags.
    Accepts a LocalLawsTagDB and an optional callback for change events.
    """

    def __init__(
        self,
        tag_db,
        on_changed: Optional[Callable] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.tag_db = tag_db
        self._on_changed = on_changed

        self.setWindowTitle("Manage Law Tags")
        self.setMinimumSize(340, 420)

        layout = QVBoxLayout(self)

        # Tag list
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self._list)

        # Action buttons
        btns = QHBoxLayout()
        self._new_btn = QPushButton("New Tag")
        self._rename_btn = QPushButton("Rename")
        self._color_btn = QPushButton("Color…")
        self._del_btn = QPushButton("Delete")
        for b in (self._new_btn, self._rename_btn, self._color_btn, self._del_btn):
            btns.addWidget(b)
        layout.addLayout(btns)

        layout.addWidget(QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self))
        layout.itemAt(layout.count() - 1).widget().rejected.connect(self.reject)
        layout.itemAt(layout.count() - 1).widget().accepted.connect(self.accept)

        self._new_btn.clicked.connect(self._create_tag)
        self._rename_btn.clicked.connect(self._rename_tag)
        self._color_btn.clicked.connect(self._change_color)
        self._del_btn.clicked.connect(self._delete_tag)
        self._list.currentRowChanged.connect(self._on_selection_changed)

        self._reload()

    def _reload(self) -> None:
        self._list.clear()
        for tag in self.tag_db.get_all_tags():
            item = QListWidgetItem(tag["name"])
            item.setData(Qt.ItemDataRole.UserRole, tag)
            c = QColor(tag.get("color", "#607d8b"))
            item.setBackground(QBrush(c))
            lum = 0.299 * c.redF() + 0.587 * c.greenF() + 0.114 * c.blueF()
            item.setForeground(QBrush(QColor("#111" if lum > 0.55 else "#eee")))
            self._list.addItem(item)
        self._on_selection_changed()

    def _current_tag(self) -> Optional[Dict]:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_selection_changed(self) -> None:
        has = self._current_tag() is not None
        for b in (self._rename_btn, self._color_btn, self._del_btn):
            b.setEnabled(has)

    def _create_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "New Tag", "Tag name:")
        name = name.strip()
        if not ok or not name:
            return
        color = QColorDialog.getColor(QColor("#607d8b"), self, "Choose tag color")
        color_hex = color.name() if color.isValid() else "#607d8b"
        self.tag_db.create_tag(name, color_hex)
        self._reload()
        self._notify_changed()

    def _rename_tag(self) -> None:
        tag = self._current_tag()
        if not tag:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Tag", "New name:", text=tag["name"]
        )
        new_name = new_name.strip()
        if not ok or not new_name or new_name == tag["name"]:
            return
        self.tag_db.rename_tag(tag["id"], new_name)
        self._reload()
        self._notify_changed()

    def _change_color(self) -> None:
        tag = self._current_tag()
        if not tag:
            return
        color = QColorDialog.getColor(
            QColor(tag.get("color", "#607d8b")), self, "Choose tag color"
        )
        if not color.isValid():
            return
        self.tag_db.update_tag_color(tag["id"], color.name())
        self._reload()
        self._notify_changed()

    def _delete_tag(self) -> None:
        tag = self._current_tag()
        if not tag:
            return
        ans = QMessageBox.question(
            self,
            "Delete Tag",
            f"Delete tag '{tag['name']}'?\n\nThis removes it from all law databases.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.tag_db.delete_tag(tag["id"])
        self._reload()
        self._notify_changed()

    def _notify_changed(self) -> None:
        if callable(self._on_changed):
            try:
                self._on_changed()
            except Exception:
                pass
