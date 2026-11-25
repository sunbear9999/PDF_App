"""Confidence editor that preserves unknown values without invalid sentinels."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QHBoxLayout, QWidget


class NullableConfidenceEditor(QWidget):
    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        self._original = value
        self._dirty = False
        legacy = value is not None and not 0 <= value <= 1
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = f"Replace legacy score ({value})" if legacy else "Set confidence"
        self.known = QCheckBox(label)
        self.known.setChecked(value is not None and not legacy)
        layout.addWidget(self.known)
        self.number = QDoubleSpinBox()
        self.number.setDecimals(4)
        self.number.setRange(0, 1)
        self.number.setSingleStep(0.05)
        self.number.setValue(value if value is not None and not legacy else 0)
        self.number.setEnabled(value is not None and not legacy)
        self.known.toggled.connect(self.number.setEnabled)
        self.known.toggled.connect(self._mark_dirty)
        self.number.valueChanged.connect(self._mark_dirty)
        layout.addWidget(self.number)

    def _mark_dirty(self, *_):
        self._dirty = True

    def value(self):
        if not self._dirty:
            return self._original
        return self.number.value() if self.known.isChecked() else None
