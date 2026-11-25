"""Reusable semantic controls backed by the core field schema."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QFormLayout, QHBoxLayout, QLabel, QPushButton,
                              QComboBox, QLineEdit, QTextEdit, QCheckBox)

from core.models.artifact_fields import semantic_fields, field_value
from core.events.domains.help_events import HelpIntent, HelpPayload
from gui.components.nullable_confidence_editor import NullableConfidenceEditor
from gui.components.typed_list_editor import TypedListEditor


HELP_LINK_SIZE = 12
# palette(link) follows the theme accent; the dotted focus underline keeps keyboard focus visible.
HELP_LINK_STYLE = ("QPushButton#fieldHelpLink { border: none; background: transparent; padding: 0; margin: 0;"
                   " color: palette(link); font-size: 9px; font-weight: bold; }"
                   "QPushButton#fieldHelpLink:hover { text-decoration: underline; }"
                   "QPushButton#fieldHelpLink:focus { border-bottom: 1px dotted palette(link); }")


def help_topic(path):
    leaf = path.split(".")[-1]
    return "core.intelligence.field." + leaf


def show_field_help(ctx, path, topic_id=None):
    bus = getattr(ctx, "bus", None)
    if bus is not None:
        registry = getattr(getattr(ctx, "help_service", None), "help_registry", None)
        topic = topic_id or help_topic(path)
        if registry is not None and registry.get(topic) is None:
            topic = "core.intelligence.field.metadata"
        bus.help_action_requested.emit(HelpIntent.SHOW_TOPIC, HelpPayload(topic_id=topic))


def field_label(ctx, path, title, parent, topic_id=None):
    """Caption followed by a small raised "?" link (like a footnote mark) that opens the field guide.
    It stays a focusable button so keyboard and screen-reader users can reach it."""
    widget = QWidget(parent)
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    label = QLabel(title, widget)
    label.setWordWrap(True)
    button = QPushButton("?", widget)
    button.setObjectName("fieldHelpLink")
    button.setFlat(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(HELP_LINK_STYLE)
    button.setFixedSize(HELP_LINK_SIZE, HELP_LINK_SIZE)
    button.setAccessibleName(f"Explain {title}")
    button.setToolTip(f"What is “{title}”? Opens the field guide.")
    button.clicked.connect(lambda: show_field_help(ctx, path, topic_id))
    layout.addWidget(label, 0)
    layout.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
    layout.addStretch(1)
    return widget


class ArtifactForm(QWidget):
    citation_search_requested = Signal(str)

    def __init__(self, app_context, row, editor_context=None, parent=None):
        super().__init__(parent)
        self.ctx, self.row = app_context, dict(row)
        service = getattr(app_context, "artifact_service", None)
        self.controls = {}
        self.specs = service.get_review_fields(row) if service else semantic_fields(row)
        self._original_target = (editor_context or {}).get("citation_target")
        self.form = QFormLayout(self)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for spec in self.specs:
            control = self._control(spec, field_value(row, spec))
            control.setAccessibleName(spec.label)
            control.setProperty("artifact_field_path", spec.path)
            control.setProperty("artifact_field_help_topic", spec.help_topic)
            control.setWhatsThis(spec.description or f"{spec.label}. See the Intelligence field guide for meaning and allowed values.")
            self.controls[spec.path] = control
            self.form.addRow(field_label(app_context, spec.path, spec.label, self, spec.help_topic), control)
        if row.get("artifact_type") in {"in_text_citation", "entity"}:
            self._citation_controls(editor_context or {}, row.get("artifact_type") == "entity")
        self._register_help_targets()

    def _control(self, spec, value):
        if spec.kind == "choice":
            widget = QComboBox(self)
            labels = dict(getattr(spec, "choice_labels", ()) or ())
            for choice in spec.choices:
                widget.addItem(labels.get(choice, str(choice)), choice)
            if value not in spec.choices:
                widget.addItem(f"Legacy: {value}", value)
            widget.setCurrentIndex(max(0, widget.findData(value)))
            return widget
        if spec.kind == "confidence":
            return NullableConfidenceEditor(value, self)
        if spec.kind == "list":
            return TypedListEditor(value or [], self)
        if spec.kind == "boolean":
            widget = QCheckBox(self)
            widget.setChecked(bool(value))
            return widget
        if spec.kind == "multiline":
            widget = QTextEdit(self)
            widget.setPlainText(str(value or ""))
            return widget
        widget = QLineEdit(self)
        widget.setText(str(value or ""))
        return widget

    def _citation_controls(self, context, entity=False):
        self.citation_target = QComboBox(self)
        self._unmatched = "Not a citation — only a name" if entity else "Unmatched — no suitable entry"
        self.set_citation_choices(context)
        if entity:
            self.citation_target.setToolTip("Choose a bibliography entry when mentions of this name cite that source "
                                            "(e.g. “LCN” for the LCN collection). The entity keeps its type as well.")
        title = "Also cites (bibliography entry)" if entity else "Bibliography match"
        self.form.addRow(field_label(self.ctx, "bibliography_match", title, self), self.citation_target)
        self.citation_search = QLineEdit(self)
        self.citation_search.setPlaceholderText("Find a bibliography entry…" if entity else "Find another entry in this source…")
        self.citation_search.setAccessibleName("Search bibliography entries")
        self.citation_search.returnPressed.connect(lambda: self.citation_search_requested.emit(self.citation_search.text()))
        self.form.addRow("Search entries (Enter)", self.citation_search)

    def set_citation_choices(self, context):
        combo = self.citation_target
        combo.clear()
        combo.addItem(getattr(self, "_unmatched", "Unmatched — no suitable entry"), None)
        for identity, label in context.get("citation_choices") or []:
            combo.addItem(label, identity)
        combo.setCurrentIndex(max(0, combo.findData(context.get("citation_target"))))

    @staticmethod
    def value(control):
        if isinstance(control, QComboBox):
            return control.currentData()
        if isinstance(control, QCheckBox):
            return control.isChecked()
        if isinstance(control, QTextEdit):
            return control.toPlainText()
        if hasattr(control, "value"):
            return control.value()
        return control.text().strip()

    def changes(self):
        changes = {}
        for spec in self.specs:
            value = self.value(self.controls[spec.path])
            if value == field_value(self.row, spec):
                continue
            if spec.path.startswith("metadata."):
                changes.setdefault("metadata", {})[spec.path.split(".")[1]] = value
            else:
                changes[spec.path] = value
        return changes

    @property
    def has_changes(self):
        return bool(self.changes()) or self.citation_changed

    @property
    def citation_changed(self):
        return hasattr(self, "citation_target") and self.selected_citation_target != self._original_target

    @property
    def selected_citation_target(self):
        return self.citation_target.currentData() if hasattr(self, "citation_target") else None

    def _register_help_targets(self):
        registry = getattr(getattr(self.ctx, "help_service", None), "ui_target_registry", None)
        if registry:
            for path, control in self.controls.items():
                identity = f"intelligence.field.{path}.{id(control)}"
                spec = next(spec for spec in self.specs if spec.path == path)
                registry.register_widget(identity, control, spec.help_topic or help_topic(path))
                control.destroyed.connect(lambda *_, r=registry, target=identity: r.unregister(target))
