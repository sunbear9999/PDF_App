"""An expanded modeless surface using the same artifact editor and navigation."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton
from gui.components.artifact_inspector import ArtifactInspector
from gui.components.artifact_source_navigator import ArtifactSourceNavigator
from gui.components.artifact_actions import bind_artifact_action


class ArtifactInspectorDialog(QDialog):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self._closing = app_context, False
        self.setWindowTitle("Artifact inspector")
        self.resize(900, 780)
        layout = QVBoxLayout(self)
        self.inspector = ArtifactInspector(self, app_context=app_context)
        layout.addWidget(self.inspector, 1)
        jump = QPushButton("Jump to source", self)
        jump.clicked.connect(self.jump)
        layout.addWidget(jump)
        bind_artifact_action(app_context, self, "intelligence.expanded_jump", "Jump to Source", self.jump)
        self.navigator = ArtifactSourceNavigator(app_context, self.inspector.status.setText, self)
        self._project_signal = getattr(getattr(app_context, "bus", None), "project_clearing_started", None)
        if self._project_signal is not None:
            self._project_signal.connect(self._project_closed)

    def set_record(self, row):
        return self.inspector.show_record(row)

    def load_artifact(self, identity):
        """Do not expose an editable placeholder before its revision is loaded."""
        return self.inspector.show_record({"artifact_id": identity},
            editor_context={"read_only": True, "strict_read_only": True})

    def jump(self):
        if identity := self.inspector.row.get("artifact_id"):
            self.navigator.navigate(identity)

    def _project_closed(self, *_):
        self.close()

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            self.inspector.shutdown()
            self.navigator.shutdown()
            if self._project_signal is not None:
                self._project_signal.disconnect(self._project_closed)
        super().closeEvent(event)
