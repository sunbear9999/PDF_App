"""Shared live reference status/actions; saved quote text remains a snapshot."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

from core.models.artifact_reference import ArtifactRef
from gui.components.artifact_actions import bind_artifact_action
from gui.components.reference_hydrator import reference_hydrator
from gui.components.artifact_source_navigator import ArtifactSourceNavigator
from gui.components.dialogs.artifact_inspector_dialog import ArtifactInspectorDialog


class ArtifactReferenceWidget(QWidget):
    ready = Signal()

    def __init__(self, app_context, reference, parent=None, *, show_quote=False, allow_note=False):
        super().__init__(parent)
        self.ctx, self.reference = app_context, ArtifactRef.from_value(reference)
        self.projection, self._closed, self._loaded = {}, False, False
        self._hydration_generation = -1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.quote_label = None
        if show_quote:
            self.quote_label = self._label(self.reference.quote, 'Saved quoted passage')
            layout.addWidget(self.quote_label)
        self.source = self._label(self.reference.source_title, "Reference source")
        self.status = self._label("Checking recorded artifact…", "Artifact reference status")
        self.method = self._label("", "Artifact extraction method")
        self.location = self._label("", "Recorded source location")
        for label in (self.source, self.location, self.status, self.method):
            layout.addWidget(label)
        actions = QHBoxLayout()
        self.jump_button = QPushButton("Jump to source", self)
        self.inspect_button = QPushButton("Inspect artifact", self)
        self.jump_button.clicked.connect(self.jump)
        self.inspect_button.clicked.connect(self.inspect)
        self.note_button = QPushButton("Add linked note…", self)
        self.note_button.clicked.connect(self.add_note)
        self.note_button.setVisible(allow_note)
        self.note_button.setEnabled(False)
        actions.addWidget(self.jump_button)
        actions.addWidget(self.inspect_button)
        actions.addWidget(self.note_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.jump_button.setEnabled(False)
        self.inspect_button.setEnabled(False)
        if not self.reference.artifact_id:
            for widget in (self.source, self.location, self.method, self.jump_button, self.inspect_button, self.note_button):
                widget.hide()
        self.hydrator = reference_hydrator(app_context)
        self.hydrator.subscribe(self)
        self.navigator = ArtifactSourceNavigator(app_context, self.status.setText, self)
        self._bind_lifecycle(app_context)

    def _bind_lifecycle(self, app_context):
        registry = getattr(getattr(app_context, "help_service", None), "ui_target_registry", None)
        if registry is not None:
            identity = f"intelligence.reference.{id(self)}"
            registry.register_widget(identity, self, "core.intelligence.references")
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))
        self.setWhatsThis("Saved quotes stay unchanged. Live artifact status, recorded source location and extraction method are checked in the current project. Press F1 for the reference guide.")
        self.destroyed.connect(self.navigator.shutdown)
        for name, label in (("jump", "Jump to Referenced Source"), ("inspect", "Inspect Referenced Artifact")):
            bind_artifact_action(app_context, self, f"intelligence.reference.{name}", label, getattr(self, name),
                                 mount="toolbar:intelligence:reference")
        bind_artifact_action(app_context, self, "intelligence.reference.note", "Add Linked Note", self.add_note,
                             mount="toolbar:intelligence:reference")

    def _label(self, text, name):
        label = QLabel(text, self)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setAccessibleName(name)
        return label

    def showEvent(self, event):
        super().showEvent(event)
        if self.reference.artifact_id:
            self.hydrator.schedule()
        elif not self._loaded:
            self.refresh()

    def refresh(self):
        if self._closed:
            return
        if not self.reference.artifact_id:
            self._loaded = True
            self.status.setText("Legacy quote — no recorded artifact reference.")
            return
        self.hydrator.refresh(self)

    def set_reference(self, reference):
        self.reference = ArtifactRef.from_value(reference)
        if self.quote_label is not None:
            self.quote_label.setText(self.reference.quote)
        self._reference_dirty()
        self.hydrator.refresh(self)

    def _reference_ready(self, projection):
        if self._closed:
            return
        self.projection, self._loaded = projection, True
        self._hydration_generation = self.hydrator.generation
        self.status.setText(self.projection.get("summary", "Artifact unavailable."))
        title = self.projection.get("source_title") or self.reference.source_title
        self.source.setText(" · ".join(filter(None, (self.projection.get("presentation_kind"), title))))
        self.source.setToolTip(self.projection.get("source_path", ""))
        self.method.setText(self.projection.get("method", ""))
        from core.models.artifact_reference import location_label
        self.location.setText(location_label((self.projection.get("row") or {}).get("locator") or {}))
        self.jump_button.setEnabled(bool(self.projection.get("can_jump")))
        self.inspect_button.setEnabled(bool(self.projection.get("row")))
        self.note_button.setEnabled(bool(self.projection.get("can_jump")))
        self.ready.emit()

    def _failed(self, error):
        self.projection, self._loaded = {}, True
        self._hydration_generation = self.hydrator.generation
        self.status.setText(f"Cannot check artifact: {error}")
        self.jump_button.setEnabled(False)
        self.inspect_button.setEnabled(False)
        self.note_button.setEnabled(False)

    def _reference_dirty(self):
        if self._closed:
            return
        self._loaded = False
        self.projection = {}
        self.jump_button.setEnabled(False)
        self.inspect_button.setEnabled(False)
        self.note_button.setEnabled(False)
        if self.reference.artifact_id:
            self.status.setText("Checking recorded artifact…")

    def jump(self):
        if self._current() and self.jump_button.isEnabled():
            self.navigator.navigate(self.reference.artifact_id)

    def _current(self):
        if self.hydrator.is_current(self):
            return True
        if not self._closed:
            self._reference_dirty()
            self.hydrator.schedule()
        return False

    def inspect(self):
        row = self.projection.get("row")
        manager = getattr(self.ctx, "dialog_manager", None)
        if not self._current() or not row or manager is None:
            return
        dialog = manager.show(ArtifactInspectorDialog, key="intelligence_expanded", singleton=True,
                              factory=lambda: ArtifactInspectorDialog(self.ctx))
        if dialog:
            dialog.load_artifact(row["artifact_id"])

    def add_note(self):
        if not self._current() or not self.note_button.isEnabled():
            return
        from gui.components.dialogs.reference_note_dialog import open_reference_note
        open_reference_note(self.ctx, self.reference.to_dict(), parent=self)

    def _project_closed(self, *_):
        self._closed = True
        self.projection = {}
        self.navigator.shutdown()
        self.source.clear()
        self.method.clear()
        self.location.clear()
        self.status.setText("Project closed — saved quote retained.")
        self.jump_button.setEnabled(False)
        self.inspect_button.setEnabled(False)
        self.note_button.setEnabled(False)
