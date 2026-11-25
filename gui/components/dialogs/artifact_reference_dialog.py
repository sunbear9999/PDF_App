"""Workspace references reuse the same live card as chat, notes and search."""
from PySide6.QtWidgets import QDialog, QVBoxLayout
from PySide6.QtCore import Qt
from gui.components.artifact_reference import ArtifactReferenceWidget


class ArtifactReferenceDialog(QDialog):
    def __init__(self, app_context, reference, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Source reference")
        self.resize(650, 420)
        layout = QVBoxLayout(self)
        self.reference = ArtifactReferenceWidget(app_context, reference, self, show_quote=True, allow_note=True)
        self._jump_pending = True
        self.reference.ready.connect(self._loaded)
        layout.addWidget(self.reference)
        bus = getattr(app_context, "bus", None)
        if bus is not None and hasattr(bus, "project_clearing_started"):
            bus.project_clearing_started.connect(self.close)

    def _loaded(self):
        if self._jump_pending:
            self._jump_pending = False
            self.reference.jump()
