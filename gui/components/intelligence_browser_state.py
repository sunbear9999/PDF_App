"""Debounced project preferences; Qt captures state and workers persist it."""
from PySide6.QtCore import QObject, QTimer, QSignalBlocker, Qt
from PySide6.QtWidgets import QApplication
from gui.components.artifact_tasks import ArtifactTaskRunner


DEFAULT_SPLIT = (360, 400)   # list above, inspector below

class IntelligenceBrowserState(QObject):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx, self.browser = app_context, parent
        self.state, self.project = {}, None
        self._restoring, self._loaded, self._sources_ready = False, False, False
        self._pending_edit = False
        self._connections = []
        self.reader = ArtifactTaskRunner(app_context, self)
        self.writer = ArtifactTaskRunner(app_context, self)
        self.reader.completed.connect(self._received)
        self.reader.failed.connect(self._failed)
        self.writer.failed.connect(self._failed)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.flush)
        self._connect(self.browser.scope.currentIndexChanged, self.schedule)
        self._connect(self.browser.tabs.currentChanged, self.schedule)
        self._connect(self.browser.search.textChanged, self.schedule)
        for identity, panel in self.browser._panels.items():
            self.panel_ready(identity, panel)
        bus = getattr(app_context, "bus", None)
        if (signal := getattr(bus, "project_clearing_started", None)) is not None:
            self._connect(signal, self._cleared)
        if (signal := getattr(bus, "project_loaded", None)) is not None:
            self._connect(signal, self.load)
        self.load()

    def _connect(self, signal, slot):
        signal.connect(slot)
        self._connections.append((signal, slot))

    def panel_ready(self, identity, panel):
        """Attach persistence when a lazily-created Intelligence view appears."""
        self._connect(panel.splitter.splitterMoved, self.schedule)
        if getattr(panel, "filter_bar", None) is not None:
            self._connect(panel.filter_bar.changed, self.schedule)
        self._connect(panel.tree.expanded, self.schedule)
        self._connect(panel.tree.collapsed, self.schedule)
        self._connect(panel.tree.header().sortIndicatorChanged, self.schedule)
        if self._loaded:
            self._apply_panel_state(identity, panel)

    def _apply_panel_state(self, identity, panel):
        view = self.state.get("views", {}).get(identity, {})
        if sizes := view.get("splitter"):
            panel.splitter.setSizes(sizes)
        if getattr(panel, "filter_bar", None) is not None and view.get("filters"):
            with QSignalBlocker(panel.filter_bar):
                panel.filter_bar.restore(view["filters"])
        if "sort_column" in view:
            panel.tree.setSortingEnabled(True)
            panel.tree.header().setSortIndicatorShown(True)
            order = Qt.SortOrder.DescendingOrder if view.get("sort_order") == "descending" else Qt.SortOrder.AscendingOrder
            panel.tree.sortByColumn(view["sort_column"], order)

    def load(self, *_):
        self._loaded = False
        self.reader.start("get_browser_preferences")

    def _received(self, _):
        result, self.reader.result = self.reader.result, None
        if not result:
            return
        self.state, self.project = result["state"], result["project_identity"]
        if self._pending_edit:
            self.state.update(view_id=self.browser.tabs.currentWidget().property("intelligence_view_id"),
                              search=self.browser.search.text())
            self._pending_edit = False
        self._loaded = True
        self._restoring = True
        browser = self.browser
        with QSignalBlocker(browser.tabs), QSignalBlocker(browser.search):
            browser.search.setText(self.state.get("search", ""))
            for index in range(browser.tabs.count()):
                if browser.tabs.widget(index).property("intelligence_view_id") == self.state.get("view_id"):
                    browser.tabs.setCurrentIndex(index)
        for identity, panel in browser._panels.items():
            self._apply_panel_state(identity, panel)
        self._restore_source()
        self._restoring = False
        browser._describe_tab()          # the restored tab's description (its signal was blocked)
        browser.pager.reset()

    def sources_ready(self):
        self._sources_ready = True
        if self._loaded:
            self._restoring = True
            self._restore_source()
            self._restoring = False

    def _restore_source(self):
        if not self._sources_ready or "source_id" not in self.state:
            return
        source = self.state.pop("source_id")
        self.browser.scope.setCurrentIndex(max(0, self.browser.scope.findData(source)))

    def apply_tree(self, identity):
        panel = self.browser._panels.get(identity)
        if panel is None:
            return
        expanded = set(self.state.get("views", {}).get(identity, {}).get("expanded", []))
        self._restoring = True
        if panel.tree.isSortingEnabled():
            panel.tree.sortByColumn(panel.tree.header().sortIndicatorSection(), panel.tree.header().sortIndicatorOrder())
        if expanded:
            for index in range(panel.tree.model().rowCount()):
                item = panel.tree.model().index(index, 0)
                if not panel.tree.model().hasChildren(item):
                    continue
                row = item.data(Qt.ItemDataRole.UserRole) or {}
                if row.get("artifact_id") in expanded:
                    panel.tree.setExpanded(item, True)
        self._restoring = False

    def schedule(self, *_):
        if not self._loaded and not self._restoring:
            self._pending_edit = True
        if self._loaded and not self._restoring:
            self.timer.start()

    def capture(self):
        browser = self.browser
        state = {"source_id": browser.scope.currentData() or "", "search": browser.search.text(),
                 "view_id": browser.tabs.currentWidget().property("intelligence_view_id"), "views": {}}
        for identity, panel in browser._panels.items():
            expanded = []
            for index in range(panel.tree.model().rowCount()):
                item = panel.tree.model().index(index, 0)
                row = item.data(Qt.ItemDataRole.UserRole) or {}
                if panel.tree.isExpanded(item) and row.get("artifact_id"):
                    expanded.append(row["artifact_id"])
            previous = set(self.state.get("views", {}).get(identity, {}).get("expanded", []))
            loaded = {panel.tree.model().index(i, 0).data(Qt.ItemDataRole.UserRole).get("artifact_id") for i in range(panel.tree.model().rowCount()) if isinstance(panel.tree.model().index(i, 0).data(Qt.ItemDataRole.UserRole), dict)}
            view = {"expanded": list(previous - loaded | set(expanded)), "splitter": panel.splitter.sizes()}
            if getattr(panel, "filter_bar", None) is not None:
                view["filters"] = panel.filter_bar.state()
            if panel.tree.isSortingEnabled():
                view.update(sort_column=panel.tree.header().sortIndicatorSection(),
                            sort_order="descending" if panel.tree.header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder else "ascending")
            state["views"][identity] = view
        return state

    def flush(self):
        self.timer.stop()
        if self._loaded and self.project:
            self.state = self.capture()
            self.writer.start("save_browser_preferences", state=self.state, expected_project=self.project)

    def _cleared(self, *_):
        self.timer.stop()
        self.reader.cancel()
        self.writer.cancel()
        self.state, self.project = {}, None
        self._loaded, self._sources_ready = False, False
        self._pending_edit = False
        self._restoring = True
        self.browser.search.clear()
        self.browser.tabs.setCurrentIndex(0)
        self.browser._source_titles = {}
        self.browser._last_snapshot = {}
        if self.browser._refresh_worker is not None:
            self.browser._refresh_worker.requestInterruption()
        for panel in self.browser._panels.values():
            panel.tree.clear()
            panel.inspector.show_record(None, load_context=False, force=True)
            panel.splitter.setSizes(list(DEFAULT_SPLIT))
            panel.tree.setSortingEnabled(False)
            panel.tree.header().setSortIndicatorShown(False)
        self._restoring = False

    def _failed(self, error):
        self.browser.nav_status.setText(error)

    def reset_view(self, identity):
        self.state.get("views", {}).pop(identity, None)
        panel = self.browser._panels[identity]
        panel.tree.setSortingEnabled(False)
        panel.tree.header().setSortIndicatorShown(False)
        panel.tree.collapseAll()
        panel.splitter.setSizes(list(DEFAULT_SPLIT))
        self.schedule()
        self.browser.refresh()

    def shutdown(self):
        self.flush()
        self.reader.shutdown()
        self.timer.stop()
        for signal, slot in self._connections:
            signal.disconnect(slot)
        self._connections.clear()
        writer = self.writer
        writer.setParent(QApplication.instance())
        writer.failed.disconnect(self._failed)
        def dispose(busy=False):
            if not busy:
                writer.shutdown()
                writer.deleteLater()
        writer.busy_changed.connect(dispose)
        if not writer.busy:
            dispose()
