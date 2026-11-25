"""Catalog-driven model controls with the shared asynchronous installer."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QGroupBox, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QApplication, QFileDialog,
                               QCheckBox)
from gui.components.model_dependency_tasks import ModelDependencyWorker
from gui.components.artifact_actions import bind_artifact_action
from gui.components.sentence_role_setup_card import catalog_detail_labels
from core.events.domains.intelligence_events import IntelligenceEvent, ProcessingStatePayload

MAX_STATUS_CHARS = 320
MAX_LOG_CHARS = 4000


class ModelDependencyCard(QGroupBox):
    changed = Signal()
    busy_changed = Signal(bool)

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self.dependency, self.worker = app_context, {}, None
        self._closing = False
        layout = QVBoxLayout(self)
        self.description, self.status = QLabel(self), QLabel(self)
        self.description.setWordWrap(True)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.description)
        layout.addWidget(self.status)
        # Optional resources (GLiNER, gazetteer, language model) are used only when switched on.
        self.use_box = QCheckBox("Use in this project's entity extraction", self)
        self.use_box.setToolTip("Off by default. Switching it on makes the Entities stage re-run with this resource.")
        self.use_box.toggled.connect(self._set_used)
        layout.addWidget(self.use_box)
        self.details = QVBoxLayout()
        layout.addLayout(self.details)
        bar = QHBoxLayout()
        self.install_button = QPushButton("Install / repair", self)
        self.import_button = QPushButton("Import model…", self)
        self.validate_button = QPushButton("Validate", self)
        for button, callback in ((self.install_button, self.install), (self.import_button, self.import_model), (self.validate_button, self.validate_model)):
            button.clicked.connect(callback)
            bar.addWidget(button)
        layout.addLayout(bar)
        for name, label in (("install", "Install or Repair Selected Model"), ("import_model", "Import Model Folder"), ("validate_model", "Validate Model")):
            bind_artifact_action(app_context, self, "intelligence.model." + name, label, getattr(self, name), "toolbar:intelligence:models")

    def set_dependency(self, dependency):
        self.dependency = dict(dependency)
        self.setTitle(dependency.get("label") or dependency.get("id") or "Model")
        self.description.setText(dependency.get("description") or "")
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setText(f"{dependency.get('status') or 'unknown'} · {dependency.get('size_hint') or 'size varies'}")
        if dependency.get("last_error"):
            self.status.setText(self.status.text() + " · Last operation: " + dependency["last_error"][-MAX_STATUS_CHARS:])
            self.status.setToolTip(dependency["last_error"][-MAX_LOG_CHARS:])
        self.install_button.setEnabled(bool(dependency.get("installable")))
        self._show_use_box()
        self.import_button.setVisible(dependency.get("kind") in {"manual", "onnx_hf"})
        while self.details.count():
            if widget := self.details.takeAt(0).widget():
                widget.deleteLater()
        for label in catalog_detail_labels(dependency, "palette(text)"):
            label.setStyleSheet("")
            label.setTextFormat(Qt.TextFormat.PlainText)
            self.details.addWidget(label)

    def _show_use_box(self):
        """Read/write the project setting off-thread (ModelReadinessService owns its locks)."""
        key = self.dependency.get("setting_key")
        available = bool(key and getattr(self.ctx, "model_readiness_service", None))
        self.use_box.setVisible(available)
        self.use_box.setEnabled(False)
        if available:
            self._setting_runner().start("resource_enabled", setting_key=key)

    def _setting_runner(self):
        if getattr(self, "_settings", None) is None:
            from gui.components.artifact_tasks import ArtifactTaskRunner
            self._settings = ArtifactTaskRunner(self.ctx, self, service_name="model_readiness_service")
            self._settings.completed.connect(self._setting_read)
            self._settings.failed.connect(self.status.setText)
            self.destroyed.connect(self._settings.shutdown)
        return self._settings

    def _setting_read(self, operation):
        enabled, self._settings.result = bool(self._settings.result), None
        self.use_box.blockSignals(True)
        self.use_box.setChecked(enabled)
        self.use_box.setEnabled(self.dependency.get("status") == "ready")
        self.use_box.blockSignals(False)
        if operation == "set_resource_enabled":
            self.changed.emit()

    def _set_used(self, enabled):
        key = self.dependency.get("setting_key")
        if key and getattr(self.ctx, "model_readiness_service", None):
            self.use_box.setEnabled(False)
            self._setting_runner().start("set_resource_enabled", setting_key=key, enabled=bool(enabled))

    def install(self):
        if self.worker:
            self.worker.cancel()
            self.status.setText("Cancellation requested; waiting for the installer to stop.")
            return
        self._start("install")

    def import_model(self):
        folder = QFileDialog.getExistingDirectory(self, "Import model folder")
        if folder:
            self._start("import", folder)

    def validate_model(self):
        self._start("validate")

    def _start(self, action, folder=""):
        service = getattr(self.ctx, "model_dependency_service", None)
        if self._closing or self.worker or not service:
            return
        self.worker = ModelDependencyWorker(service, self.dependency["id"], QApplication.instance(), action=action, source_dir=folder)
        self.worker.done.connect(self._done, Qt.ConnectionType.QueuedConnection)
        self.worker.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.worker.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self.worker.log.connect(self._log, Qt.ConnectionType.QueuedConnection)
        self.install_button.setText("Cancel" if action == "install" else "Working…")
        self.install_button.setEnabled(action == "install")
        self.import_button.setEnabled(False)
        self.validate_button.setEnabled(False)
        self.status.setText("Installing / checking model…")
        self.busy_changed.emit(True)
        self.worker.start()

    def _done(self, identity, ok, error):
        if self._closing:
            return
        self.status.setText("Model operation completed." if ok else error[-MAX_STATUS_CHARS:] or "Model operation failed.")
        self.status.setToolTip(error[-MAX_LOG_CHARS:])
        bus = getattr(self.ctx, "bus", None)
        if bus is not None:
            bus.intelligence_state_changed.emit(IntelligenceEvent.MODEL_DEPENDENCY_CHANGED, ProcessingStatePayload(dependency_id=identity))

    def _finished(self):
        self.worker = None
        self.install_button.setText("Install / repair")
        self.install_button.setEnabled(bool(self.dependency.get("installable")))
        self.import_button.setEnabled(True)
        self.validate_button.setEnabled(True)
        self.busy_changed.emit(False)
        if not self._closing:
            self.changed.emit()

    def _progress(self, identity, current, total, message):
        if not self._closing:
            self.status.setText(f"{message} · {int(current * 100 / total) if total else 0}%")

    def _log(self, identity, message):
        if not self._closing:
            self.status.setToolTip(str(message)[-MAX_LOG_CHARS:])

    def shutdown(self):
        self._closing = True
        if self.worker:
            self.worker.cancel()
