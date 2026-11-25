"""Previewable deterministic bulk tagging rules."""
from __future__ import annotations

import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QListWidget, QPushButton, QVBoxLayout,
)

from core.events.domains.metadata_events import TagEvent, TagEventPayload, TagIntent, TagPayload
from core.events.event_bus import EventBus
from core.services.content.tag_rules import TAG_RULE_FIELDS, TAG_RULE_OPERATORS


class AutoTagDialog(QDialog):
    def __init__(self, tags, selected_tag_id=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.bus = EventBus.get_instance()
        self.request_id = str(uuid.uuid4())
        self.setWindowTitle("Auto-tag Sources")
        self.setMinimumSize(560, 480)
        self._build_ui(tags, selected_tag_id)
        self.bus.tag_data_updated.connect(self._handle_result)

    def _build_ui(self, tags, selected_tag_id):
        root = QVBoxLayout(self)
        intro = QLabel("Preview a deterministic rule, then apply its tag to every matching source.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        form = QFormLayout()
        self.tag = QComboBox()
        for item in tags:
            self.tag.addItem(item.get("name", ""), item.get("id"))
        selected = self.tag.findData(selected_tag_id)
        if selected >= 0:
            self.tag.setCurrentIndex(selected)
        form.addRow("Assign tag", self.tag)
        self.field = QComboBox()
        for value, label in TAG_RULE_FIELDS:
            self.field.addItem(label, value)
        form.addRow("Source field", self.field)
        self.operator = QComboBox()
        for value, label in TAG_RULE_OPERATORS:
            self.operator.addItem(label, value)
        form.addRow("Rule", self.operator)
        self.value = QLineEdit()
        self.value.setPlaceholderText("For example: law")
        form.addRow("Value", self.value)
        root.addLayout(form)
        self.case_sensitive = QCheckBox("Case sensitive")
        self.untagged_only = QCheckBox("Only sources that currently have no tags")
        root.addWidget(self.case_sensitive)
        root.addWidget(self.untagged_only)
        self.preview_button = QPushButton("Preview matches")
        self.preview_button.clicked.connect(self.preview_rule)
        root.addWidget(self.preview_button)
        self.summary = QLabel("No preview yet.")
        root.addWidget(self.summary)
        self.matches = QListWidget()
        root.addWidget(self.matches, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.addButton("Apply tag", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_rule)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        for widget in (self.tag, self.field, self.operator, self.value, self.case_sensitive, self.untagged_only):
            signal = widget.textChanged if widget is self.value else (
                widget.toggled if isinstance(widget, QCheckBox) else widget.currentIndexChanged
            )
            signal.connect(self._invalidate_preview)

    def _rule(self):
        return {
            "field": self.field.currentData(),
            "operator": self.operator.currentData(),
            "value": self.value.text().strip(),
            "case_sensitive": self.case_sensitive.isChecked(),
            "untagged_only": self.untagged_only.isChecked(),
        }

    def preview_rule(self):
        self._send(TagIntent.PREVIEW_RULE)

    def apply_rule(self):
        self.apply_button.setEnabled(False)
        self._send(TagIntent.APPLY_RULE)

    def _send(self, intent):
        self.summary.setText("Checking sources…")
        self.preview_button.setEnabled(False)
        self.bus.tag_action_requested.emit(
            intent,
            TagPayload(
                tag_id=self.tag.currentData(), request_id=self.request_id, rule=self._rule(),
            ),
        )

    def _handle_result(self, event: TagEvent, payload: TagEventPayload):
        if payload.get("request_id") != self.request_id:
            return
        if event not in {TagEvent.RULE_PREVIEW, TagEvent.RULE_APPLIED}:
            return
        self.preview_button.setEnabled(True)
        error = payload.get("error")
        if error:
            self.summary.setText(f"Rule error: {error}")
            return
        count = payload.get("matched_count", 0)
        self._show_matches(payload.get("matched_sources", []), count)
        if event == TagEvent.RULE_APPLIED:
            changed = payload.get("changed_count", 0)
            self.summary.setText(f"Tagged {changed} new matching source(s); {count} matched.")
        else:
            self.apply_button.setEnabled(count > 0 and self.tag.currentData() is not None)

    def _show_matches(self, sources, count):
        self.matches.clear()
        for source in sources:
            self.matches.addItem(source.get("name") or source.get("path") or "Unknown source")
        suffix = " Showing the first 200." if count > len(sources) else ""
        self.summary.setText(f"{count} source(s) match.{suffix}")

    def _invalidate_preview(self, *_):
        self.apply_button.setEnabled(False)
        self.summary.setText("Rule changed — preview again before applying.")

    def closeEvent(self, event):
        try:
            self.bus.tag_data_updated.disconnect(self._handle_result)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)
