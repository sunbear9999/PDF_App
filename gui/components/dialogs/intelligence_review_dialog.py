"""One resumable review dialog for registered artifact issue queues."""
from PySide6.QtCore import QTimer, Qt, QSignalBlocker
from core.events.domains.intelligence_events import IntelligenceEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton

from gui.components.artifact_inspector import ArtifactInspector
from gui.components.knowledge_decision_panel import KnowledgeDecisionPanel
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_source_navigator import ArtifactSourceNavigator
from gui.components.artifact_actions import bind_artifact_action
from gui.components.intelligence_filter_bar import ChoiceMenu
from core.models.entity_categories import MEASURE_TYPES, TYPE_LABELS

REVIEW_TYPE_CHOICES = [kind for kind in TYPE_LABELS if kind not in MEASURE_TYPES]


class IntelligenceReviewDialog(QDialog):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self.source_id, self.receipt = app_context, "", None
        self._closing, self._advance, self._pending = False, "saved", None
        self._active_issue = None
        self.setWindowTitle("Review intelligence issues")
        self.resize(780, 760)
        layout = QVBoxLayout(self)
        self.queue = QComboBox(self)
        self.queue.setAccessibleName("Review issue category")
        registry = getattr(app_context, "intelligence_review_registry", None) or getattr(getattr(app_context, "artifact_service", None), "review_registry", None)
        for spec in registry.all() if registry else []:
            self.queue.addItem(spec.label, spec.id)
            self.queue.setItemData(self.queue.count() - 1, spec.description, Qt.ItemDataRole.ToolTipRole)
        filters = QHBoxLayout()
        filters.addWidget(self.queue, 1)
        self.types = ChoiceMenu("entity_type", self, offer_measures=False)
        self.types.set_counts({kind: None for kind in REVIEW_TYPE_CHOICES})
        self.types.setToolTip("Only show items about entities of the ticked types (people, places, …). Nothing ticked shows all.")
        self.types.changed.connect(self.reload)
        filters.addWidget(self.types)
        layout.addLayout(filters)
        self.progress = QLabel("Loading review queue…", self)
        self.progress.setWordWrap(True)
        self.progress.setAccessibleName("Review progress and source context")
        layout.addWidget(self.progress)
        self.inspector = ArtifactInspector(self, app_context=app_context)
        layout.addWidget(self.inspector, 1)
        self.decision = KnowledgeDecisionPanel(app_context,self)
        self.decision.hide()
        self._special = False
        layout.addWidget(self.decision,1)
        self.decision.decided.connect(self._decision_saved)
        self.decision.busy_changed.connect(self._busy)
        self.runner = ArtifactTaskRunner(app_context, self)
        self.runner.completed.connect(self._loaded)
        self.counter = ArtifactTaskRunner(app_context, self)     # remaining count, after the item shows
        self.counter.completed.connect(self._counted)
        self.runner.failed.connect(self.progress.setText)
        self.runner.busy_changed.connect(self._busy)
        self.inspector.runner.busy_changed.connect(self._busy)
        self.inspector.saved.connect(self._saved)
        self.queue.currentIndexChanged.connect(self.reload)
        self._buttons(layout)
        self.navigator = ArtifactSourceNavigator(app_context, self.progress.setText, self)
        self._project_signal = getattr(getattr(app_context, "bus", None), "project_clearing_started", None)
        if self._project_signal is not None:
            self._project_signal.connect(self._project_closed)
        self._change_signal = getattr(getattr(app_context,"bus",None),"intelligence_state_changed",None)
        if self._change_signal is not None:
            self._change_signal.connect(self._external_change,Qt.ConnectionType.QueuedConnection)
        QTimer.singleShot(0, self.reload)

    def _external_change(self, event, _):
        if self._closing or event not in {IntelligenceEvent.ARTIFACT_REVIEWED,IntelligenceEvent.KNOWLEDGE_REVIEWED}:
            return
        if not (self.runner.busy or self.inspector.runner.busy or self.decision.runner.busy or self._has_changes()):
            self.reload()

    def _buttons(self, layout):
        bar = QHBoxLayout()
        self.buttons = []
        for name, label in (("save_next", "Save & next"), ("skip", "Skip"), ("defer", "Defer"),
                            ("undo", "Undo"), ("jump", "Jump to source"), ("reset_queue", "Revisit skipped")):
            button = QPushButton(label.replace("&", "&&"), self)   # a single & would become a shortcut underline
            callback = getattr(self, name)
            button.clicked.connect(callback)
            bind_artifact_action(self.ctx, self, f"intelligence.review_{name}", label, callback, "toolbar:intelligence:review", button)
            bar.addWidget(button)
            self.buttons.append(button)
        bar.addStretch(1)
        layout.addLayout(bar)

    def set_source(self, source_id):
        if self._has_changes() and self.source_id != (source_id or ""):
            self.progress.setText("Save or discard edits before switching sources.")
            return
        self.source_id = source_id or ""
        self.reload()

    def reload(self, *_):
        if self._has_changes():
            with QSignalBlocker(self.queue):
                self.queue.setCurrentIndex(max(0, self.queue.findData(self._active_issue)))
            self.progress.setText("Save or discard edits before switching queues.")
            return
        self._active_issue = self.queue.currentData()
        if not self._closing and self.queue.currentData():
            self.runner.start("get_review_queue", issue_id=self.queue.currentData(), source_id=self.source_id,
                              entity_types=sorted(self.types.selected), count=False)

    def _loaded(self, operation):
        result, self.runner.result = self.runner.result, None
        if self._closing or not result:
            return
        if operation == "execute_review_command":
            self._load("undone")
            return
        state = result.get("progress") or {}
        self.receipt = state.get("receipt")
        self._state = state
        self._show_progress(result.get("remaining"))
        if result.get("remaining") is None:
            self.counter.start("count_review_queue", issue_id=self.queue.currentData(), source_id=self.source_id,
                               entity_types=sorted(self.types.selected))
        kind = result.get("resource_kind")
        self._special = kind in {"assertion", "migration", "merge"} and bool(result.get("row"))
        self._pair_id = (result.get("row") or {}).get("pair_id") if kind == "merge" else None
        self.buttons[4].setVisible(kind != "merge")   # each entity pane lists its mentions to jump to
        self.inspector.setVisible(not self._special)
        self.decision.setVisible(self._special)
        if self._special:
            row = result["row"]
            if kind == "merge":   # a suggested duplicate: the less mentioned entity merges into the other
                self.decision.configure(row["source_id"], "entity_merge", {"target_id": row["target_id"]})
                self.progress.setText(self.progress.text() + " · " + row["reason"])
            else:
                self.decision.configure(row.get("assertion_id") or row["artifact_id"], "relation_verify" if kind == "assertion" else "migration_resolve")
        else:
            self.decision.runner.cancel()
            self.inspector.show_record(result.get("row"), editor_context=result.get("editor_context"), load_context=False, force=True)
        if not result.get("row"):
            self.inspector.status.setText("No remaining items in this queue. Revisit skipped items or choose another category.")

    def _show_progress(self, remaining):
        state = getattr(self, "_state", {}) or {}
        count = "counting…" if remaining is None else f"{remaining} remaining"
        self.progress.setText(f"{count} · {state.get('reviewed', 0)} reviewed · "
                              f"{state.get('skipped_count', 0)} skipped · {state.get('deferred_count', 0)} deferred")

    def _counted(self, _):
        result, self.counter.result = self.counter.result or {}, None
        if not self._closing and not self.runner.busy:
            self._show_progress(result.get("remaining"))

    def _busy(self, busy):
        busy = busy or self.runner.busy or self.inspector.runner.busy or self.decision.runner.busy
        self.queue.setEnabled(not busy)
        for button in self.buttons:
            button.setEnabled(not busy)
        if not busy and self._pending:
            action, self._pending = self._pending, None
            self._load(action)

    def _load(self, action):
        if self.runner.busy or self.inspector.runner.busy or self.decision.runner.busy:
            self._pending = action
            return
        self.runner.start("get_review_queue", issue_id=self.queue.currentData(), source_id=self.source_id,
                          action=action, artifact_id=(getattr(self, "_pair_id", None) or self.decision.identity) if self._special else self.inspector.row.get("artifact_id", ""),
                          receipt=self.decision.receipt if self._special else self.inspector.receipt,
                          entity_types=sorted(self.types.selected), count=False)

    def _has_changes(self):
        return self.decision.has_changes if self._special else bool(self.inspector.form and self.inspector.form.has_changes)

    def _decision_saved(self, command):
        self._load("undone" if command == "undo" else "saved")

    def save_next(self):
        if self._special:
            self.decision.confirm()
            return
        self._advance = "saved"
        form = self.inspector.form
        if form and (form.changes() or form.citation_changed):
            self.inspector.save()
        else:
            self.inspector.verify()

    def _saved(self):
        self._load(self._advance)

    def skip(self):
        if self._has_changes():
            self.progress.setText("Save or discard changes before skipping.")
            return
        self._load("skip")

    def defer(self):
        if self._has_changes():
            self.progress.setText("Save or discard changes before deferring.")
            return
        self._load("defer")

    def undo(self):
        if self._has_changes():
            self.progress.setText("Save or discard changes before undoing the last review.")
            return
        if self.receipt:
            self.runner.start("execute_review_command", artifact_id=self.receipt["artifact_id"], command="undo",
                              expected_revision=self.receipt["after_revision"], receipt=self.receipt)

    def jump(self):
        identity = self.decision.source.row.get("artifact_id") if self._special else self.inspector.row.get("artifact_id")
        if identity:
            self.navigator.navigate(identity)

    def reset_queue(self):
        if self._has_changes():
            self.progress.setText("Save or discard changes before revisiting skipped items.")
            return
        self._load("reset")

    def _project_closed(self, *_):
        self.receipt = None
        self.close()

    def closeEvent(self, event):
        if self._closing:
            super().closeEvent(event)
            return
        self._closing = True
        if self._project_signal is not None:
            self._project_signal.disconnect(self._project_closed)
        if self._change_signal is not None:
            self._change_signal.disconnect(self._external_change)
        self.runner.shutdown()
        self.counter.shutdown()
        self.inspector.shutdown()
        self.decision.shutdown()
        self.navigator.shutdown()
        super().closeEvent(event)
