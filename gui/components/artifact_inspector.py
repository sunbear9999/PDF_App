"""Reusable, structured inspector for intelligence artifacts."""
from __future__ import annotations

from types import SimpleNamespace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QFormLayout, QLabel, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget, QHBoxLayout, QToolButton, QMenu, QApplication, QDialog

from core.events.domains.intelligence_events import IntelligenceIntent, ReviewDecisionPayload
from core.models.producer_models import NO_MODEL, identity_of
from core.events.domains.help_events import HelpIntent, HelpPayload
from gui.components.artifact_form import ArtifactForm, field_label, show_field_help
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_actions import inspector_actions
from gui.components.action_tooltips import tooltip
from gui.components.icon_buttons import iconize
from gui.components.entity_mention_list import EntityMentionList
from gui.components.artifact_summary import caption as _caption, example_fields, readable as _readable, sentence_box, summary
from gui.components.dialogs.artifact_source_repair import ArtifactSourceRepairDialog
from gui.components.artifact_actions import bind_artifact_action
from gui.managers.dialog_manager import exec_as_modal

MAX_DETAIL_ITEMS = 64
MAX_DETAIL_DEPTH = 4
MAX_DETAIL_TEXT_CHARS = 4000


def method_text(row) -> str:
    """How this artifact was produced, from its recorded producer identity."""
    identity = identity_of(row)
    if identity is not None:
        return identity.describe()
    return ("Not recorded — produced before Papyrus recorded methods. "
            "Rerun its stage to record the method and model.")


class ArtifactInspector(QWidget):
    from PySide6.QtCore import Signal
    saved = Signal()
    """Structured artifact view; raw JSON is available only on request."""

    def __init__(self, parent=None, *, app_context=None):
        super().__init__(parent)
        self.ctx = app_context or SimpleNamespace()
        self.row, self.form, self.receipt = {}, None, None
        self._project_signal = getattr(getattr(self.ctx, "bus", None), "project_clearing_started", None)
        if self._project_signal is not None:
            self._project_signal.connect(self._project_closed)
        # Saving the project also saves an edit in progress here (the Save button still works).
        self._saved_signal = getattr(getattr(self.ctx, "bus", None), "project_saved", None)
        if self._saved_signal is not None:
            self._saved_signal.connect(self._project_saved)
        self.runner = ArtifactTaskRunner(self.ctx, self)
        self.runner.completed.connect(self._completed)
        self.runner.failed.connect(self._error)
        self.runner.busy_changed.connect(self._busy)
        self.raw_runner = ArtifactTaskRunner(self.ctx, self)
        self.raw_runner.completed.connect(self._raw_ready)
        self.raw_runner.failed.connect(self._error)
        self.destroyed.connect(self.runner.shutdown)
        self.destroyed.connect(self.raw_runner.shutdown)
        bus = getattr(self.ctx, 'bus', None)
        if bus is not None:
            bus.intelligence_state_changed.connect(self._schema_changed)
        self._raw_record, self._closing = {}, False
        self.setAccessibleName("Artifact inspector and editor")
        layout = QVBoxLayout(self)
        self._toolbar(layout)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Artifact review status")
        layout.addWidget(self.status)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)                         # the record takes the room
        self.mention_list = EntityMentionList(self.ctx, self)   # an entity's mentions; pick one to jump to it
        self.mention_list.hide()
        layout.addWidget(self.mention_list, 0)
        self.raw_toggle = QPushButton("Show raw JSON (advanced)")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.toggled.connect(self._toggle_raw)
        iconize(self.raw_toggle, "{ }", "Show raw JSON (advanced)", "Show or hide the record's stored data (advanced, read-only).")
        self._bar.insertWidget(self._bar.count() - 1, self.raw_toggle)   # beside Save / Verify / More
        self.raw = QTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setVisible(False)
        self.raw.setMaximumHeight(200)
        layout.addWidget(self.raw)
        self.clear()
        inspector_actions(self.ctx, self)
        bind_artifact_action(self.ctx,self,"intelligence.entity_review","Review Entity Grouping",self.show_entity_grouping,"toolbar:intelligence:inspector")

    def _toggle_raw(self, shown):
        self.raw.setVisible(shown)
        self.raw_toggle.setToolTip(("Hide" if shown else "Show") + " the record's stored data (advanced, read-only).")
        if shown:
            self._format_raw()

    def _format_raw(self):
        self.raw.setPlainText("Preparing the advanced record…")
        self.raw_runner.start("format_record", record=self._raw_record)

    def _raw_ready(self, _):
        self.raw.setPlainText(self.raw_runner.result or "")
        self.raw_runner.result = None

    def clear(self):
        self.show_record(None)

    def show_record(self, row, *, editor_context=None, load_context=True, force=False):
        row = dict(row or {})
        if not force and self.form and self.form.has_changes:
            self.status.setText("Save or discard your changes before switching artifacts.")
            return False
        self.row = row
        self.mention_list.set_entity(row)
        self._showing_history = False
        self._schema_generation = getattr(getattr(self.ctx, 'artifact_service', None), 'schema_generation', 0)
        self._schema_invalid = False
        self._read_only = bool((editor_context or {}).get("read_only"))
        self._strict_read_only = bool((editor_context or {}).get("strict_read_only"))
        db = getattr(getattr(self.ctx, "artifact_service", None), "db", None)
        self._project = (id(db), id(getattr(db, "_conn", None)))
        self.form = None
        self._detail_budget = MAX_DETAIL_ITEMS
        body = QWidget(self.scroll)
        layout = QVBoxLayout(body)
        if row.get("run_id"):
            self._run_body(layout, row)
        elif row and (row.get("artifact_id") or row.get("artifact_type")):
            self._record_body(layout, row, editor_context or {})
        elif row:
            self._group(layout, "Next step", list(row.items()))
        else:
            layout.addWidget(QLabel("Select an artifact to inspect or review it.", body))
        layout.addStretch()
        old = self.scroll.takeWidget()
        self.scroll.setWidget(body)
        if old:
            old.deleteLater()
        self._raw_record = row
        self.raw_runner.cancel()
        self.raw.clear()
        if self.raw_toggle.isChecked():
            self._format_raw()
        self._busy(False)
        if load_context and row.get("artifact_id") and getattr(self.ctx, "artifact_service", None):
            self.runner.start("inspect_artifact", artifact_id=row["artifact_id"])
        return True

    def _run_body(self, layout, row):
        for title, names in (("Processing run", ("stage", "status", "source_id")),
                             ("Method", ("producer_name", "producer_version", "model_name", "model_role")),
                             ("Timing", ("started_at", "finished_at", "created_at")),
                             ("Diagnostics", ("error_text",))):
            self._group(layout, title, [(name, row.get(name)) for name in names])
        self._group(layout, "Outputs", [("artifact_count", len(row.get("output_artifact_ids") or []))])
        self._group(layout, "How this was produced", [("method", method_text(row))])

    def _record_body(self, layout, row, context):
        self._group(layout, *summary(row))
        if sentence := context.get("mention_sentence"):
            layout.addWidget(sentence_box(self, sentence))
        if related := context.get("preview_context"):
            title = "Example mention — one sentence that names it" if row.get("artifact_type") == "entity" else "Representative occurrence — one example"
            self._group(layout, title, example_fields(related))
        if getattr(self.ctx, "artifact_service", None) and row.get("artifact_id") and not context.get("read_only"):
            box = QGroupBox("Your review", self)
            inner = QVBoxLayout(box)
            inner.setContentsMargins(8, 12, 8, 8)
            inner.setSpacing(6)
            self.form = ArtifactForm(self.ctx, row, context, box)
            self.form.citation_search_requested.connect(self._search_citations)
            inner.addWidget(self.form)
            layout.addWidget(box)
        details_toggle = QToolButton(self)
        details_toggle.setText("ⓘ Details")
        details_toggle.setToolTip("Details — location, method and history")
        details_toggle.setAccessibleName("Details — location, method and history")
        details_toggle.setCheckable(True)
        details_body = QWidget(self)
        details_body.hide()
        details_toggle.toggled.connect(details_body.setVisible)
        layout.addWidget(details_toggle)
        layout.addWidget(details_body)
        layout = QVBoxLayout(details_body)
        layout.setContentsMargins(0, 0, 0, 0)
        groups = (("Assessment", ("review_state", "status")),
                  ("Source location — recorded, read-only", ("source_path", "source_type", "locator", "section_path")),
                  ("Method", ("model_name", "extractor_name", "extractor_version")),
                  ("History", ("revision", "created_at", "updated_at")))
        for title, names in groups:
            fields = [(name, "No model used" if name == "model_name" and row[name] == NO_MODEL else row[name]) for name in names if name in row]
            if fields:
                self._group(layout, title, fields)
        self._group(layout, "How this was produced", [("method", method_text(row))])
        metadata = dict(row.get("metadata") or {})
        for path in self.form.controls if self.form else ():
            if path.startswith("metadata."):
                metadata.pop(path.split(".")[1], None)
        if metadata:
            self._group(layout, "Recorded details — read-only", [("metadata", metadata)])

    def show_history(self, revisions=(), proposals=(), *, snapshot=None):
        from gui.components.paged_collection import PagedCollection
        self.form = None
        self._showing_history = True
        self.history_view = PagedCollection(self.ctx, self)
        back = QPushButton('Back to current artifact', self.history_view)
        back.clicked.connect(self.reset)
        self.history_view.layout().insertWidget(0, back)
        self.history_view.pager.register_actions('intelligence.history', 'toolbar:intelligence:history')
        self.history_view.pager.requested.connect(self._load_history_page)
        self.history_view.selected.connect(self._history_selected)
        old = self.scroll.takeWidget()
        self.scroll.setWidget(self.history_view)
        if old:
            old.deleteLater()
        if snapshot is None:
            items = [{'title': f"Revision {row.get('revision', '?')}", 'preview': row.get('created_at', ''),
                      'snapshot': row.get('snapshot') or {}} for row in revisions[:25]]
            snapshot = {'items': items}
        self.history_view.apply(snapshot)
        self.raw_runner.cancel()
        self.raw.clear()
        self._busy(False)

    def _load_history_page(self):
        self.runner.start('query_artifact_history', artifact_id=self.row['artifact_id'],
                          cursor=self.history_view.pager.cursor)

    def _history_selected(self, index):
        self._detail_budget = MAX_DETAIL_ITEMS
        item = self.history_view.model.items[index]
        snapshot = item.get('snapshot') or {}
        body = QWidget(self.history_view)
        layout = QVBoxLayout(body)
        self._group(layout, item['title'], [(name, snapshot.get(name)) for name in
                    ('artifact_type', 'exact_text', 'review_state', 'metadata')])
        area = QScrollArea(self.history_view)
        area.setWidgetResizable(True)
        area.setWidget(body)
        self.history_view.set_detail(area)
        self._raw_record = item
        if self.raw_toggle.isChecked():
            self._format_raw()

    def _group(self, layout, title, fields):
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setContentsMargins(8, 12, 8, 8)
        form.setSpacing(6)
        for key, value, *caption in fields:
            form.addRow(field_label(self.ctx, key, caption[0] if caption else _caption(key), box), self._value_widget(value, key))
        layout.addWidget(box)

    def _value_widget(self, value, path="metadata", depth=0):
        if isinstance(value, (dict, list)):
            if depth >= MAX_DETAIL_DEPTH or self._detail_budget <= 0:
                return QLabel('Additional details are available in the advanced read-only record.')
            nested = QWidget()
            form = QFormLayout(nested)
            form.setContentsMargins(0, 0, 0, 0)
            entries = value.items() if isinstance(value, dict) else enumerate(value, 1)
            shown = 0
            for key, child in entries:
                if self._detail_budget <= 0:
                    break
                self._detail_budget -= 1
                shown += 1
                child_path = f"{path}.{key}"
                form.addRow(field_label(self.ctx, child_path, _caption(str(key)), nested), self._value_widget(child, child_path, depth+1))
            if shown < len(value):
                form.addRow(QLabel("More entries are available in the advanced read-only record.", nested))
            if not value:
                form.addRow(QLabel("—"))
            return nested
        text = _readable(value)
        if len(text) > MAX_DETAIL_TEXT_CHARS:
            text = text[:MAX_DETAIL_TEXT_CHARS] + '\nPreview only. Open the source or advanced record for the full text.'
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _toolbar(self, layout):
        bar = QHBoxLayout()
        self.save_button = QPushButton("Save", self)
        self.verify_button = QPushButton("Verify", self)
        self.save_button.clicked.connect(self.save)
        self.verify_button.clicked.connect(self.verify)
        bar.addWidget(self.save_button)
        bar.addWidget(self.verify_button)
        iconize(self.save_button, "💾", "Save", tooltip(self.ctx, "intelligence.save", "Save"))
        iconize(self.verify_button, "✔", "Verify", tooltip(self.ctx, "intelligence.verify", "Verify"))
        self.more_actions = {}
        more = QToolButton(self)
        more.setText("⋯")
        more.setAccessibleName("More review actions")
        more.setObjectName("iconButton")
        more.setToolTip("More review actions: undo, discard, reject, repair location, grouping and the field guide.")
        menu = QMenu(more)
        menu.setToolTipsVisible(True)
        for label, callback, identity in (
                ("Undo last review", self.undo, "intelligence.undo"), ("Discard changes", self.reset, "intelligence.reset"),
                ("Reject artifact", self.reject_artifact, "intelligence.reject_artifact"),
                ("Repair source location…", self.show_provenance, "intelligence.show_provenance"),
                ("Review entity grouping…", self.show_entity_grouping, "intelligence.entity_review"),
                ("Artifact field guide", self.show_guide, "intelligence.show_guide")):
            self.more_actions[label] = menu.addAction(label, callback)
            self.more_actions[label].setToolTip(tooltip(self.ctx, identity, label))
        more.setMenu(menu)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bar.addWidget(more)
        bar.addStretch()
        layout.addLayout(bar)
        self._bar = bar

    def _busy(self, busy):
        available = bool(self.row.get("artifact_id")) and getattr(self.ctx, "artifact_service", None) is not None and not getattr(self, '_schema_invalid', False) and not getattr(self, '_showing_history', False)
        self.save_button.setEnabled(available and not busy and not getattr(self, "_read_only", False))
        self.verify_button.setEnabled(available and not busy and not getattr(self, "_read_only", False))
        strict = getattr(self,"_strict_read_only",False)
        self.save_button.setVisible(not strict)
        self.verify_button.setVisible(not strict)
        for name,action in self.more_actions.items():
            action.setEnabled(not strict or name == "Artifact field guide")
        if self.form:
            self.form.setEnabled(not busy and not getattr(self, '_schema_invalid', False))

    def _write(self, command):
        if getattr(self, '_showing_history', False):
            self.status.setText('History is read only. Use Discard changes to return to the current artifact.')
            return
        if self._schema_invalid:
            self.status.setText('Artifact fields changed. Discard/reload the old form before saving.')
            return
        if self._strict_read_only:
            self.status.setText("This is read-only decision context. Use the decision controls.")
            return
        if self.runner.busy:
            return
        if self._read_only and command not in {"reject", "undo"}:
            self.status.setText("Resolve the source conflict before confirming this artifact, or reject an obsolete observation.")
            return
        if not self.row.get("artifact_id"):
            return
        changes = self.form.changes() if self.form else {}
        if command == "save" and not changes and not (self.form and self.form.citation_changed):
            self.status.setText("No changes to save. Use Verify to confirm the current assessment.")
            return
        self.runner.start("execute_review_command", artifact_id=self.row["artifact_id"], command=command,
                          changes=changes, expected_revision=self.row.get("revision"), receipt=self.receipt, expected_project=self._project,
                          citation_changed=bool(self.form and self.form.citation_changed),
                          expected_schema_generation=self._schema_generation,
                          citation_target=self.form.selected_citation_target if self.form else None)

    def save(self):
        self._write("save")

    def verify(self):
        self._write("verify")

    def reject_artifact(self):
        self._write("reject")

    def undo(self):
        if self.receipt:
            self._write("undo")
        else:
            self.status.setText("No review to undo in this inspector.")

    def reset(self):
        if self._strict_read_only:
            return
        self.show_record(self.row, force=True)
        self.status.setText("Unsaved changes discarded.")

    def _completed(self, operation):
        result, self.runner.result = self.runner.result, None
        if not result:
            return
        if operation == "execute_review_command":
            self.receipt = result.get("receipt")
            self.show_record(result["row"], editor_context=result.get("editor_context"), force=True, load_context=False)
            self.status.setText("Review saved. The original method and source identity are preserved.")
            self.saved.emit()
        elif operation == "get_editor_context":
            if self.form and hasattr(self.form, "citation_target"):
                selected = self.form.selected_citation_target
                label = self.form.citation_target.currentText()
                result["citation_target"] = selected
                if selected and selected not in {identity for identity, _ in result.get("citation_choices") or []}:
                    result.setdefault("citation_choices", []).append((selected, label))
                self.form.set_citation_choices(result)
        elif operation == "inspect_artifact":
            if self.form and self.form.has_changes:
                return
            if 'history_page' in result:
                self.row = result['row']
                self.show_history(snapshot=result['history_page'])
            else:
                self.show_record(result['row'], editor_context=result.get('editor_context'), load_context=False, force=True)
        elif operation == 'query_artifact_history':
            if hasattr(self, 'history_view'):
                self.history_view.apply(result)

    def _error(self, error):
        self.status.setText(error)

    def _schema_changed(self, event, payload):
        from core.events.domains.intelligence_events import IntelligenceEvent
        if event == IntelligenceEvent.ARTIFACT_SCHEMA_CHANGED and self.row.get('artifact_type') == payload.artifact_type:
            self.runner.cancel()
            self._schema_invalid = True
            if self.form:
                self.form.setEnabled(False)
            self.save_button.setEnabled(False)
            self.verify_button.setEnabled(False)
            self.status.setText('Plugin fields changed or were unloaded. Your draft is retained; discard/reload before continuing.')

    def _search_citations(self, search):
        self.runner.start("get_editor_context", row=self.row, search=search)

    def show_entity_grouping(self):
        if self._strict_read_only:
            return
        if self.row.get("artifact_type") not in {"entity","entity_mention"}:
            self.status.setText("Select an entity or entity mention to review its grouping.")
            return
        identity = self.row.get("parent_artifact_id") if self.row.get("artifact_type") == "entity_mention" else self.row.get("artifact_id")
        bus = getattr(self.ctx,"bus",None)
        if bus and identity:
            bus.intelligence_action_requested.emit(IntelligenceIntent.REVIEW_DECISION, ReviewDecisionPayload(artifact_id=identity))

    def show_guide(self):
        bus = getattr(self.ctx, "bus", None)
        if bus:
            bus.help_action_requested.emit(HelpIntent.SHOW_TOPIC, HelpPayload(topic_id="core.intelligence.artifacts"))

    def help_focused(self):
        widget = QApplication.focusWidget()
        while widget:
            path = widget.property("artifact_field_path")
            if path:
                show_field_help(self.ctx, path, widget.property("artifact_field_help_topic"))
                return
            widget = widget.parentWidget()
        self.show_guide()

    def show_raw(self):
        self.raw_toggle.setChecked(not self.raw_toggle.isChecked())

    def show_provenance(self):
        if self._strict_read_only or not self.row.get("artifact_id") or self.runner.busy:
            return
        if self.form and self.form.has_changes:
            self.status.setText("Save or discard edits before repairing the source location.")
            return
        dialog = ArtifactSourceRepairDialog(self.ctx, self)
        dialog.set_record(self.row)
        manager = getattr(self.ctx, "dialog_manager", None)
        accepted = manager.exec_modal(dialog) if manager else exec_as_modal(dialog)
        if accepted == QDialog.DialogCode.Accepted and dialog.confirmed.isChecked():
            self.runner.start("execute_review_command", artifact_id=self.row["artifact_id"], command="repair",
                              changes=dialog.changes(), provenance_confirmed=True,
                              expected_revision=self.row.get("revision"), expected_project=self._project)
        dialog.deleteLater()

    def _project_closed(self, *_):
        self.receipt = None
        self.show_record(None, load_context=False, force=True)

    def _project_saved(self, *_):
        if self.form is not None and self.form.has_changes and not self._closing:
            self.save()

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.runner.shutdown()
        self.raw_runner.shutdown()
        self.mention_list.shutdown()
        if self._project_signal is not None:
            self._project_signal.disconnect(self._project_closed)
        if self._saved_signal is not None:
            self._saved_signal.disconnect(self._project_saved)
            self._saved_signal = None
