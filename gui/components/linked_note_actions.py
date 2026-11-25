"""Shared guarded actions for a project-owned note shown on any surface."""
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QPushButton, QColorDialog
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_actions import bind_artifact_action
from gui.components.dialogs.artifact_inspector_dialog import ArtifactInspectorDialog


class LinkedNoteActions(QObject):
    def __init__(self, app_context, data, parent):
        super().__init__(parent)
        self.ctx, self.data, self.owner = app_context, dict(data), parent
        self._closed = False
        self.available = True
        parent.destroyed.connect(self.shutdown)
        self.runner = ArtifactTaskRunner(app_context, self, service_name="reference_note_service")
        self.runner.failed.connect(self._failed)
        self.runner.completed.connect(self._completed)
        self.status = QLabel("Project note — source file unchanged", parent)
        self.status.setWordWrap(True)
        self.edit_button = QPushButton("Edit note…", parent)
        self.edit_button.clicked.connect(self.edit)
        registry = getattr(getattr(app_context, "help_service", None), "ui_target_registry", None)
        if registry is not None:
            identity = f"intelligence.note.actions.{id(self)}"
            registry.register_widget(identity, parent, "core.intelligence.notes")
            parent.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))
        for key, label, callback in (("inspect", "Edit Saved Linked Note", self.edit),
                                     ("delete", "Delete Linked Note", self.delete),
                                     ("color", "Choose Linked Note Color", self.choose_color)):
            bind_artifact_action(app_context, parent, f"intelligence.note.{key}", label, callback,
                                 mount="toolbar:intelligence:note")

    def edit(self):
        if self._closed or not self.available:
            return
        manager = getattr(self.ctx, "dialog_manager", None)
        if manager is None:
            return
        dialog = manager.show(ArtifactInspectorDialog, key="intelligence_expanded", singleton=True,
                              factory=lambda: ArtifactInspectorDialog(self.ctx))
        if dialog:
            dialog.load_artifact(self.data["note_id"])

    def choose_color(self):
        if self._closed or not self.available:
            return
        manager = getattr(self.ctx, "dialog_manager", None)
        if manager is None:
            return
        dialog = manager.show(QColorDialog, key=f"linked_note_color:{self.data['annotation_id']}", singleton=True,
                              factory=lambda: QColorDialog(QColor(self.data.get("color") or "#b366ff"), self.owner))
        if dialog and not dialog.property("linked_note_connected"):
            dialog.setProperty("linked_note_connected", True)
            dialog.colorSelected.connect(lambda color: self.set_color(color.name()))

    def set_color(self, color):
        if not isinstance(color, str):
            color = QColor.fromRgbF(*color[:3]).name()
        self._modify("color", color=color)

    def delete(self):
        self._modify("delete")

    def _modify(self, command, **values):
        if self._closed or not self.available or self.runner.busy:
            return
        self.status.setText("Applying linked-note change…")
        self.runner.start("modify", annotation_id=self.data["annotation_id"], command=command,
            expected_revision=self.data["revision"], expected_note_revision=self.data["note_revision"],
            expected_project=self.data["project_identity"], **values)

    def _failed(self, error):
        self.status.setText(error)

    def _completed(self, _):
        self.status.setText("Saved. Refreshing the notes list…")

    def shutdown(self, *_):
        self._closed = True
        self.runner.shutdown()
