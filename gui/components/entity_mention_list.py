"""All mentions of one entity, expandable inside a decision pane; pick one to jump to it.

Loads on first expand (off the Qt thread) and lists each mention with its page and a
snippet of its sentence. Selecting a mention and pressing Go to (or double-clicking it)
opens the source at that exact mention.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from gui.components.artifact_source_navigator import ArtifactSourceNavigator
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.icon_buttons import iconize

MAX_LISTED = 1000
TREE_MIN_HEIGHT = 160


class EntityMentionList(QWidget):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self.entity_id, self._loaded_for = app_context, "", None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.toggle = QToolButton(self)
        self.toggle.setCheckable(True)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setText("All mentions")
        self.toggle.setToolTip("Show every mention of this entity; select one and Go to it in the source.")
        self.toggle.toggled.connect(self._toggled)
        row.addWidget(self.toggle)
        self.go = QToolButton(self)
        iconize(self.go, "↗", "Go to the selected mention", "Open the source at the selected mention (or double-click it).")
        self.go.clicked.connect(self.jump)
        row.addWidget(self.go)
        self.status = QLabel("", self)
        row.addWidget(self.status, 1)
        layout.addLayout(row)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Page", "Mention in its sentence"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAccessibleName("Mentions of this entity")
        self.tree.itemDoubleClicked.connect(lambda *_: self.jump())
        self.tree.itemSelectionChanged.connect(self._selection)
        self.tree.setMinimumHeight(TREE_MIN_HEIGHT)
        self.tree.hide()
        layout.addWidget(self.tree, 1)
        self.runner = ArtifactTaskRunner(app_context, self)
        self.runner.completed.connect(self._loaded)
        self.runner.failed.connect(self.status.setText)
        self.navigator = ArtifactSourceNavigator(app_context, self.status.setText, self)
        self._selection()

    def set_entity(self, row):
        """Show the toggle for an entity (hidden for other records); collapse and forget old rows."""
        identity = (row or {}).get("artifact_id") if (row or {}).get("artifact_type") == "entity" else ""
        if identity != self.entity_id:
            self.entity_id, self._loaded_for = identity, None
            self.tree.clear()
            self.toggle.setChecked(False)
        self.setVisible(bool(identity))

    def _toggled(self, shown):
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        self.tree.setVisible(shown)
        if shown and self.entity_id and self._loaded_for != self.entity_id:
            self.status.setText("Loading mentions…")
            self.runner.start("query_occurrences", artifact_id=self.entity_id, query={"limit": MAX_LISTED})

    def _loaded(self, _):
        page, self.runner.result = self.runner.result or {}, None
        if (page.get("parent") or {}).get("artifact_id") != self.entity_id:
            return
        self._loaded_for = self.entity_id
        self.tree.clear()
        for mention in page.get("items") or []:
            locator = mention.get("locator") or {}
            number = locator.get("page")
            item = QTreeWidgetItem([str(number + 1) if isinstance(number, int) else "",
                                    mention.get("context_snippet") or mention.get("exact_text") or ""])
            item.setData(0, Qt.ItemDataRole.UserRole, mention["artifact_id"])
            item.setToolTip(1, mention.get("context_snippet") or "")
            self.tree.addTopLevelItem(item)
        more = " (first 1,000; Browse all occurrences lists every one)" if page.get("next_cursor") else ""
        self.status.setText(f"{self.tree.topLevelItemCount():,} mentions{more}")
        self.tree.resizeColumnToContents(0)

    def _selection(self):
        self.go.setEnabled(bool(self.tree.selectedItems()))

    def jump(self):
        items = self.tree.selectedItems()
        if items:
            self.navigator.navigate(items[0].data(0, Qt.ItemDataRole.UserRole))

    def shutdown(self):
        self.runner.shutdown()
        self.navigator.shutdown()
