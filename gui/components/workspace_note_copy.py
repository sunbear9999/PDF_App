"""Explicit canvas-copy refresh; canonical notes never mirror canvas edits."""
import weakref
from PySide6.QtCore import QObject
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QLabel, QTextEdit, QPushButton
from gui.components.artifact_actions import bind_artifact_action
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.dialogs.linked_note_dialog import LinkedNoteDialog


def open_workspace_note(ctx, view, node):
    """Bind refresh to this particular canvas copy, even if other copies exist."""
    manager = getattr(ctx, 'dialog_manager', None)
    if manager is None:
        return None

    def create():
        dialog = LinkedNoteDialog(ctx, node.highlight_id, view)
        dialog._workspace_copy = WorkspaceNoteCopy(ctx, dialog, view, node)
        return dialog

    return manager.show(LinkedNoteDialog, key=f'workspace_note:{id(view)}:{node.node_id}',
                        singleton=True, factory=create)


def state(node):
    return (node.highlight_id, node.note, node.color, dict(node.entity_properties or {}))


class WorkspaceNoteCopy(QObject):
    def __init__(self, app_context, dialog, view, node):
        super().__init__(dialog)
        self.dialog, self.view = dialog, weakref.ref(view)
        self.node_id, self.original = node.node_id, state(node)
        self.preview, self._armed = None, False
        layout = dialog.content_layout
        layout.addWidget(QLabel('Workspace copy — replacing it changes this card only', dialog))
        local = QTextEdit(dialog)
        local.setReadOnly(True)
        local.setPlainText(node.note)
        local.setAccessibleName('Current independent workspace commentary')
        layout.addWidget(local)
        self.button = QPushButton('Replace workspace commentary/color with the previewed project note', dialog)
        self.button.setEnabled(False)
        self.button.clicked.connect(self.refresh)
        layout.addWidget(self.button)
        bind_artifact_action(app_context, dialog, 'intelligence.note.refresh_copy', 'Refresh Workspace Note Copy', self.refresh)
        self.runner = ArtifactTaskRunner(app_context, self, service_name='reference_note_service')
        self.runner.completed.connect(self._ready)
        self.runner.failed.connect(self._failed)
        dialog.destroyed.connect(self.runner.shutdown)
        self.runner.start('note_snapshot', highlight_id=node.highlight_id)

    def refresh(self):
        if not self.preview or self.runner.busy:
            return
        self._armed = True
        self.button.setEnabled(False)
        self.runner.start('note_snapshot', highlight_id=self.original[0])

    def _ready(self, _):
        data, self.runner.result = self.runner.result, None
        if not self._armed:
            self.preview = data
            self.button.setEnabled(True)
            return
        view = self.view()
        node = view.nodes.get(self.node_id) if view is not None else None
        if node is None or state(node) != self.original or data != self.preview:
            self._failed('The project note or workspace copy changed. Close this preview and review again.')
            return
        view.save_state_for_undo()
        node.note, node.color = data['content'], data['color']
        node.entity_properties.update(note_text=node.note, artifact_ref=data['artifact_ref'],
                                      primary_evidence_id=data['annotation_id'], copied_note_revision=data['note_revision'])
        node.setBrush(QBrush(QColor(node.color)))
        node.refresh_layout()
        view._mark_workspace_dirty(autosave=True)
        self._failed('Workspace copy refreshed. The canonical note and original source were unchanged.')

    def _failed(self, message):
        self._armed = False
        self.preview = None
        self.button.setEnabled(False)
        self.dialog.status.setText(message)
