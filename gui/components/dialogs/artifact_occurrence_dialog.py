"""Complete, bounded occurrence browser for an entity or bibliography entry."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QVBoxLayout, QLineEdit, QSplitter, QTreeWidgetItem, QToolBar, QAbstractItemView

from core.events.domains.intelligence_events import IntelligenceIntent, IntelligenceEvent, ReviewDecisionPayload
from gui.components.artifact_actions import bind_artifact_action
from core.models.intelligence_models import ArtifactQuery, SourceScope
from gui.components.cursor_pager import CursorPager
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_tree import ArtifactTreeView
from gui.components.dialogs.artifact_inspector_dialog import ArtifactInspectorDialog


class ArtifactOccurrenceDialog(ArtifactInspectorDialog):
    def __init__(self, app_context, parent=None):
        super().__init__(app_context, parent)
        self.artifact_id, self.source_id = "", ""
        self.setWindowTitle("Artifact occurrences")
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search all occurrences…")
        self.search.setAccessibleName("Search all occurrences")
        layout = self.layout()
        layout.insertWidget(0, self.search)
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.tree = ArtifactTreeView(self.splitter)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setHeaderLabels(["Kind", "Source passage", "Location", "Assessment"])
        self.tree.currentItemChanged.connect(self._selected)
        layout.removeWidget(self.inspector)
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.inspector)
        layout.insertWidget(1, self.splitter, 1)
        self.pager = CursorPager(app_context, self)
        self.pager.register_actions("intelligence.occurrences", "toolbar:intelligence:occurrences")
        self.pager.requested.connect(self.load)
        layout.insertWidget(2, self.pager)
        toolbar = QToolBar(self)
        self.split_action = toolbar.addAction("Split selected mentions on this page…",self.split_selected)
        self.split_action.setEnabled(False)
        bind_artifact_action(app_context,self,"intelligence.occurrences.split","Split Selected Occurrence Mentions",self.split_selected,"toolbar:intelligence:occurrences")
        layout.insertWidget(3,toolbar)
        self.runner = ArtifactTaskRunner(app_context, self)
        self.runner.completed.connect(self._loaded)
        self.runner.failed.connect(self.inspector.status.setText)
        self.inspector.saved.connect(self.load)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.pager.reset)
        self.search.textChanged.connect(self.search_changed)
        self._change_signal = getattr(getattr(app_context,"bus",None),"intelligence_state_changed",None)
        if self._change_signal is not None:
            self._change_signal.connect(self._knowledge_changed,Qt.ConnectionType.QueuedConnection)

    def _knowledge_changed(self,event,_):
        if not self._closing and event == IntelligenceEvent.KNOWLEDGE_REVIEWED:
            self.load()

    def search_changed(self, *_):
        self.timer.start()

    def configure(self, artifact_id, source_id=""):
        self.artifact_id, self.source_id = artifact_id, source_id or ""
        self.pager.reset()

    def load(self):
        if self._closing or not self.artifact_id:
            return
        if self.inspector.form and self.inspector.form.has_changes:
            self.inspector.status.setText("Save or discard changes before loading another occurrence page.")
            return
        query = ArtifactQuery(source_scope=SourceScope(source_ids=[self.source_id] if self.source_id else []),
                              text=self.search.text(), cursor=self.pager.cursor, limit=100)
        self.runner.start("query_occurrences", artifact_id=self.artifact_id, query=query)

    def _loaded(self, _):
        page, self.runner.result = self.runner.result, None
        if not page:
            return
        if page.get("invalidated"):
            self.pager.reset()
            return
        parent = page.get("parent") or {}
        self.split_action.setEnabled(parent.get("artifact_type") == "entity")
        label = (parent.get("metadata") or {}).get("canonical_label") or parent.get("exact_text") or "Artifact"
        self.setWindowTitle(f"Occurrences — {label[:80]}")
        self.pager.apply(page)
        self.tree.clear()
        self.inspector.clear()
        names = page.get("source_labels") or {}
        for row in page.get("items") or []:
            locator = row.get("locator") or {}
            location = f"Page {locator.get('page') or locator.get('page_num')}" if locator.get("page") or locator.get("page_num") else row.get("source_path") or row.get("source_type") or "Recorded source"
            if name := names.get(row.get("source_id")):
                location = f"{name} — {location}"
            item = QTreeWidgetItem([row.get("artifact_type") or "", row.get("context_snippet") or row.get("exact_text") or "",
                                    location, row.get("review_state") or ""])
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            self.tree.addTopLevelItem(item)
        if not page.get("items"):
            self.inspector.status.setText("No current occurrences match this source and filter.")

    def split_selected(self):
        rows = [item.data(0,Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()]
        if not rows or any(row.get("artifact_type") != "entity_mention" or row.get("parent_artifact_id") != self.artifact_id for row in rows):
            self.inspector.status.setText("Select mentions of this entity on the current page.")
            return
        self.ctx.bus.intelligence_action_requested.emit(IntelligenceIntent.REVIEW_DECISION,
            ReviewDecisionPayload(self.artifact_id,"entity_split",{"mention_ids":[row["artifact_id"] for row in rows]}))

    def _selected(self, item, _):
        if item:
            self.inspector.show_record(item.data(0, Qt.ItemDataRole.UserRole))

    def closeEvent(self, event):
        if not self._closing:
            if self._change_signal is not None:
                self._change_signal.disconnect(self._knowledge_changed)
            self.timer.stop()
            self.runner.shutdown()
        super().closeEvent(event)
