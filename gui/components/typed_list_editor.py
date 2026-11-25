"""Reusable typed editor for nested artifact list values."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLineEdit,
    QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)


def _blank_like(value):
    if isinstance(value, dict):
        return {key: _blank_like(child) for key, child in value.items()}
    if isinstance(value, list):
        return []
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return type(value)(0)
    return ""


class TypedListEditor(QWidget):
    """Rows are explicit widgets, including for lists of nested dictionaries."""

    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.template = values[0] if values else ""
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout = QVBoxLayout()
        layout.addLayout(self.rows_layout)
        actions = QHBoxLayout()
        add = QPushButton("Add item")
        add.clicked.connect(lambda: self._append(_blank_like(self.template)))
        actions.addWidget(add)
        actions.addStretch()
        layout.addLayout(actions)
        for value in values:
            self._append(value)

    def _append(self, value):
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        editor = _TypedValue(value)
        layout.addWidget(editor, 1)
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: self._remove(wrapper))
        layout.addWidget(remove)
        self.rows_layout.addWidget(wrapper)
        self.rows.append((wrapper, editor))

    def _remove(self, wrapper):
        self.rows = [(row, editor) for row, editor in self.rows if row is not wrapper]
        self.rows_layout.removeWidget(wrapper)
        wrapper.deleteLater()

    def value(self):
        return [editor.value() for _, editor in self.rows]


class _TypedValue(QWidget):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.kind = type(value)
        if isinstance(value, dict):
            self.kind = dict
            self.children = {}
            form = QFormLayout()
            for key, child in value.items():
                widget = _TypedValue(child)
                form.addRow(key.replace("_", " ").capitalize(), widget)
                self.children[key] = widget
            layout.addLayout(form)
        elif isinstance(value, list):
            self.kind = list
            self.editor = TypedListEditor(value)
            layout.addWidget(self.editor)
        elif isinstance(value, bool):
            self.editor = QCheckBox()
            self.editor.setChecked(value)
            layout.addWidget(self.editor)
        elif isinstance(value, int):
            self.editor = QSpinBox()
            self.editor.setRange(-1000000000, 1000000000)
            self.editor.setValue(value)
            layout.addWidget(self.editor)
        elif isinstance(value, float):
            self.editor = QDoubleSpinBox()
            self.editor.setDecimals(5)
            self.editor.setRange(-1e12, 1e12)
            self.editor.setValue(value)
            layout.addWidget(self.editor)
        else:
            self.kind = str
            self.editor = QTextEdit() if len(str(value or "")) > 120 else QLineEdit()
            if isinstance(self.editor, QTextEdit):
                self.editor.setPlainText(str(value or ""))
                self.editor.setMaximumHeight(70)
            else:
                self.editor.setText(str(value or ""))
            layout.addWidget(self.editor)

    def value(self):
        if self.kind is dict:
            return {key: editor.value() for key, editor in self.children.items()}
        if self.kind is list:
            return self.editor.value()
        if self.kind is bool:
            return self.editor.isChecked()
        if self.kind in (int, float):
            return self.editor.value()
        return (self.editor.toPlainText() if isinstance(self.editor, QTextEdit)
                else self.editor.text()).strip()
