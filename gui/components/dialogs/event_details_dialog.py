"""One typed source-backed event editor for new interpretations and candidate review."""
import uuid
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QTextEdit,
                              QComboBox, QLabel, QPushButton, QTableWidget, QTableWidgetItem)
from core.models.artifact_reference import ArtifactRef
from core.services.intelligence.event_authoring import PARTICIPANT_ROLES, EVENT_STATES, validate
from gui.components.artifact_reference import ArtifactReferenceWidget
from gui.components.artifact_actions import bind_artifact_action
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_form import field_label


def open_event_details(ctx, row, *, create=False, parent=None):
    manager = getattr(ctx, 'dialog_manager', None)
    if manager is None:
        return None
    return manager.show(EventDetailsDialog, key=f"event:{create}:{row['artifact_id']}", singleton=True,
        factory=lambda: EventDetailsDialog(ctx, parent).configure(row, create=create))


class EventDetailsDialog(QDialog):
    saved = Signal()

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self.snapshot, self.row = app_context, {}, {}
        self.create, self.request_id = False, str(uuid.uuid4())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('Event interpretation and participants')
        self.resize(760, 760)
        layout = QVBoxLayout(self)
        self.source_layout = QVBoxLayout(); layout.addLayout(self.source_layout)
        form = QFormLayout()
        self.summary = QTextEdit(self); self.summary.setAcceptRichText(False)
        self.date, self.date_end = QLineEdit(self), QLineEdit(self)
        self.certainty, self.decision = QComboBox(self), QComboBox(self)
        self.certainty.addItems(['unknown', 'ambiguous', 'likely', 'confirmed'])
        self.decision.addItems(EVENT_STATES)
        for path,label,widget in (('event_summary','Authored event summary',self.summary),
            ('normalized_date','Start (YYYY / YYYY-MM / YYYY-MM-DD)',self.date),('event_date_end','End (optional calendar interval)',self.date_end),
            ('certainty','Date certainty',self.certainty),('event_decision','Candidate decision',self.decision)):
            form.addRow(field_label(app_context,'metadata.'+path,label,self),widget)
            widget.setAccessibleName(label)
        layout.addLayout(form)
        self.roles = QTableWidget(0, 2, self)
        layout.addWidget(field_label(app_context,'metadata.participant_roles','Recorded entities and your assigned roles',self))
        self.roles.setHorizontalHeaderLabels(['Recorded co-mention', 'User-assigned role'])
        self.roles.setAccessibleName('Participant roles; assigning a role is your interpretation')
        layout.addWidget(self.roles, 1)
        self.status = QLabel('Loading source-backed event…', self)
        self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.save_button = QPushButton('Save event', self); layout.addWidget(self.save_button)
        self.save_button.clicked.connect(self.save)
        self.runner = ArtifactTaskRunner(app_context, self, service_name='reference_note_service')
        self.runner.completed.connect(self._ready); self.runner.failed.connect(self.status.setText)
        self.runner.busy_changed.connect(lambda busy: self.save_button.setEnabled(not busy and bool(self.snapshot)))
        self.destroyed.connect(self.runner.shutdown)
        bind_artifact_action(app_context, self, 'intelligence.event.save', 'Save Event Interpretation', self.save)
        bus = getattr(app_context, 'bus', None)
        if bus is not None:
            bus.project_clearing_started.connect(self.close)
        self._help()

    def configure(self, row, *, create=False):
        self.row, self.create = row, create
        self.reference = ArtifactRef.from_value(row).to_dict()
        self.source_layout.addWidget(ArtifactReferenceWidget(self.ctx, self.reference, self, show_quote=True))
        meta = row.get('metadata') or {}
        self.summary.setPlainText(meta.get('event_summary') or row.get('text') or row.get('exact_text') or '')
        self.date.setText(meta.get('normalized_date') or row.get('normalized_text') or '')
        self.date_end.setText(meta.get('event_date_end') or '')
        self.certainty.setCurrentText(meta.get('certainty') or 'unknown')
        self.decision.setCurrentText(meta.get('event_decision') or 'candidate')
        self.runner.start('prepare', reference=self.reference) if create else self.runner.start('event_snapshot', artifact_id=row['artifact_id'])
        return self

    def _ready(self, operation):
        result, self.runner.result = self.runner.result or {}, None
        if operation in {'save_event', 'update_event'}:
            self.saved.emit(); self.accept(); return
        self.snapshot = result
        people = result.get('participants') or []
        self.roles.setRowCount(len(people))
        current_roles = (result.get('row') or {}).get('metadata', {}).get('participant_roles', {})
        for index, person in enumerate(people):
            item = QTableWidgetItem(person['name']); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setData(Qt.ItemDataRole.UserRole, person['entity_id']); self.roles.setItem(index, 0, item)
            choice = QComboBox(self.roles); choice.addItems(PARTICIPANT_ROLES)
            choice.setAccessibleName('Role of ' + person['name'])
            choice.setCurrentText(current_roles.get(person['entity_id'], 'co-mentioned'))
            self.roles.setCellWidget(index, 1, choice)
        self.status.setText('The source quotation stays unchanged. The summary, interval and roles are your interpretation.'
            + (' Create first, then reopen to assign recorded participant roles.' if self.create else '')
            + (' Dense context: only the first 32 current co-mentions are editable.' if result.get('participants_truncated') else ''))
        self.save_button.setEnabled(True)

    def save(self):
        if self.runner.busy or not self.snapshot:
            return
        values = {'summary': self.summary.toPlainText(), 'date': self.date.text().strip(),
            'date_end': self.date_end.text().strip(), 'certainty': self.certainty.currentText(),
            'decision': self.decision.currentText(), 'roles': {self.roles.item(i, 0).data(Qt.ItemDataRole.UserRole):
                self.roles.cellWidget(i, 1).currentText() for i in range(self.roles.rowCount())}}
        try:
            validate(values)
        except ValueError as exc:
            self.status.setText(str(exc)); return
        guard = {'expected_project': self.snapshot['project_identity']}
        if self.create:
            self.runner.start('save_event', reference=self.reference, values=values, expected_token=self.snapshot['token'], request_id=self.request_id, **guard)
        else:
            self.runner.start('update_event', artifact_id=self.row['artifact_id'], values=values, expected_revision=self.snapshot['row']['revision'], **guard)

    def _help(self):
        self.setWhatsThis('F1: event interpretations, calendar intervals, candidate decisions and explicit participant roles. Source evidence remains read only.')
        registry = getattr(getattr(self.ctx, 'help_service', None), 'ui_target_registry', None)
        if registry:
            identity = 'intelligence.event.editor.' + str(id(self))
            registry.register_widget(identity, self, 'core.intelligence.events')
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def closeEvent(self, event):
        self.runner.shutdown()
        super().closeEvent(event)
