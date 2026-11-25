"""Explicit source-location repair; machine identity is never unlocked."""
from copy import deepcopy
from gui.components.artifact_form import field_label
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QCheckBox,
                              QDialogButtonBox, QSpinBox, QDoubleSpinBox)


class ArtifactSourceRepairDialog(QDialog):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self.controls, self.row = app_context, {}, {}
        self.setWindowTitle("Repair recorded source location")
        layout = QVBoxLayout(self)
        explanation = QLabel("Check the original source first. This changes only recorded location coordinates; IDs, model identity and source text remain protected.", self)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.confirmed = QCheckBox("I verified that the current recorded location is incorrect", self)
        layout.addWidget(self.confirmed)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.confirmed.toggled.connect(self._confirm)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def set_record(self, row):
        self.row = deepcopy(row)
        for name, value in (row.get("locator") or {}).items():
            if name not in {"page", "page_num", "char_start", "char_end", "start", "end"} or not isinstance(value, (int, float)):
                continue
            control = QSpinBox(self) if isinstance(value, int) else QDoubleSpinBox(self)
            control.setRange(0, 1000000000)
            control.setValue(value)
            control.setEnabled(False)
            self.controls[name] = control
            self.form.addRow(field_label(self.ctx, f"locator.{name}", name.replace("_", " ").capitalize(), self), control)

    def _confirm(self, checked):
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(checked and bool(self.controls))
        for control in self.controls.values():
            control.setEnabled(checked)

    def changes(self):
        locator = deepcopy(self.row.get("locator") or {})
        locator.update({name: control.value() for name, control in self.controls.items()})
        return {"locator": locator}
