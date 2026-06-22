"""
gui/docks/unified_research/components/item_selector_widget.py

A reusable widget displayed when a SHOW_ITEM_SELECTOR step pauses the runner.
Shows a scrollable list of checkable items and an action button. When the user
confirms, emits form_submitted({selected_items: [...]}) so the runner resumes.
"""
from __future__ import annotations

import json
from typing import Any, List

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)


class ItemSelectorWidget(QWidget):
    """Checkable list with a confirm button; wire form_submitted → runner.submit_user_input."""

    form_submitted = Signal(dict)

    def __init__(
        self,
        step_id: str,
        items: List[Any],
        title: str = "Select Items",
        action_label: str = "Confirm",
        display_key: str = "",
        subtitle_key: str = "",
        theme: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._step_id = step_id
        self._items = items
        self._display_key = display_key
        self._subtitle_key = subtitle_key
        self._theme = theme or {}
        self._boxes: List[QCheckBox] = []

        self._build_ui(title, action_label)

    def _item_label(self, item: Any) -> str:
        if isinstance(item, dict):
            if self._display_key and self._display_key in item:
                return str(item[self._display_key])
            return (
                item.get("claim") or item.get("text") or item.get("label")
                or item.get("title") or json.dumps(item)
            )
        return str(item)

    def _item_subtitle(self, item: Any) -> str:
        if isinstance(item, dict) and self._subtitle_key and self._subtitle_key in item:
            return str(item[self._subtitle_key])
        if isinstance(item, dict):
            return item.get("type") or item.get("category") or ""
        return ""

    def _build_ui(self, title: str, action_label: str):
        bg = self._theme.get("bg_panel", "#1e1e2e")
        text_main = self._theme.get("text_main", "#e0e0e0")
        text_muted = self._theme.get("text_muted", "#888")
        accent = self._theme.get("accent", "#8b63d8")
        border = self._theme.get("border", "#3a3a5c")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header
        hdr = QLabel(f"<b>{title}</b>")
        hdr.setStyleSheet(f"color:{text_main}; padding:6px 4px 2px 4px; font-size:13px;")
        layout.addWidget(hdr)

        # Select-all row
        ctrl_row = QHBoxLayout()
        ctrl_row.setContentsMargins(4, 0, 4, 0)
        btn_all = QPushButton("Select All")
        btn_none = QPushButton("Select None")
        for b in (btn_all, btn_none):
            b.setFixedHeight(22)
            b.setStyleSheet(f"color:{text_muted}; border:1px solid {border}; border-radius:3px; padding:0 6px;")
        btn_all.clicked.connect(self._select_all)
        btn_none.clicked.connect(self._select_none)
        ctrl_row.addWidget(btn_all)
        ctrl_row.addWidget(btn_none)
        ctrl_row.addStretch()
        layout.addLayout(ctrl_row)

        # Scrollable list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")
        inner = QWidget()
        inner.setStyleSheet(f"background:{bg};")
        list_layout = QVBoxLayout(inner)
        list_layout.setContentsMargins(4, 4, 4, 4)
        list_layout.setSpacing(3)

        for item in self._items:
            frame = QFrame()
            frame.setStyleSheet(
                f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius:5px; }}"
                f"QFrame:hover {{ border-color: {accent}; }}"
            )
            row = QVBoxLayout(frame)
            row.setContentsMargins(8, 6, 8, 6)
            row.setSpacing(2)

            label_text = self._item_label(item)
            cb = QCheckBox(label_text)
            cb.setChecked(True)
            cb.setWordWrap(True)
            cb.setStyleSheet(f"color:{text_main};")
            cb.setProperty("_item", item)
            self._boxes.append(cb)
            row.addWidget(cb)

            sub = self._item_subtitle(item)
            if sub:
                sub_lbl = QLabel(sub)
                sub_lbl.setStyleSheet(f"color:{text_muted}; font-size:11px; padding-left:20px;")
                row.addWidget(sub_lbl)

            list_layout.addWidget(frame)

        list_layout.addStretch()
        scroll.setWidget(inner)
        scroll.setMaximumHeight(340)
        layout.addWidget(scroll)

        # Action button
        btn_confirm = QPushButton(action_label)
        btn_confirm.setFixedHeight(32)
        btn_confirm.setStyleSheet(
            f"QPushButton {{ background:{accent}; color:#fff; border:none; border-radius:5px; font-weight:bold; }}"
            f"QPushButton:hover {{ background: #9f7de8; }}"
        )
        btn_confirm.clicked.connect(self._on_confirm)
        layout.addWidget(btn_confirm)

    def _select_all(self):
        for cb in self._boxes:
            cb.setChecked(True)

    def _select_none(self):
        for cb in self._boxes:
            cb.setChecked(False)

    def _on_confirm(self):
        selected = [cb.property("_item") for cb in self._boxes if cb.isChecked()]
        self.form_submitted.emit({"selected_items": selected})
        self.setEnabled(False)
