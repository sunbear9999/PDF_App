"""
gui/components/dialogs/import_export_tab.py

Import / Export tab for the GlobalSettingsDialog.

Handles .ppack (Papyrus Pack) export and import with:
  - All categories shown (empty ones greyed out)
  - Edited-default prompts marked distinctly
  - Dependency warnings at export and import
  - Security warnings before importing executable categories
  - Per-item conflict resolution (rename / replace / skip)
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.base.core import FALLBACK_THEME, UnifiedThemedMixin
from core.models.pack_models import CATEGORY_DISPLAY_NAMES, CATEGORY_ORDER, PackItem
from core.services.pack_dependency import UNSAFE_CATEGORIES

if TYPE_CHECKING:
    from gui.app_context import AppContext
    from core.services.pack_service import PackService
    from core.models.pack_models import PackManifest
    from core.services.pack_dependency import (
        ConflictItem,
        ExportDepWarning,
        ImportDepWarning,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tree widget (shared by export and import panels)
# ─────────────────────────────────────────────────────────────────────────────

class _PackItemTree(QTreeWidget, UnifiedThemedMixin):
    """
    Two-column checkbox tree for export/import item selection.

    Column 0: checkbox + display name
    Column 1: brief metadata / description
    """

    def __init__(self, theme: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._theme = {**FALLBACK_THEME, **theme}
        self._blocking = False
        self.setColumnCount(2)
        self.setHeaderLabels(["Item", "Details"])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.setAlternatingRowColors(False)
        self.setUniformRowHeights(True)
        self.itemChanged.connect(self._on_item_changed)
        self._apply_style()

    def _apply_style(self) -> None:
        t = self._theme
        bg = t.get("bg_input", "#333")
        bg2 = t.get("bg_panel", "#2b2b2b")
        fg = t.get("text_main", "#fff")
        border = t.get("border", "#555")
        self.setStyleSheet(
            f"QTreeWidget {{ background: {bg}; color: {fg}; "
            f"border: 1px solid {border}; border-radius: 4px; outline: none; }}"
            f"QTreeWidget::item {{ padding: 3px 4px; }}"
            f"QTreeWidget::item:hover {{ background: {bg2}; }}"
            f"QHeaderView::section {{ background: {bg2}; color: {fg}; "
            f"border: none; border-bottom: 1px solid {border}; padding: 4px 6px; "
            f"font-size: 12px; }}"
        )

    def populate(
        self,
        categories: Dict[str, List[PackItem]],
        show_empty: bool = False,
    ) -> None:
        """
        Populate tree from category→items map.
        When show_empty=True, categories with no items appear greyed out.
        """
        self.clear()
        t = self._theme
        fg_muted = t.get("text_muted", "#888")

        for cat in CATEGORY_ORDER:
            items = categories.get(cat, [])
            if not items and not show_empty:
                continue

            label = CATEGORY_DISPLAY_NAMES.get(cat, cat)
            cat_node = QTreeWidgetItem([label, ""])
            cat_node.setData(0, Qt.ItemDataRole.UserRole, cat)

            font = QFont()
            font.setBold(True)
            cat_node.setFont(0, font)

            if items:
                cat_node.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserTristate
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                cat_node.setCheckState(0, Qt.CheckState.Checked)
            else:
                # Empty category — shown greyed, no checkbox
                cat_node.setFlags(Qt.ItemFlag.ItemIsEnabled)
                cat_node.setForeground(0, QColor(fg_muted))
                cat_node.setText(1, "No custom items")
                cat_node.setForeground(1, QColor(fg_muted))

            self.addTopLevelItem(cat_node)

            for item in items:
                detail = self._detail_str(item)
                child = QTreeWidgetItem([item.display_name, detail])
                child.setData(0, Qt.ItemDataRole.UserRole, item.item_id)
                child.setData(0, Qt.ItemDataRole.UserRole + 1, cat)
                child.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                child.setCheckState(0, Qt.CheckState.Checked)

                # Edited-default prompts get a visual hint
                if cat == "prompts" and item.metadata.get("is_default_override"):
                    font_c = QFont()
                    font_c.setItalic(True)
                    child.setFont(0, font_c)
                    child.setText(1, (detail + "  [default override]").strip())

                cat_node.addChild(child)

            cat_node.setExpanded(True)

    @staticmethod
    def _detail_str(item: PackItem) -> str:
        md = item.metadata
        if "description" in md and md["description"]:
            return str(md["description"])[:80]
        if "key" in md:
            return str(md["key"])
        if "step_type" in md:
            return str(md["step_type"])
        if "preview" in md:
            return str(md["preview"])[:80]
        if "colors_preview" in md:
            colors = md["colors_preview"]
            return f"bg={colors.get('bg_main','')} accent={colors.get('accent','')}"
        return ""

    def get_selection(self) -> Dict[str, List[str]]:
        """Return {category: [item_ids]} for all checked leaf items."""
        result: Dict[str, List[str]] = {}
        for i in range(self.topLevelItemCount()):
            cat_node = self.topLevelItem(i)
            cat = cat_node.data(0, Qt.ItemDataRole.UserRole)
            selected = []
            for j in range(cat_node.childCount()):
                child = cat_node.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    selected.append(child.data(0, Qt.ItemDataRole.UserRole))
            if selected:
                result[cat] = selected
        return result

    def add_ids_to_selection(self, category: str, item_ids: List[str]) -> None:
        """Check specific items by category+id (for auto-adding deps)."""
        for i in range(self.topLevelItemCount()):
            cat_node = self.topLevelItem(i)
            if cat_node.data(0, Qt.ItemDataRole.UserRole) != category:
                continue
            for j in range(cat_node.childCount()):
                child = cat_node.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) in item_ids:
                    child.setCheckState(0, Qt.CheckState.Checked)

    def select_all(self) -> None:
        self._blocking = True
        for i in range(self.topLevelItemCount()):
            cat_node = self.topLevelItem(i)
            if not (cat_node.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            cat_node.setCheckState(0, Qt.CheckState.Checked)
            for j in range(cat_node.childCount()):
                cat_node.child(j).setCheckState(0, Qt.CheckState.Checked)
        self._blocking = False

    def clear_all(self) -> None:
        self._blocking = True
        for i in range(self.topLevelItemCount()):
            cat_node = self.topLevelItem(i)
            if not (cat_node.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            cat_node.setCheckState(0, Qt.CheckState.Unchecked)
            for j in range(cat_node.childCount()):
                cat_node.child(j).setCheckState(0, Qt.CheckState.Unchecked)
        self._blocking = False

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._blocking or column != 0:
            return
        self._blocking = True
        parent = item.parent()
        if parent is None:
            state = item.checkState(0)
            if state == Qt.CheckState.PartiallyChecked:
                state = Qt.CheckState.Checked
                item.setCheckState(0, state)
            for j in range(item.childCount()):
                item.child(j).setCheckState(0, state)
        else:
            checked = sum(
                1 for j in range(parent.childCount())
                if parent.child(j).checkState(0) == Qt.CheckState.Checked
            )
            total = parent.childCount()
            if checked == total:
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif checked == 0:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self._blocking = False


# ─────────────────────────────────────────────────────────────────────────────
# Export dependency warning dialog
# ─────────────────────────────────────────────────────────────────────────────

class _ExportDepDialog(QDialog):
    """
    Shown when the export selection is missing dependencies.
    User can choose to auto-include them or export as-is.
    """

    def __init__(
        self,
        warnings: List["ExportDepWarning"],
        theme: dict,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Missing Dependencies")
        self.setMinimumWidth(480)
        t = {**FALLBACK_THEME, **theme}
        fg = t.get("text_main", "#fff")
        bg = t.get("bg_main", "#1e1e1e")
        bg_panel = t.get("bg_panel", "#2b2b2b")
        border = t.get("border", "#555")
        accent = t.get("accent", "#0078D7")
        fg_muted = t.get("text_muted", "#999")

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {fg}; }}"
            f"QLabel {{ color: {fg}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        hdr = QLabel(
            "Some selected items have dependencies that are not included in your selection:"
        )
        hdr.setWordWrap(True)
        layout.addWidget(hdr)

        # Group warnings by item
        from collections import defaultdict
        by_item: Dict[str, List["ExportDepWarning"]] = defaultdict(list)
        for w in warnings:
            by_item[w.item_id].append(w)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(220)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {border}; border-radius: 4px; background: {bg_panel}; }}"
        )
        inner = QWidget()
        inner.setStyleSheet(f"background: {bg_panel};")
        inner_l = QVBoxLayout(inner)
        inner_l.setContentsMargins(10, 8, 10, 8)
        inner_l.setSpacing(4)

        for item_id, item_warnings in by_item.items():
            dep_names = ", ".join(w.missing_dep_id for w in item_warnings)
            cat = item_warnings[0].category
            cat_label = CATEGORY_DISPLAY_NAMES.get(cat, cat)
            lbl = QLabel(f"<b>{item_id}</b> ({cat_label}) needs: <i>{dep_names}</i>")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {fg}; background: transparent;")
            inner_l.addWidget(lbl)

        inner_l.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        note = QLabel(
            "Include the missing dependencies in the export so recipients have everything they need."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {fg_muted}; font-size: 12px;")
        layout.addWidget(note)

        btn_box = QDialogButtonBox()
        btn_include = btn_box.addButton(
            "Include Dependencies", QDialogButtonBox.ButtonRole.AcceptRole
        )
        btn_skip = btn_box.addButton(
            "Export As-Is", QDialogButtonBox.ButtonRole.RejectRole
        )
        for btn in (btn_include, btn_skip):
            btn.setStyleSheet(
                f"QPushButton {{ background: {bg_panel}; color: {fg}; "
                f"border: 1px solid {border}; border-radius: 4px; padding: 6px 16px; }}"
                f"QPushButton:hover {{ background: {accent}; color: white; border-color: {accent}; }}"
            )
        layout.addWidget(btn_box)

        btn_include.clicked.connect(self.accept)
        btn_skip.clicked.connect(self.reject)

        self._warnings = warnings

    def get_extra_selection(self) -> Dict[str, List[str]]:
        """Call after accept() — returns the deps that should be added to selection."""
        by_cat: Dict[str, List[str]] = {}
        for w in self._warnings:
            by_cat.setdefault(w.dep_category, []).append(w.missing_dep_id)
        return by_cat


# ─────────────────────────────────────────────────────────────────────────────
# Security warning dialog
# ─────────────────────────────────────────────────────────────────────────────

class _SecurityWarningDialog(QDialog):
    """
    Shown before importing from UNSAFE_CATEGORIES (plugins, blueprints, steps).
    User must confirm they trust the source.
    """

    def __init__(
        self,
        unsafe_cats: List[str],
        theme: dict,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Security Warning")
        self.setMinimumWidth(440)
        t = {**FALLBACK_THEME, **theme}
        fg = t.get("text_main", "#fff")
        bg = t.get("bg_main", "#1e1e1e")
        bg_panel = t.get("bg_panel", "#2b2b2b")
        border = t.get("border", "#555")
        accent = t.get("accent", "#0078D7")
        warn_color = "#e8a202"

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {fg}; }}"
            f"QLabel {{ color: {fg}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        warn_lbl = QLabel("⚠  This pack contains executable content")
        warn_lbl.setStyleSheet(
            f"color: {warn_color}; font-size: 14px; font-weight: bold;"
        )
        layout.addWidget(warn_lbl)

        cat_names = ", ".join(
            CATEGORY_DISPLAY_NAMES.get(c, c) for c in unsafe_cats
        )
        body = QLabel(
            f"The following categories can execute arbitrary code on your system:\n\n"
            f"  {cat_names}\n\n"
            f"Only import packs from sources you trust. Malicious packs could "
            f"compromise your system or data."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {fg}; font-size: 13px;")
        layout.addWidget(body)

        self._confirm = QCheckBox("I trust the source of this pack and want to proceed")
        self._confirm.setStyleSheet(f"color: {fg}; font-size: 13px;")
        layout.addWidget(self._confirm)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        for btn in btn_box.buttons():
            btn.setStyleSheet(
                f"QPushButton {{ background: {bg_panel}; color: {fg}; "
                f"border: 1px solid {border}; border-radius: 4px; padding: 6px 16px; }}"
                f"QPushButton:hover {{ background: {accent}; color: white; border-color: {accent}; }}"
            )
        ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Proceed with Import")
        ok_btn.setEnabled(False)
        self._confirm.toggled.connect(ok_btn.setEnabled)
        layout.addWidget(btn_box)

        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)


# ─────────────────────────────────────────────────────────────────────────────
# Conflict resolution dialog
# ─────────────────────────────────────────────────────────────────────────────

class _ConflictDialog(QDialog):
    """
    Per-item conflict resolution: rename, replace, or skip.
    """

    SKIP = "skip"
    REPLACE = "replace"
    RENAME = "rename"

    def __init__(
        self,
        conflicts: List["ConflictItem"],
        theme: dict,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Name Conflicts")
        self.setMinimumWidth(560)
        self.setMinimumHeight(400)
        t = {**FALLBACK_THEME, **theme}
        fg = t.get("text_main", "#fff")
        bg = t.get("bg_main", "#1e1e1e")
        bg_panel = t.get("bg_panel", "#2b2b2b")
        border = t.get("border", "#555")
        accent = t.get("accent", "#0078D7")
        fg_muted = t.get("text_muted", "#999")

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {fg}; }}"
            f"QLabel {{ color: {fg}; }}"
            f"QLineEdit {{ background: {t.get('bg_input','#333')}; color: {fg}; "
            f"border: 1px solid {border}; border-radius: 4px; padding: 4px 6px; }}"
        )

        self._conflicts = conflicts
        # {item_id: {"action": SKIP|REPLACE|RENAME, "name": str}}
        self._choices: Dict[str, dict] = {}
        self._rename_inputs: Dict[str, QLineEdit] = {}
        self._btn_groups: Dict[str, QButtonGroup] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 14, 16, 14)

        hdr = QLabel(
            "The following items already exist locally. Choose how to handle each:"
        )
        hdr.setWordWrap(True)
        layout.addWidget(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {border}; border-radius: 4px; background: {bg_panel}; }}"
        )
        container = QWidget()
        container.setStyleSheet(f"background: {bg_panel};")
        grid = QGridLayout(container)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setSpacing(6)
        grid.setColumnStretch(3, 1)

        # Header row
        for col, text in enumerate(["Item", "Skip", "Replace", "Rename as"]):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {fg_muted}; font-size: 11px; font-weight: bold;")
            grid.addWidget(lbl, 0, col)

        row = 1
        for conflict in conflicts:
            cat_label = CATEGORY_DISPLAY_NAMES.get(conflict.category, conflict.category)
            name_lbl = QLabel(f"{conflict.display_name}\n<small>{cat_label}</small>")
            name_lbl.setWordWrap(True)
            name_lbl.setTextFormat(Qt.TextFormat.RichText)
            name_lbl.setStyleSheet(f"color: {fg}; background: transparent;")
            grid.addWidget(name_lbl, row, 0)

            btn_skip = QRadioButton("Skip")
            btn_replace = QRadioButton("Replace")
            btn_rename = QRadioButton()
            for rb in (btn_skip, btn_replace, btn_rename):
                rb.setStyleSheet(f"color: {fg}; background: transparent;")

            rename_input = QLineEdit()
            rename_input.setPlaceholderText(f"{conflict.display_name} (copy)")
            rename_input.setText(f"{conflict.display_name} (copy)")
            rename_input.setEnabled(False)
            rename_input.setMinimumWidth(120)
            self._rename_inputs[conflict.item_id] = rename_input

            grp = QButtonGroup(self)
            grp.addButton(btn_skip, 0)
            grp.addButton(btn_replace, 1)
            grp.addButton(btn_rename, 2)
            btn_skip.setChecked(True)
            self._choices[conflict.item_id] = {"action": self.SKIP, "name": ""}
            self._btn_groups[conflict.item_id] = grp

            cid = conflict.item_id

            def _make_handler(item_id, r_input):
                def handler(btn_id):
                    actions = [self.SKIP, self.REPLACE, self.RENAME]
                    self._choices[item_id]["action"] = actions[btn_id]
                    r_input.setEnabled(btn_id == 2)
                return handler

            grp.idClicked.connect(_make_handler(cid, rename_input))

            grid.addWidget(btn_skip, row, 1)
            grid.addWidget(btn_replace, row, 2)
            rename_row = QHBoxLayout()
            rename_row.setSpacing(4)
            rename_row.addWidget(btn_rename)
            rename_row.addWidget(rename_input)
            rename_cell = QWidget()
            rename_cell.setStyleSheet(f"background: transparent;")
            rename_cell.setLayout(rename_row)
            grid.addWidget(rename_cell, row, 3)

            row += 1

        container.setLayout(grid)
        scroll.setWidget(container)
        layout.addWidget(scroll)

        # Apply all / quick actions row
        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        for label, action_id in [("Skip All", 0), ("Replace All", 1)]:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {fg_muted}; "
                f"border: 1px solid {border}; border-radius: 4px; padding: 2px 10px; font-size: 12px; }}"
                f"QPushButton:hover {{ color: {fg}; background: {bg_panel}; }}"
            )
            btn.clicked.connect(lambda checked=False, aid=action_id: self._apply_all(aid))
            quick_row.addWidget(btn)
        quick_row.addStretch()
        layout.addLayout(quick_row)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        for btn in btn_box.buttons():
            btn.setStyleSheet(
                f"QPushButton {{ background: {bg_panel}; color: {fg}; "
                f"border: 1px solid {border}; border-radius: 4px; padding: 6px 16px; }}"
                f"QPushButton:hover {{ background: {accent}; color: white; border-color: {accent}; }}"
            )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Apply & Import")
        layout.addWidget(btn_box)

        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)

    def _apply_all(self, btn_id: int) -> None:
        actions = [self.SKIP, self.REPLACE, self.RENAME]
        for cid, grp in self._btn_groups.items():
            grp.button(btn_id).setChecked(True)
            self._choices[cid]["action"] = actions[btn_id]
            self._rename_inputs[cid].setEnabled(btn_id == 2)

    def get_resolution(self) -> Tuple[Dict[str, str], List[str]]:
        """
        Returns:
          renames: {original_id: new_id}  — for RENAME action
          skip_ids: [item_id, ...]        — items the user chose to skip
        """
        renames: Dict[str, str] = {}
        skips: List[str] = []
        for conflict in self._conflicts:
            cid = conflict.item_id
            action = self._choices[cid]["action"]
            if action == self.SKIP:
                skips.append(cid)
            elif action == self.RENAME:
                new_name = self._rename_inputs[cid].text().strip()
                if new_name and new_name != cid:
                    renames[cid] = new_name
        return renames, skips


# ─────────────────────────────────────────────────────────────────────────────
# Import dependency warning dialog
# ─────────────────────────────────────────────────────────────────────────────

class _ImportDepDialog(QDialog):
    """
    Shown when importing items whose dependencies are missing locally.
    User can include available deps from the pack, or import broken.
    """

    def __init__(
        self,
        warnings: List["ImportDepWarning"],
        theme: dict,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Missing Dependencies")
        self.setMinimumWidth(500)
        t = {**FALLBACK_THEME, **theme}
        fg = t.get("text_main", "#fff")
        bg = t.get("bg_main", "#1e1e1e")
        bg_panel = t.get("bg_panel", "#2b2b2b")
        border = t.get("border", "#555")
        accent = t.get("accent", "#0078D7")
        fg_muted = t.get("text_muted", "#999")

        self.setStyleSheet(
            f"QDialog {{ background: {bg}; color: {fg}; }}"
            f"QLabel {{ color: {fg}; }}"
        )

        self._warnings = warnings
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        hdr = QLabel(
            "Some items you're importing require dependencies that are not installed:"
        )
        hdr.setWordWrap(True)
        layout.addWidget(hdr)

        # List warnings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(200)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {border}; border-radius: 4px; background: {bg_panel}; }}"
        )
        inner = QWidget()
        inner.setStyleSheet(f"background: {bg_panel};")
        inner_l = QVBoxLayout(inner)
        inner_l.setContentsMargins(10, 8, 10, 8)
        inner_l.setSpacing(4)

        from collections import defaultdict
        by_item: Dict[str, List["ImportDepWarning"]] = defaultdict(list)
        for w in warnings:
            by_item[w.item_id].append(w)

        for item_id, item_warnings in by_item.items():
            cat_label = CATEGORY_DISPLAY_NAMES.get(item_warnings[0].category, item_warnings[0].category)
            lines = []
            for w in item_warnings:
                dep_cat = CATEGORY_DISPLAY_NAMES.get(w.dep_category, w.dep_category)
                suffix = " (also in this pack)" if w.dep_in_pack else " (not in this pack)"
                lines.append(f"  • {w.missing_dep_id} ({dep_cat}){suffix}")
            text = f"<b>{item_id}</b> ({cat_label}):\n" + "\n".join(lines)
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {fg}; background: transparent;")
            inner_l.addWidget(lbl)
        inner_l.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        # Options
        in_pack = any(w.dep_in_pack for w in warnings)
        if in_pack:
            self._include_rb = QRadioButton(
                "Also import available dependencies from this pack (recommended)"
            )
            self._include_rb.setChecked(True)
            self._include_rb.setStyleSheet(f"color: {fg};")
            layout.addWidget(self._include_rb)

        self._broken_rb = QRadioButton("Import anyway (items may not work correctly)")
        self._broken_rb.setStyleSheet(f"color: {fg};")
        layout.addWidget(self._broken_rb)

        if in_pack:
            grp = QButtonGroup(self)
            grp.addButton(self._include_rb)
            grp.addButton(self._broken_rb)
        else:
            self._include_rb = None

        note = QLabel(
            "Dependencies marked 'not in this pack' cannot be automatically included."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {fg_muted}; font-size: 11px;")
        layout.addWidget(note)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        for btn in btn_box.buttons():
            btn.setStyleSheet(
                f"QPushButton {{ background: {bg_panel}; color: {fg}; "
                f"border: 1px solid {border}; border-radius: 4px; padding: 6px 16px; }}"
                f"QPushButton:hover {{ background: {accent}; color: white; border-color: {accent}; }}"
            )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Continue")
        layout.addWidget(btn_box)

        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)

    def should_include_pack_deps(self) -> bool:
        """True if user chose to auto-include available deps from the pack."""
        return (
            self._include_rb is not None
            and self._include_rb.isChecked()
        )

    def get_extra_deps(self) -> Dict[str, List[str]]:
        """Return {category: [ids]} of deps that exist in the pack and should be added."""
        if not self.should_include_pack_deps():
            return {}
        extra: Dict[str, List[str]] = {}
        for w in self._warnings:
            if w.dep_in_pack:
                extra.setdefault(w.dep_category, []).append(w.missing_dep_id)
        return extra


# ─────────────────────────────────────────────────────────────────────────────
# Export panel
# ─────────────────────────────────────────────────────────────────────────────

class _ExportPanel(QWidget, UnifiedThemedMixin):

    def __init__(
        self,
        pack_service: Optional["PackService"],
        bus,
        theme: dict,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._pack_service = pack_service
        self._bus = bus
        self._theme = {**FALLBACK_THEME, **theme}
        self._loaded = False
        self._build_ui()

    def _build_ui(self) -> None:
        t = self._theme
        bg_panel = t.get("bg_panel", "#2b2b2b")
        fg = t.get("text_main", "#fff")
        fg_muted = t.get("text_muted", "#999")
        border = t.get("border", "#555")
        accent = t.get("accent", "#0078D7")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Pack metadata inputs
        meta_frame = QFrame()
        meta_frame.setStyleSheet(
            f"QFrame {{ background: {bg_panel}; border: 1px solid {border}; border-radius: 6px; }}"
        )
        meta_layout = QVBoxLayout(meta_frame)
        meta_layout.setContentsMargins(12, 10, 12, 10)
        meta_layout.setSpacing(6)

        lbl_name = QLabel("Pack Name")
        lbl_name.setStyleSheet(f"color: {fg}; font-size: 12px; font-weight: bold; border: none;")
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("My Custom Pack")
        inp_style = (
            f"background: {t.get('bg_input','#333')}; color: {fg}; "
            f"border: 1px solid {border}; border-radius: 4px; padding: 5px 8px;"
        )
        self._name_input.setStyleSheet(inp_style)

        lbl_desc = QLabel("Description (optional)")
        lbl_desc.setStyleSheet(f"color: {fg_muted}; font-size: 12px; border: none;")
        self._desc_input = QLineEdit()
        self._desc_input.setPlaceholderText("A brief description of what's in this pack")
        self._desc_input.setStyleSheet(inp_style)

        meta_layout.addWidget(lbl_name)
        meta_layout.addWidget(self._name_input)
        meta_layout.addWidget(lbl_desc)
        meta_layout.addWidget(self._desc_input)
        layout.addWidget(meta_frame)

        # Tree toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        lbl_tree = QLabel("Select items to export:")
        lbl_tree.setStyleSheet(f"color: {fg}; font-size: 13px; font-weight: bold;")
        toolbar.addWidget(lbl_tree)
        toolbar.addStretch()

        def _mini_btn(label):
            b = QPushButton(label)
            b.setFixedHeight(26)
            b.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {fg_muted}; "
                f"border: 1px solid {border}; border-radius: 4px; padding: 2px 10px; font-size: 12px; }}"
                f"QPushButton:hover {{ color: {fg}; background: {bg_panel}; }}"
            )
            return b

        btn_all = _mini_btn("Select All")
        btn_none = _mini_btn("Select None")
        btn_refresh = _mini_btn("Refresh")
        for b in (btn_all, btn_none, btn_refresh):
            toolbar.addWidget(b)
        layout.addLayout(toolbar)

        # Tree
        self._tree = _PackItemTree(t, self)
        layout.addWidget(self._tree, 1)

        # Export button
        export_row = QHBoxLayout()
        export_row.addStretch()
        self._btn_export = QPushButton("Export Pack…")
        self._btn_export.setFixedHeight(34)
        self._btn_export.setStyleSheet(
            f"QPushButton {{ background: {accent}; color: white; border: none; "
            f"border-radius: 4px; padding: 6px 22px; font-weight: bold; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {t.get('accent_hover', accent)}; }}"
            f"QPushButton:disabled {{ background: {border}; color: {fg_muted}; }}"
        )
        export_row.addWidget(self._btn_export)
        layout.addLayout(export_row)

        btn_all.clicked.connect(self._tree.select_all)
        btn_none.clicked.connect(self._tree.clear_all)
        btn_refresh.clicked.connect(self._load_items)
        self._btn_export.clicked.connect(self._do_export)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._load_items()

    def _load_items(self) -> None:
        self._loaded = True
        if not self._pack_service:
            return
        try:
            cats = self._pack_service.get_exportable_categories(include_empty=True)
        except Exception:
            return
        self._tree.populate(cats, show_empty=True)

    def _do_export(self) -> None:
        if not self._pack_service:
            return
        selection = self._tree.get_selection()
        if not selection:
            QMessageBox.information(
                self, "Nothing Selected", "Please select at least one item to export."
            )
            return

        # Dependency check
        try:
            dep_warnings = self._pack_service.analyze_export_deps(selection)
        except Exception:
            dep_warnings = []

        if dep_warnings:
            dlg = _ExportDepDialog(dep_warnings, self._theme, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                # Auto-add the missing deps to the selection and re-check the tree
                extra = dlg.get_extra_selection()
                for cat, ids in extra.items():
                    self._tree.add_ids_to_selection(cat, ids)
                # Re-read selection after updating the tree
                selection = self._tree.get_selection()

        name = self._name_input.text().strip() or "My Pack"
        desc = self._desc_input.text().strip()

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Papyrus Pack", f"{name}.ppack", "Papyrus Pack (*.ppack)"
        )
        if not path:
            return
        if not path.endswith(".ppack"):
            path += ".ppack"

        self._pack_service.pack_exported.connect(
            self._on_exported, Qt.ConnectionType.SingleShotConnection
        )
        self._pack_service.operation_failed.connect(
            self._on_failed, Qt.ConnectionType.SingleShotConnection
        )
        self._pack_service.export_pack(path, selection, name, desc)

    def _on_exported(self, file_path: str) -> None:
        fname = os.path.basename(file_path)
        if self._bus:
            try:
                self._bus.plugin_notification_requested.emit(
                    f"Pack exported: {fname}", "success", 4000
                )
            except Exception:
                pass
        QMessageBox.information(self, "Export Complete", f"Pack saved to:\n{file_path}")

    def _on_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Export Failed", message)


# ─────────────────────────────────────────────────────────────────────────────
# Import panel
# ─────────────────────────────────────────────────────────────────────────────

class _ImportPanel(QWidget, UnifiedThemedMixin):

    def __init__(
        self,
        pack_service: Optional["PackService"],
        bus,
        theme: dict,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._pack_service = pack_service
        self._bus = bus
        self._theme = {**FALLBACK_THEME, **theme}
        self._current_file: str = ""
        self._pack_categories: Dict[str, List[PackItem]] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        t = self._theme
        bg_panel = t.get("bg_panel", "#2b2b2b")
        fg = t.get("text_main", "#fff")
        fg_muted = t.get("text_muted", "#999")
        border = t.get("border", "#555")
        accent = t.get("accent", "#0078D7")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # File picker
        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        lbl = QLabel("Pack file:")
        lbl.setStyleSheet(f"color: {fg}; font-size: 13px;")
        file_row.addWidget(lbl)

        self._path_label = QLabel("No file selected")
        self._path_label.setStyleSheet(f"color: {fg_muted}; font-size: 12px;")
        self._path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        file_row.addWidget(self._path_label, 1)

        btn_browse = QPushButton("Browse…")
        btn_browse.setFixedHeight(30)
        btn_browse.setStyleSheet(
            f"QPushButton {{ background: {bg_panel}; color: {fg}; "
            f"border: 1px solid {border}; border-radius: 4px; padding: 4px 14px; }}"
            f"QPushButton:hover {{ background: {t.get('bg_input','#333')}; }}"
        )
        file_row.addWidget(btn_browse)
        layout.addLayout(file_row)

        # Pack info frame
        self._info_frame = QFrame()
        self._info_frame.setStyleSheet(
            f"QFrame {{ background: {bg_panel}; border: 1px solid {border}; border-radius: 6px; }}"
        )
        info_layout = QVBoxLayout(self._info_frame)
        info_layout.setContentsMargins(12, 8, 12, 8)
        info_layout.setSpacing(4)

        self._info_name = QLabel()
        self._info_name.setStyleSheet(
            f"color: {fg}; font-size: 13px; font-weight: bold; border: none;"
        )
        self._info_desc = QLabel()
        self._info_desc.setStyleSheet(f"color: {fg_muted}; font-size: 12px; border: none;")
        self._info_desc.setWordWrap(True)
        self._info_meta = QLabel()
        self._info_meta.setStyleSheet(f"color: {fg_muted}; font-size: 11px; border: none;")

        info_layout.addWidget(self._info_name)
        info_layout.addWidget(self._info_desc)
        info_layout.addWidget(self._info_meta)
        self._info_frame.setVisible(False)
        layout.addWidget(self._info_frame)

        # Toolbar
        toolbar = QHBoxLayout()
        lbl_tree = QLabel("Select items to import:")
        lbl_tree.setStyleSheet(f"color: {fg}; font-size: 13px; font-weight: bold;")
        toolbar.addWidget(lbl_tree)
        toolbar.addStretch()

        def _mini_btn(label):
            b = QPushButton(label)
            b.setFixedHeight(26)
            b.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {fg_muted}; "
                f"border: 1px solid {border}; border-radius: 4px; padding: 2px 10px; font-size: 12px; }}"
                f"QPushButton:hover {{ color: {fg}; background: {bg_panel}; }}"
            )
            return b

        btn_all = _mini_btn("Select All")
        btn_none = _mini_btn("Select None")
        for b in (btn_all, btn_none):
            toolbar.addWidget(b)
        layout.addLayout(toolbar)

        # Tree
        self._tree = _PackItemTree(t, self)
        self._tree.setVisible(False)
        layout.addWidget(self._tree, 1)

        # Placeholder
        self._placeholder = QLabel("Browse for a .ppack file to see its contents.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(f"color: {fg_muted}; font-size: 13px; border: none;")
        layout.addWidget(self._placeholder, 1)

        # Import button
        import_row = QHBoxLayout()
        import_row.addStretch()
        self._btn_import = QPushButton("Import Selected")
        self._btn_import.setFixedHeight(34)
        self._btn_import.setEnabled(False)
        self._btn_import.setStyleSheet(
            f"QPushButton {{ background: {accent}; color: white; border: none; "
            f"border-radius: 4px; padding: 6px 22px; font-weight: bold; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {t.get('accent_hover', accent)}; }}"
            f"QPushButton:disabled {{ background: {border}; color: {fg_muted}; }}"
        )
        import_row.addWidget(self._btn_import)
        layout.addLayout(import_row)

        btn_browse.clicked.connect(self._browse)
        btn_all.clicked.connect(self._tree.select_all)
        btn_none.clicked.connect(self._tree.clear_all)
        self._btn_import.clicked.connect(self._do_import)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Papyrus Pack", "", "Papyrus Pack (*.ppack)"
        )
        if path:
            self._load_pack(path)

    def _load_pack(self, path: str) -> None:
        if not self._pack_service:
            return
        self._current_file = path
        self._path_label.setText(os.path.basename(path))

        manifest, categories = self._pack_service.preview_pack(path)
        self._pack_categories = categories

        if manifest is None:
            self._info_frame.setVisible(False)
            self._tree.setVisible(False)
            self._placeholder.setText("Failed to read pack file.")
            self._placeholder.setVisible(True)
            self._btn_import.setEnabled(False)
            return

        self._info_name.setText(manifest.name or os.path.basename(path))
        self._info_desc.setText(manifest.description or "")
        parts = []
        if manifest.app_version:
            parts.append(f"App v{manifest.app_version}")
        if manifest.created_at:
            parts.append(manifest.created_at[:10])
        self._info_meta.setText("  ·  ".join(parts))
        self._info_frame.setVisible(True)

        if categories:
            self._tree.populate(categories, show_empty=False)
            self._tree.setVisible(True)
            self._placeholder.setVisible(False)
            self._btn_import.setEnabled(True)
        else:
            self._tree.setVisible(False)
            self._placeholder.setText("This pack is empty or contains no recognized items.")
            self._placeholder.setVisible(True)
            self._btn_import.setEnabled(False)

    def _do_import(self) -> None:
        if not self._pack_service or not self._current_file:
            return
        selection = self._tree.get_selection()
        if not selection:
            QMessageBox.information(
                self, "Nothing Selected", "Please select at least one item to import."
            )
            return

        # 1. Security warning for unsafe categories
        unsafe_in_sel = [c for c in selection if c in UNSAFE_CATEGORIES]
        if unsafe_in_sel:
            dlg = _SecurityWarningDialog(unsafe_in_sel, self._theme, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return

        # 2. Conflict resolution
        renames: Dict[str, str] = {}
        try:
            conflicts = self._pack_service.find_import_conflicts(
                self._pack_categories, selection
            )
        except Exception:
            conflicts = []

        if conflicts:
            dlg = _ConflictDialog(conflicts, self._theme, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            renames, skip_ids = dlg.get_resolution()
            # Remove skipped items from selection
            for cat, ids in list(selection.items()):
                filtered = [i for i in ids if i not in skip_ids]
                if filtered:
                    selection[cat] = filtered
                else:
                    del selection[cat]
            if not selection:
                return

        # 3. Dependency check
        try:
            dep_warnings = self._pack_service.analyze_import_deps(
                self._current_file, self._pack_categories, selection
            )
        except Exception:
            dep_warnings = []

        if dep_warnings:
            dlg = _ImportDepDialog(dep_warnings, self._theme, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            extra = dlg.get_extra_deps()
            for cat, ids in extra.items():
                existing = selection.get(cat, [])
                selection[cat] = list(set(existing + ids))

        # 4. Run the actual import
        self._pack_service.pack_imported.connect(
            self._on_imported, Qt.ConnectionType.SingleShotConnection
        )
        self._pack_service.operation_failed.connect(
            self._on_failed, Qt.ConnectionType.SingleShotConnection
        )
        self._pack_service.import_pack(self._current_file, selection, renames)

    def _on_imported(self, summary: dict) -> None:
        if not summary:
            QMessageBox.information(self, "Import Complete", "No items were imported.")
            return
        lines = [
            f"  • {CATEGORY_DISPLAY_NAMES.get(cat, cat)}: {count} item(s)"
            for cat, count in summary.items()
        ]
        msg = "Successfully imported:\n" + "\n".join(lines)
        if self._bus:
            try:
                total = sum(summary.values())
                self._bus.plugin_notification_requested.emit(
                    f"Pack imported: {total} item(s)", "success", 4000
                )
            except Exception:
                pass
        QMessageBox.information(self, "Import Complete", msg)

    def _on_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Import Failed", message)


# ─────────────────────────────────────────────────────────────────────────────
# Main tab widget
# ─────────────────────────────────────────────────────────────────────────────

class ImportExportTab(QWidget, UnifiedThemedMixin):
    """
    Settings tab for importing and exporting .ppack configuration bundles.
    Follows the GlobalSettingsDialog tab protocol (apply_changes / has_pending).
    """

    def __init__(
        self,
        app_context: "AppContext",
        theme: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._app_context = app_context
        self._theme = {**FALLBACK_THEME, **(theme or {})}

        pack_svc = getattr(app_context, "pack_service", None)
        bus = getattr(app_context, "bus", None)

        self._tabs = QTabWidget()
        self._export_panel = _ExportPanel(pack_svc, bus, self._theme, self)
        self._import_panel = _ImportPanel(pack_svc, bus, self._theme, self)
        self._build_ui()

    def _build_ui(self) -> None:
        t = self._theme
        bg = t.get("bg_main", "#1e1e1e")
        bg_panel = t.get("bg_panel", "#2b2b2b")
        fg = t.get("text_main", "#fff")
        fg_muted = t.get("text_muted", "#999")
        border = t.get("border", "#555")
        accent = t.get("accent", "#0078D7")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: none; background: {bg}; }}"
            f"QTabBar::tab {{ background: {bg_panel}; color: {fg_muted}; "
            f"padding: 7px 20px; border: none; border-right: 1px solid {border}; "
            f"font-size: 13px; }}"
            f"QTabBar::tab:selected {{ background: {bg}; color: {fg}; "
            f"border-bottom: 2px solid {accent}; }}"
            f"QTabBar::tab:hover:!selected {{ background: {bg}; color: {fg}; }}"
        )

        self._tabs.addTab(self._export_panel, "Export")
        self._tabs.addTab(self._import_panel, "Import")
        outer.addWidget(self._tabs)

        info_bar = QLabel(
            "Plugins can contribute additional exportable data via api.register_pack_contributor()."
        )
        info_bar.setWordWrap(True)
        info_bar.setStyleSheet(
            f"color: {fg_muted}; font-size: 11px; padding: 6px 16px; "
            f"background: {bg_panel}; border-top: 1px solid {border};"
        )
        outer.addWidget(info_bar)

    def apply_changes(self) -> None:
        pass

    def has_pending(self) -> bool:
        return False
