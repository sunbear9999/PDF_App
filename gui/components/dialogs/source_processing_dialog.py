"""Live source plan, local processing commands and model readiness."""
import uuid
from PySide6.QtCore import QTimer, Qt, QSignalBlocker
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QToolBar, QSplitter, QScrollArea, QWidget
from core.models.intelligence_models import ProcessingMode
from core.events.domains.workflow_events import WorkflowIntent, WorkflowEvent, WorkflowPayload
from core.events.domains.intelligence_events import IntelligenceEvent, ProcessingStatePayload
from core.events.domains.help_events import HelpIntent, HelpPayload
from gui.components.source_scope_combo import SourceScopeCombo
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_actions import bind_artifact_action
from gui.components.processing_stage_table import ProcessingStageTable
from gui.components.model_dependency_card import ModelDependencyCard

REFRESH_DELAY_MS = 200


class SourceProcessingDialog(QDialog):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self.data, self.overrides = app_context, {}, {}
        self._closing, self._dirty, self._job = False, False, ""
        self._last_message = ""
        self._connections, self.cards = [], []
        self.setWindowTitle("Source processing and model readiness")
        self.resize(970, 790)
        layout = QVBoxLayout(self)
        self._controls(layout)
        self.status = QLabel("Choose a source to inspect its processing plan.", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.table = ProcessingStageTable(splitter)
        self.table.choice_changed.connect(self._choice)
        self.table.stage_selected.connect(self._details)
        self.details = QScrollArea(splitter)
        self.details.setWidgetResizable(True)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        layout.addWidget(splitter, 1)
        self.reader = ArtifactTaskRunner(app_context, self, service_name="processing_plan_service")
        self.reader.completed.connect(self._loaded)
        self.reader.failed.connect(self.status.setText)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(REFRESH_DELAY_MS)
        self.timer.timeout.connect(self.reload)
        self._register_help()
        self._connect(self.scope.currentIndexChanged, self._source_changed)
        self._connect(self.scope.sources_invalidated, self.reload)
        bus = getattr(app_context, "bus", None)
        if bus:
            self._connect(bus.intelligence_state_changed, self._invalidated, queued=True)
            self._connect(bus.workflow_state_changed, self._workflow, queued=True)
            self._connect(bus.project_clearing_started, self._project_closed)
        self.reload()

    def _connect(self, signal, slot, queued=False):
        signal.connect(slot, Qt.ConnectionType.QueuedConnection if queued else Qt.ConnectionType.AutoConnection)
        self._connections.append((signal, slot))

    def _register_help(self):
        registry = getattr(getattr(self.ctx, "help_service", None), "ui_target_registry", None)
        if registry:
            identity = f"intelligence.processing.{id(self)}"
            registry.register_widget(identity, self, "core.intelligence.processing")
            self.destroyed.connect(lambda *_, r=registry, key=identity: r.unregister(key))

    def _controls(self, layout):
        bar = QHBoxLayout()
        self.scope = SourceScopeCombo(self.ctx, self)
        self.scope.setAccessibleName("Source to process")
        self.scope.setItemText(0, "Choose a source…")
        bar.addWidget(self.scope, 1)
        self.preset = QComboBox(self)
        self.preset.setAccessibleName("Source processing preset")
        for mode in ProcessingMode:
            self.preset.addItem(mode.value.capitalize(), mode.value)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        bar.addWidget(self.preset)
        layout.addLayout(bar)
        actions = QToolBar(self)
        actions.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        actions.setMovable(False)
        self.buttons = {}
        for name, label in (("save_plan", "Save source plan"), ("discard_plan", "Discard draft"), ("run_missing", "Run missing"), ("rerun_stale", "Rerun stale"),
                            ("run_selected", "Run selected stage"), ("force_selected", "Force rerun stage"), ("cancel", "Cancel this run"), ("guide", "Processing help")):
            callback = getattr(self, name)
            button = actions.addAction(label, callback)
            bind_artifact_action(self.ctx, self, "intelligence.processing." + name, label, callback, "toolbar:intelligence:processing")
            self.buttons[name] = button
        layout.addWidget(actions)

    def set_source(self, identity):
        if self._dirty:
            self.status.setText("Save the draft source plan before switching sources.")
            return
        self._pending_source = identity or ""
        if self.scope.findData(identity) >= 0:
            self.scope.setCurrentIndex(self.scope.findData(identity))
        self.reload()

    def _source_changed(self, *_):
        if self._dirty and self.scope.currentData() != self.data.get("source_id"):
            with QSignalBlocker(self.scope):
                self.scope.setCurrentIndex(max(0, self.scope.findData(self.data.get("source_id"))))
            self.status.setText("Save the draft plan before switching sources.")
            return
        self.data, self.overrides, self._last_message = {}, {}, ""
        self.reload()

    def _preset_changed(self, *_):
        if self.scope.currentData():
            self.overrides, self._dirty = {}, True
            self.timer.start()

    def _choice(self, stage, checked):
        self.overrides[stage] = checked
        self._dirty = True
        self.timer.start()

    def selection(self):
        saved = self.data.get("selection") or {}
        return {"preset": self.preset.currentData(), "stage_overrides": dict(self.overrides),
                "transcription_policy": saved.get("transcription_policy", "ask"), "generative_policy": saved.get("generative_policy", "on_demand")}

    def reload(self, *_):
        if not self._closing:
            self.reader.start("get_processing_snapshot", source_id=self.scope.currentData() or "",
                              selection=self.selection() if self._dirty else None)

    def _loaded(self, operation):
        data, self.reader.result = self.reader.result, None
        if not data or self._closing:
            return
        if "sources" in data:
            self.scope.set_sources(data["sources"])
            self.scope.setItemText(0, "Choose a source…")
            if self.data.get("source_id") and not self.scope.currentData():
                self._dirty, self.overrides, self._last_message = False, {}, "Source is no longer available. Choose another source."
        if pending := getattr(self, "_pending_source", ""):
            self._pending_source = ""
            if self.scope.findData(pending) >= 0:
                self.scope.setCurrentIndex(self.scope.findData(pending))
                return
        if data.get("source_id", "") != (self.scope.currentData() or ""):
            return
        self.data = data
        if operation == "save_selection":
            self._dirty = False
            self.ctx.bus.intelligence_state_changed.emit(IntelligenceEvent.PROCESSING_POLICY_CHANGED, ProcessingStatePayload(source_id=data["source_id"]))
        if not self._dirty and data.get("selection"):
            self.overrides = dict(data["selection"].get("stage_overrides") or {})
            with QSignalBlocker(self.preset):
                self.preset.setCurrentIndex(max(0, self.preset.findData(data["selection"]["preset"])))
        self.table.set_plan(data.get("items") or [])
        if not self.table.rows:
            self._details("")
        self.status.setText("Draft plan — save before running." if self._dirty else "Stages and prerequisites are shared with ingestion. Optional models are not required for built-in rules." if data.get("source_id") else "Choose a source to inspect its processing plan.")
        if self._last_message and not self._dirty:
            self.status.setText(self._last_message)
        self._buttons()

    def _buttons(self):
        model_busy = any(card.worker for card in self.cards)
        self.scope.setEnabled(not self._job and not model_busy)
        self.preset.setEnabled(not self._job and not model_busy)
        self.table.setEnabled(not self._job and not model_busy)
        for name, button in self.buttons.items():
            button.setEnabled(name == "guide" or (name == "cancel" and bool(self._job)) or
                              (name != "cancel" and bool(self.data.get("source_id")) and not self._job and not model_busy and (not self._dirty or name == "save_plan")))
        self.buttons["discard_plan"].setEnabled(self._dirty and not self._job and not model_busy)
        if (self.table.rows.get(self.table.current_stage()) or {}).get("readiness") == "unavailable":
            self.buttons["run_selected"].setEnabled(False)
            self.buttons["force_selected"].setEnabled(False)

    def discard_plan(self):
        self._dirty = False
        self.overrides = {}
        self.reload()

    def save_plan(self):
        if self.data.get("source_id"):
            self.reader.start("save_selection", source_id=self.data["source_id"], selection=self.selection(), expected_project=self.data.get("project_identity"))

    def _run(self, mode):
        if self._dirty or self._job or any(card.worker for card in self.cards) or not self.data.get("source_id"):
            return
        stage = self.table.current_stage()
        self._job = str(uuid.uuid4())
        self.ctx.bus.workflow_action_requested.emit(WorkflowIntent.RUN_BLUEPRINT, WorkflowPayload(
            blueprint_id="intelligence_process_source", job_id=self._job, job_name="Source intelligence: " + self.data.get("title", "source"), job_type="Intelligence",
            initial_state={"source_id": self.data["source_id"], "processing_mode": mode,
                           "processing_stages": [stage] if mode in {"selected", "force"} and stage else [],
                           "processing_project": self.data["project_identity"]}))
        self.status.setText("Processing request queued. Track progress in the process monitor.")
        self._buttons()

    def run_missing(self): self._run("missing")
    def rerun_stale(self): self._run("stale")
    def run_selected(self): self._run("selected")
    def force_selected(self): self._run("force")

    def cancel(self):
        if self._job:
            self.ctx.bus.workflow_action_requested.emit(WorkflowIntent.ABORT_WORKFLOW, WorkflowPayload(job_id=self._job))

    def _workflow(self, event, payload):
        if getattr(payload, "job_id", None) == self._job and event in {WorkflowEvent.COMPLETED, WorkflowEvent.FAILED, WorkflowEvent.CANCELLED}:
            self._last_message = self._completion_message(payload) if event == WorkflowEvent.COMPLETED else getattr(payload, "errors", None) or "Processing cancelled."
            self._job = ""
            self._buttons()
            self.timer.start()

    def _completion_message(self, payload):
        result = (getattr(payload, "initial_state", {}) or {}).get("source_processing_result") or {}
        counts = {}
        for row in result.get("results") or []:
            counts[row.get("status", "unknown")] = counts.get(row.get("status", "unknown"), 0) + 1
        detail = ", ".join(f"{count} {state}" for state, count in counts.items()) or "no stages needed"
        return f"Processing {result.get('status', 'finished')}: {result.get('error') or detail}. Inspect outcomes below."

    def _invalidated(self, *_):
        if not self._closing:
            self.timer.start()

    def guide(self):
        self.ctx.bus.help_action_requested.emit(HelpIntent.SHOW_TOPIC, HelpPayload(topic_id="core.intelligence.processing"))

    def _project_closed(self, *_):
        self.cancel()
        self.close()

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            self.timer.stop()
            self.reader.shutdown()
            self.scope.shutdown()
            for card in self.cards:
                card.shutdown()
            for signal, slot in self._connections:
                signal.disconnect(slot)
        super().closeEvent(event)

    def _details(self, identity):
        self._buttons()
        for card in self.cards:
            if card.worker:
                return
            card.shutdown()
        self.cards = []
        body = QWidget(self.details)
        layout = QVBoxLayout(body)
        row = self.table.rows.get(identity) or {}
        fields = (("Purpose", "description"), ("Cost", "cost_hint"), ("Chosen because", "reason"), ("Readiness", "readiness_detail"),
                  ("Last result", "detail"), ("Would run now", "provider"), ("Recorded last run", "recorded_provider"))
        if not row:
            layout.addWidget(QLabel("Choose a source and stage to inspect its processing details.", body))
        for caption, key in fields if row else ():
            label = QLabel(f"{caption}: {row.get(key) or 'Not recorded'}", body)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label)
        catalog = {entry["id"]: entry for entry in self.data.get("models") or []}
        for key in row.get("model_dependencies") or []:
            if key in catalog:
                card = ModelDependencyCard(self.ctx, body)
                card.set_dependency(catalog[key])
                card.changed.connect(self.reload)
                card.busy_changed.connect(self._model_busy_changed)
                self.cards.append(card)
                layout.addWidget(card)
        layout.addStretch()
        old = self.details.takeWidget()
        self.details.setWidget(body)
        if old:
            old.deleteLater()

    def _model_busy_changed(self, *_):
        self._buttons()
