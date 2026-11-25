"""Compatibility dialog using the same semantic form as the inline inspector."""
from types import SimpleNamespace

from PySide6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox, QComboBox, QLineEdit, QLabel
from gui.components.artifact_form import ArtifactForm
from core.services.intelligence.artifact_review_commands import validate_semantic_changes


class ArtifactEditorDialog(QDialog):
    """Historic callers remain supported; the dock uses inline editing."""
    def __init__(self, row, *, extra_types=(), citation_choices=(), citation_target=None,
                 source_choices=(), entity_types=(), parent=None):
        super().__init__(parent)
        self.row = row
        self.setWindowTitle("Review artifact")
        self.resize(670, 640)
        layout = QVBoxLayout(self)
        self.message = QLabel("Identity and provenance are read-only; use the inspector's source repair action when needed.", self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.form = ArtifactForm(SimpleNamespace(), row, {"citation_choices": citation_choices, "citation_target": citation_target}, self)
        self.controls = dict(self.form.controls)
        type_control = QComboBox(self)
        type_control.addItem(row.get("artifact_type") or "", row.get("artifact_type"))
        type_control.setEnabled(False)
        self.controls["artifact_type"] = type_control
        layout.addWidget(type_control)
        layout.addWidget(self.form)
        for key, value in (row.get("locator") or {}).items():
            field = QLineEdit("" if value is None else str(value), self)
            field.setEnabled(False)
            self.controls[f"locator.{key}"] = field
            self.form.form.addRow(key.replace("_", " "), field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def changes(self):
        return self.form.changes()

    @property
    def provenance_confirmed(self):
        return False

    @property
    def selected_citation_target(self):
        return self.form.selected_citation_target

    @property
    def citation_changed(self):
        return self.form.citation_changed

    def _validate_and_accept(self):
        try:
            validate_semantic_changes(self.row, self.changes())
        except (ValueError, TypeError) as exc:
            self.message.setText(str(exc))
            return
        self.accept()
