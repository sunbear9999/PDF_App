"""One modeless, guarded linked-note editor for all citation surfaces."""
import uuid
import hashlib
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton
from gui.components.artifact_reference import ArtifactReferenceWidget
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_actions import bind_artifact_action
from gui.components.dialogs.artifact_inspector_dialog import ArtifactInspectorDialog
from core.models.artifact_reference import location_label


def open_reference_note(ctx, reference, body="", *, origin=None, parent=None):
    manager = getattr(ctx, "dialog_manager", None)
    if manager is None:
        return None
    return manager.show(ReferenceNoteDialog, key=f"reference_note:{reference.get('artifact_id', '')}", singleton=True,
        factory=lambda: ReferenceNoteDialog(ctx, reference, body, origin=origin, parent=parent))


def open_selection_note(ctx, selection, body="", *, parent=None):
    manager = getattr(ctx, "dialog_manager", None)
    if manager is None:
        return None
    identity = hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()
    return manager.show(ReferenceNoteDialog, key=f"selection_note:{identity}", singleton=True,
        factory=lambda: ReferenceNoteDialog(ctx, {}, body, selection=selection, parent=parent))


class ReferenceNoteDialog(QDialog):
    def __init__(self, app_context, reference, body="", *, origin=None, selection=None, parent=None):
        super().__init__(parent)
        self.ctx, self.reference, self.origin = app_context, dict(reference), dict(origin or {})
        self.selection = dict(selection) if selection is not None else None
        self.preview, self.receipt, self._closing = {}, {}, False
        self.request_id = str(uuid.uuid4())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Adopt physical annotation" if (self.selection or {}).get('legacy_annotation') else "Save linked note")
        self.resize(720, 650)
        layout = QVBoxLayout(self)
        self._build_source(layout)
        self.span_preview = QLabel("", self)
        self.span_preview.setWordWrap(True)
        self.span_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.span_preview.setAccessibleName("Current recorded passage for this note")
        self.span_preview.hide()
        layout.addWidget(self.span_preview)
        explanation = QLabel("Saved in this project with its recorded source span. The source file stays unchanged. Your note is separate from the quotation.", self)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        label = QLabel("Your note (optional)" if self.selection is not None else "Your note / saved assistant suggestion", self)
        self.body = QTextEdit(self)
        self.body.setPlainText(body)
        self.body.setAccessibleName("Linked note content")
        label.setBuddy(self.body)
        layout.addWidget(label)
        layout.addWidget(self.body, 1)
        self.status = QLabel("Checking the source before saving…", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Linked note save status")
        layout.addWidget(self.status)
        self._build_actions(layout)
        self._register_help()
        self.runner = ArtifactTaskRunner(app_context, self, service_name="reference_note_service")
        self.runner.completed.connect(self._completed)
        self.runner.failed.connect(self._failed)
        self.runner.busy_changed.connect(self._busy)
        self.destroyed.connect(self.runner.shutdown)
        bus = getattr(app_context, "bus", None)
        if bus is not None and hasattr(bus, "project_clearing_started"):
            bus.project_clearing_started.connect(self.close)
        self.reload()

    def _build_source(self, layout):
        self.source = None
        if self.selection is None:
            self.source = ArtifactReferenceWidget(self.ctx, self.reference, self, show_quote=True)
            layout.addWidget(self.source)
            return
        self._selection_quote = QTextEdit(self)
        self._selection_quote.setReadOnly(True)
        self._selection_quote.setAcceptRichText(False)
        self._selection_quote.setPlainText(self.selection.get('quote') or '')
        self._selection_quote.setAccessibleName('Recorded source quotation, read only')
        layout.addWidget(self._selection_quote, 1)
        provenance = ('Physical annotation adoption — original authorship is unrecorded; the original stays in the PDF.'
                      if self.selection.get('legacy_annotation') else
                      'Manual selection — its recorded location will be verified before saving.')
        for text, name in ((self.selection.get('source_path') or '', 'Manually selected source'),
                           (provenance, 'Selection provenance')):
            label = QLabel(text, self)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setAccessibleName(name)
            layout.addWidget(label)

    def _register_help(self):
        self.setWhatsThis("Save project commentary with a verified source span. The source file stays unchanged. F1 explains authorship, model lineage and stale-reference handling.")
        registry = getattr(getattr(self.ctx, "help_service", None), "ui_target_registry", None)
        if registry is not None:
            identity = f"intelligence.note.dialog.{id(self)}"
            registry.register_widget(identity, self, "core.intelligence.notes")
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def _build_actions(self, layout):
        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save linked note", self)
        self.reload_button = QPushButton("Reload source preview", self)
        self.inspect_button = QPushButton("Edit saved note…", self)
        for button, method, key, label in (
            (self.save_button, self.save, "save", "Save Linked Note"),
            (self.reload_button, self.reload, "reload", "Reload Linked Note Source"),
            (self.inspect_button, self.inspect, "inspect", "Edit Saved Linked Note")):
            button.clicked.connect(method)
            bind_artifact_action(self.ctx, self, f"intelligence.note.{key}", label, method, mount="toolbar:intelligence:note")
            buttons.addWidget(button)
        self.inspect_button.setEnabled(False)
        self.save_button.setEnabled(False)
        layout.addLayout(buttons)

    def reload(self):
        if self._closing or self.receipt or getattr(self, "runner", None) is None or self.runner.busy:
            return
        self.preview = {}
        operation = "prepare_selection" if self.selection is not None else "prepare"
        arguments = {"selection": self.selection} if self.selection is not None else {"reference": self.reference}
        if not self.runner.start(operation, **arguments):
            self._failed("The linked-note service is unavailable.")

    def save(self):
        if self._closing or not self.preview or self.receipt or self.runner.busy:
            return
        self.status.setText("Saving to this project. Closing this window does not undo an accepted save.")
        self.runner.start("save", reference=self.reference, body=self.body.toPlainText(), origin=self.origin, selection=self.selection,
            expected_token=self.preview["token"], expected_project=self.preview["project_identity"], request_id=self.request_id)

    def _completed(self, operation):
        result = self.runner.result or {}
        if operation in {"prepare", "prepare_selection"}:
            self.preview = result
            if self.selection is not None:
                self._selection_quote.setPlainText(result['reference']['quote'])
            current_text = "\n".join(span.get("quote", "") for span in result["anchor"]["spans"])
            self.span_preview.setText("Current recorded passage to link:\n" + current_text)
            self.span_preview.setVisible(current_text.strip() != result["reference"]["quote"].strip())
            span = result['anchor']['spans'][0]
            location = location_label({'page': span.get('page_index'), 'start': span.get('time_start')})
            self.status.setText(result["summary"] + "\n" + location + "\n" + result["anchor"].get("reason", "") + "\nReady to save this recorded span.")
        elif operation == "save":
            self.receipt = result
            self.body.setReadOnly(True)
            self.status.setText("This annotation was already adopted; its existing project note was preserved. Use Edit saved note to revise it."
                                if result.get('reused') else "Linked note saved in this project. Use Edit saved note to revise it in the shared inspector.")
        self._busy(self.runner.busy)

    def _failed(self, error):
        self.preview = {}
        self.status.setText(error)
        self.save_button.setEnabled(False)

    def _busy(self, busy):
        self.save_button.setEnabled(bool(self.preview) and not busy and not self.receipt)
        self.reload_button.setEnabled(not busy and not self.receipt)
        self.inspect_button.setEnabled(bool(self.receipt) and not busy)
        self.body.setEnabled(not busy)

    def inspect(self):
        manager = getattr(self.ctx, "dialog_manager", None)
        if not manager or not self.receipt or self._closing:
            return
        dialog = manager.show(ArtifactInspectorDialog, key="intelligence_expanded", singleton=True,
            factory=lambda: ArtifactInspectorDialog(self.ctx))
        if dialog:
            dialog.load_artifact(self.receipt["note_id"])

    def closeEvent(self, event):
        self._closing = True
        self.runner.shutdown()
        if self.source:
            self.source._project_closed()
        super().closeEvent(event)
