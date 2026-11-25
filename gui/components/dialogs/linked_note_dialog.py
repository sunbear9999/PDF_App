"""Inspect a project note from any surface using the existing guarded actions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton, QHBoxLayout

from gui.components.artifact_actions import bind_artifact_action
from gui.components.artifact_reference import ArtifactReferenceWidget
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.linked_note_actions import LinkedNoteActions


def open_linked_note(ctx, highlight_id, *, parent=None):
    manager = getattr(ctx, 'dialog_manager', None)
    if manager is None:
        return None
    return manager.show(LinkedNoteDialog, key=f'linked_note:{highlight_id}', singleton=True,
                        factory=lambda: LinkedNoteDialog(ctx, highlight_id, parent))


def open_linked_notes(ctx, highlight_ids, *, parent=None):
    identities = list(dict.fromkeys(str(value) for value in highlight_ids if str(value).startswith('LinkedNote|')))
    if not identities:
        return None
    dialog = open_linked_note(ctx, identities[0], parent=parent)
    if dialog:
        dialog.set_selection(identities)
    return dialog


class LinkedNoteDialog(QDialog):
    def __init__(self, app_context, highlight_id, parent=None):
        super().__init__(parent)
        self.ctx, self.highlight_id = app_context, highlight_id
        self.note_actions, self.reference, self._closing = None, None, False
        self._snapshot_current = False
        self.selection, self.selection_index = [highlight_id], 0
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('Manage linked project note')
        self.resize(760, 720)
        self.content_layout = QVBoxLayout(self)
        self.status = QLabel('Loading the current note…', self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setAccessibleName('Project note management status')
        self.content_layout.addWidget(self.status)
        self.body = QTextEdit(self)
        self.body.setReadOnly(True)
        self.body.setAccessibleName('Saved commentary preview')
        label = QLabel('Saved commentary', self)
        label.setBuddy(self.body)
        self.content_layout.addWidget(label)
        self.content_layout.addWidget(self.body, 1)
        self.reload_button = QPushButton('Reload note', self)
        self.reload_button.clicked.connect(self.reload)
        self.content_layout.addWidget(self.reload_button)
        navigation = QHBoxLayout()
        self.previous_note = QPushButton('Previous selected note', self)
        self.next_note = QPushButton('Next selected note', self)
        self.previous_note.clicked.connect(self.previous_selected)
        self.next_note.clicked.connect(self.next_selected)
        navigation.addWidget(self.previous_note)
        navigation.addWidget(self.next_note)
        self.content_layout.addLayout(navigation)
        for key, label, callback in (('previous_selected', 'Previous Selected Note', self.previous_selected),
                                     ('next_selected', 'Next Selected Note', self.next_selected)):
            bind_artifact_action(app_context, self, 'intelligence.note.'+key, label, callback)
        bind_artifact_action(app_context, self, 'intelligence.note.reload', 'Reload Linked Note Source', self.reload)
        self.runner = ArtifactTaskRunner(app_context, self, service_name='reference_note_service')
        self.runner.completed.connect(self._loaded)
        self.runner.failed.connect(self._failed)
        self.runner.busy_changed.connect(self._busy)
        self.destroyed.connect(self.runner.shutdown)
        self._register_help()
        bus = getattr(app_context, 'bus', None)
        if bus is not None:
            bus.project_clearing_started.connect(self.close)
        self.reload()

    def _register_help(self):
        self.setWhatsThis('Manage project notes through guarded current revisions. F1 explains source spans, commentary, editing and deletion.')
        registry = getattr(getattr(self.ctx, 'help_service', None), 'ui_target_registry', None)
        if registry is not None:
            identity = f'intelligence.note.manager.{id(self)}'
            registry.register_widget(identity, self, 'core.intelligence.notes')
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def set_selection(self, identities):
        self.selection = list(identities)
        self.selection_index = self.selection.index(self.highlight_id)
        self._busy(self.runner.busy)

    def previous_selected(self):
        self._select_note(-1)

    def next_selected(self):
        self._select_note(1)

    def _select_note(self, direction):
        index = self.selection_index + direction
        if self.runner.busy or (self.note_actions and self.note_actions.runner.busy) or not 0 <= index < len(self.selection):
            return
        self.selection_index = index
        self.highlight_id = self.selection[index]
        self.reload()

    def reload(self):
        if self._closing or self.runner.busy or (self.note_actions and self.note_actions.runner.busy):
            return
        self._snapshot_current = False
        if not self.runner.start('note_snapshot', highlight_id=self.highlight_id):
            self.status.setText('Open a project with linked-note support first.')

    def _loaded(self, _):
        if self._closing:
            return
        data, self.runner.result = self.runner.result, None
        if self.note_actions is None:
            self.reference = ArtifactReferenceWidget(self.ctx, data['artifact_ref'], self, show_quote=True)
            self.content_layout.insertWidget(0, self.reference)
            self.note_actions = LinkedNoteActions(self.ctx, data, self)
            self.content_layout.addWidget(self.note_actions.status)
            buttons = QHBoxLayout()
            self.color_button = QPushButton('Change highlight color…', self)
            self.delete_button = QPushButton('Delete project note', self)
            self.color_button.clicked.connect(self.note_actions.choose_color)
            self.delete_button.clicked.connect(self.note_actions.delete)
            for button in (self.note_actions.edit_button, self.color_button, self.delete_button):
                buttons.addWidget(button)
            self.content_layout.addLayout(buttons)
            self.note_actions.runner.completed.connect(self._modified)
            self.note_actions.runner.busy_changed.connect(self._busy)
        self.note_actions.data = data
        if self.reference.reference.artifact_id != data['artifact_ref']['artifact_id']:
            self.reference.set_reference(data['artifact_ref'])
        self._snapshot_current = True
        self.body.setPlainText(data['content'])
        self.status.setText('Edit uses the shared artifact inspector. Deleting retires this project note and removes its overlay. Workspace cards keep their own text and can be removed separately.')

    def _busy(self, busy):
        self.reload_button.setEnabled(not busy)
        self.previous_note.setEnabled(not busy and self.selection_index > 0)
        self.next_note.setEnabled(not busy and self.selection_index+1 < len(self.selection))
        if self.note_actions:
            self.note_actions.available = not busy and self._snapshot_current
            for button in (self.note_actions.edit_button, self.color_button, self.delete_button):
                button.setEnabled(self.note_actions.available)

    def _failed(self, error):
        self._snapshot_current = False
        if self.note_actions:
            self.note_actions.available = False
        self.status.setText(error)

    def _modified(self, _):
        self._snapshot_current = False
        self.note_actions.available = False
        self.status.setText('Change saved. Reload to read the current note; deleted notes cannot be reloaded.')
        for button in (self.note_actions.edit_button, self.color_button, self.delete_button):
            button.setEnabled(False)

    def closeEvent(self, event):
        self._closing = True
        self.runner.shutdown()
        if self.note_actions:
            self.note_actions.shutdown()
        super().closeEvent(event)
