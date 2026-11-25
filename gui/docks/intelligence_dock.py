"""Accessible, artifact-backed Intelligence dock."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
    QApplication, QLineEdit, QMessageBox,
)
from gui.components.source_scope_combo import SourceScopeCombo
from gui.components.intelligence_pager import IntelligencePager
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.artifact_actions import bind_artifact_action
from gui.components.icon_buttons import iconize
from gui.components.dialogs.intelligence_review_dialog import IntelligenceReviewDialog
from gui.components.intelligence_view_builders import (
    artifact_columns, argument_unit_columns, explore_columns, graph_preview_rows,
    timeline_preview_rows, populate_citation_tree, populate_entity_tree,
)
from gui.components.intelligence_overview import IntelligenceOverview
from gui.docks.intelligence_workers import _IntelligenceRefreshWorker
from gui.components.artifact_source_navigator import ArtifactSourceNavigator
from gui.components.intelligence_view_panel import IntelligenceViewPanel
from gui.components.intelligence_browser_state import IntelligenceBrowserState
from gui.components.dialogs.artifact_inspector_dialog import ArtifactInspectorDialog
from gui.components.dialogs.source_processing_dialog import SourceProcessingDialog
from gui.docks.intelligence_dock_commands import IntelligenceDockCommands
from gui.docks.intelligence_dock_style import COMPACT_STYLE, fill_generic

CHANGE_QUIET_MS = 1500   # longer than a quick Save & next rhythm


class IntelligenceDockWidget(IntelligenceDockCommands, QWidget):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        self.pm = getattr(app_context, "project_manager", None)
        self.artifacts = getattr(app_context, "artifact_service", None)
        self.registry = getattr(app_context, "intelligence_view_registry", None)
        self._rows = {}
        self._panels = {}
        self._refresh_worker = None
        self._pending_refresh = False
        self._last_snapshot = {}
        self._source_titles = {}
        self._edit_workers = []
        self._closing = False
        self._commands = ArtifactTaskRunner(app_context, self)
        self._commands.completed.connect(self._command_saved)
        self._commands.failed.connect(self._command_failed)
        self._source_commands = ArtifactTaskRunner(
            app_context, self, service_name="source_intelligence_service"
        )
        self._source_commands.completed.connect(self._source_command_saved)
        self._source_commands.failed.connect(self._command_failed)
        self._build()
        self.browser_state = IntelligenceBrowserState(app_context, self)
        self._intelligence_signal = getattr(getattr(app_context, "bus", None), "intelligence_state_changed", None)
        # Edits arrive in bursts (Save & next in a review dialog): refresh once they pause, so the
        # dialog's next read is not queued behind a full dock reload (2.0 s → 0.5–0.8 s per item).
        self._change_timer = QTimer(self)
        self._change_timer.setSingleShot(True)
        self._change_timer.setInterval(CHANGE_QUIET_MS)
        self._change_timer.timeout.connect(self._changed_refresh)
        self._stale_while_hidden, self._ever_shown = False, False
        if self._intelligence_signal is not None:
            self._intelligence_signal.connect(self._state_changed, Qt.ConnectionType.QueuedConnection)
        self.refresh()

    def _build(self):
        self.setObjectName("intelligenceDock")
        self.setStyleSheet(COMPACT_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Scope:"))
        self.scope = SourceScopeCombo(self.ctx, self)
        toolbar.addWidget(self.scope, 1)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Search this tab…")
        self.search.setToolTip("Search the rows of the current tab, across all pages (names, text and details).")
        toolbar.addWidget(self.search, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        iconize(refresh, "⟳", "Refresh", "Reload this tab from the project.")
        toolbar.addWidget(refresh)
        backfill = QPushButton("Run missing")
        iconize(backfill, "⚡", "Run missing", "Queue the selected preset's missing local stages; no generative AI runs.")
        backfill.clicked.connect(self._backfill)
        toolbar.addWidget(backfill)
        review = QPushButton("Review issues…", self)
        review.clicked.connect(self._review_issues)
        review.setToolTip("Work through items the system is unsure about (uncertain types, suggested links, conflicts), one at a time.")
        toolbar.addWidget(review)
        purge = QPushButton("Purge source…", self)
        purge.clicked.connect(self._purge_source)
        bind_artifact_action(
            self.ctx, self, "intelligence.purge_source", "Purge Source Intelligence",
            self._purge_source, "toolbar:intelligence", purge,
        )
        toolbar.addWidget(purge)
        connections = QPushButton("Review connections…", self)
        connections.clicked.connect(self._review_connections)
        bind_artifact_action(self.ctx, self, "intelligence.review_connections", "Review Connections",
                             self._review_connections, "toolbar:intelligence", connections)
        toolbar.addWidget(connections)
        self._add_processing_button(toolbar)
        bind_artifact_action(self.ctx, self, "intelligence.review_issues", "Review issues", self._review_issues, "toolbar:intelligence", review)
        layout.addLayout(toolbar)
        self.tabs = QTabWidget()
        specs = self.registry.all() if self.registry else []
        for spec in specs:
            if spec.id == "overview":
                widget = IntelligenceOverview(self.ctx, self)
            else:
                # Building every inspector, toolbar and filter made session
                # restore scale with every registered view. Materialize a view
                # only when it is selected.
                widget = QWidget(self.tabs)
            widget.setProperty("intelligence_view_id", spec.id)
            widget.setAccessibleName(f"Intelligence {spec.label}")
            widget.setAccessibleDescription(spec.description or spec.empty_state)
            tab_index = self.tabs.addTab(widget, spec.label.replace("&", "&&"))
            self.tabs.setTabToolTip(tab_index, spec.description or spec.empty_state)
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        self.tab_description = QLabel(self)
        self.tab_description.setObjectName("intelligenceTabDescription")
        self.tab_description.setWordWrap(True)
        self.tabs.currentChanged.connect(self._ensure_current_panel)
        self.tabs.currentChanged.connect(self._describe_tab)
        layout.addWidget(self.tab_description)
        layout.addWidget(self.tabs)
        self._describe_tab()
        self.pager = IntelligencePager(self.ctx, self)
        self.pager.attach(self.scope, self.tabs, self.search)
        self.pager.requested.connect(self.refresh)
        layout.addWidget(self.pager)
        self.tabs.currentChanged.connect(self._place_pager)
        self._place_pager()
        self.nav_status = ArtifactSourceNavigator.status_label(self)
        status_row = QHBoxLayout()
        status_row.addWidget(self.nav_status, 1)
        self.undo_merge_button = QPushButton("Undo merge", self)
        iconize(self.undo_merge_button, "↶", "Undo merge", "Reverse the entity merge you just made.")
        self.undo_merge_button.setToolTip("Reverse the entity merge you just made, newest step first.")
        self.undo_merge_button.clicked.connect(self._undo_merge)
        self.undo_merge_button.hide()
        status_row.addWidget(self.undo_merge_button)
        layout.addLayout(status_row)
        self._navigator = ArtifactSourceNavigator(self.ctx, self.nav_status.setText, self)
        keys = getattr(self.ctx, "keybinding_registry", None)
        if keys:
            keys.bind(
                "intelligence.jump_to_source", self._jump_from_current_tab, self,
                Qt.ShortcutContext.WidgetWithChildrenShortcut,
            )

    def _state_changed(self, *_):
        self._change_timer.start()

    def _changed_refresh(self):
        if self._ever_shown and not self.isVisible():
            self._stale_while_hidden = True     # refreshed when shown again
            return
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._ever_shown = True
        if self._stale_while_hidden:
            self._stale_while_hidden = False
            self.refresh()

    def _place_pager(self, *_):
        """Page buttons sit right under the current tab's list (Overview has none)."""
        panel = self._panels.get(self._current_view() or "")
        slot = getattr(panel, "pager_slot", None)
        if slot is not None:
            slot.addWidget(self.pager)
        self.pager.setVisible(slot is not None)

    def _describe_tab(self, *_):
        self._ensure_current_panel()
        if hasattr(self, "pager"):
            self._place_pager()          # also when a restored tab was selected with signals blocked
        widget = self.tabs.currentWidget()
        spec = self.registry.get(widget.property("intelligence_view_id")) if widget and self.registry else None
        self.tab_description.setText(spec.description if spec else "")

    def _tree_view(self, view_id: str):
        panel = IntelligenceViewPanel(self.ctx, self)
        panel.configure(view_id, self)
        panel.setProperty("intelligence_view_id", view_id)
        self._panels[view_id] = panel
        self._rows[view_id] = (panel.tree, panel.inspector)
        if hasattr(self, "browser_state"):
            self.browser_state.panel_ready(view_id, panel)
        return panel

    def _ensure_current_panel(self, *_):
        index = self.tabs.currentIndex()
        widget = self.tabs.widget(index) if index >= 0 else None
        view_id = widget.property("intelligence_view_id") if widget else ""
        if not view_id or view_id == "overview" or view_id in self._panels:
            return
        label, tip = self.tabs.tabText(index), self.tabs.tabToolTip(index)
        panel = self._tree_view(view_id)
        with QSignalBlocker(self.tabs):
            self.tabs.removeTab(index)
            self.tabs.insertTab(index, panel, label)
            self.tabs.setTabToolTip(index, tip)
            self.tabs.setCurrentIndex(index)
        widget.deleteLater()

    def _add_processing_button(self, toolbar):
        button = QPushButton("Processing…", self)
        button.setToolTip("See each source's processing stages and model readiness; run or rerun stages.")
        button.clicked.connect(self._processing)
        toolbar.addWidget(button)
        bind_artifact_action(self.ctx, self, "intelligence.processing", "Processing", self._processing, "toolbar:intelligence", button)

    def refresh(self, *_):
        if self._closing:
            return
        source_id = self.scope.currentData() if hasattr(self, "scope") else ""
        if self._refresh_worker is not None:
            self._pending_refresh = True
            return
        overview = self._widget_for("overview")
        if overview:
            overview.setPlainText("Loading intelligence artifacts…")
        worker = _IntelligenceRefreshWorker(
            self.ctx, source_id, QApplication.instance(),
            query=self.pager.query()[1], view_id=self.pager.query()[0],
            request_key=self.pager.request_key(),
            projection=self.pager.projection(), facets=self.pager.facet_fields(),
        )
        worker.ready.connect(self._apply_snapshot, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._refresh_failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._refresh_finished, Qt.ConnectionType.QueuedConnection)
        self._refresh_worker = worker
        worker.start()

    def _apply_snapshot(self, snapshot):
        if "snapshot_id" in snapshot:
            worker = self._refresh_worker
            snapshot = worker.take_snapshot(snapshot["snapshot_id"]) if worker else None
            if snapshot is None:
                return
        db = getattr(self.pm, "db_intelligence", None)
        if self._closing or snapshot.get("project_identity", (id(db), id(getattr(db, "_conn", None)))) != (id(db), id(getattr(db, "_conn", None))):
            return
        self._restore_sources(snapshot)
        if snapshot.get("source_id", self.scope.currentData()) != self.scope.currentData():
            self._pending_refresh = True
            return
        if snapshot.get("request_key") and snapshot["request_key"] != self.pager.request_key():
            self._pending_refresh = True
            return
        if snapshot.get("invalidated"):
            self.nav_status.setText("Intelligence changed. Restarting from the first page.")
            self.pager.reset()
            return
        self.pager.apply(snapshot)
        self._last_snapshot = snapshot
        self._timeline_status(snapshot)
        if self._active_view('network'):
            self._panels['network'].show_network(snapshot)
            self._fill('network', snapshot.get('network') or [], lambda row:(row['relation_type'],row['display_text']+'\n'+row['relation_explanation'],row['source_id'],row['relation_state']))
        all_rows = snapshot.get("artifacts") or []
        self._refresh_overview(snapshot)
        needle = self.search.text().strip().casefold()
        display_rows = [
            row for row in all_rows
            if not needle or needle in json.dumps(row, ensure_ascii=False, default=str).casefold()
        ]
        current = [
            row for row in display_rows
            if row.get("status") not in {"stale", "superseded", "archived"}
            and row.get("review_state") not in {"rejected", "superseded"}
        ]
        self._fill_entities(snapshot.get("entities") or current)
        self._fill_citations([
            row for row in current if row.get("artifact_type") in {"citation_entry", "in_text_citation"}
        ], snapshot.get("citation_links") or {})
        if snapshot.get("entity_mode") and self._active_view("artifacts"):
            populate_entity_tree(*self._rows["artifacts"], snapshot.get("entities") or [], self._source_title)
        else:
            self._fill("artifacts", display_rows, lambda row: explore_columns(row, self._source_title))
        self._refresh_graph(
            snapshot.get("graph") or {}, snapshot.get("timeline") or [],
            {row.get("artifact_id"): row for row in all_rows}, needle=needle,
        )
        self._fill("claims", [
            row for row in current if row.get("artifact_type") == "argument_unit"
        ], lambda row: argument_unit_columns(row, self._source_title))
        self._refresh_runs(snapshot.get("runs") or [])
        self._fill_other(current)
        bar = getattr(self._panels.get(self._current_view()), "filter_bar", None)
        if bar is not None and snapshot.get("facets"):
            bar.set_facets(snapshot["facets"])
        self.browser_state.apply_tree(self.pager.query()[0])

    def _fill_other(self, rows):
        """Views without their own renderer (Notes, Contents, plugin views) list their declared types."""
        view_id = self._current_view()
        spec = self.registry.get(view_id) if self.registry and view_id else None
        if spec is not None and spec.renderer_id == "intelligence.artifact_list" and view_id in self._rows and view_id != "artifacts":
            fill_generic(self, view_id, spec, rows)

    def _timeline_status(self,snapshot):
        panel=self._panels.get('timeline')
        if panel and hasattr(panel,'timeline_filters') and self._active_view('timeline'):
            panel.timeline_filters.show_result(snapshot)
            panel.show_events(snapshot)
            panel.timeline_summary.setText(panel.timeline_filters.status.text())

    def _restore_sources(self, snapshot):
        if "sources" in snapshot:
            self.scope.set_sources(snapshot["sources"])
            self._source_titles = {source.get("id"): source.get("title") or source.get("path") or ""
                                   for source in snapshot["sources"]}
            self.browser_state.sources_ready()
    def _refresh_overview(self, snapshot):
        widget = self._widget_for("overview")
        if widget and "counts" in snapshot:
            widget.show_snapshot(snapshot)

    def _refresh_graph(self, output, timeline, artifacts_by_id, needle=""):
        from gui.components.event_timeline_view import event_columns
        self._fill("graph", graph_preview_rows(output, artifacts_by_id, needle), artifact_columns)
        self._fill("timeline", timeline_preview_rows(timeline, artifacts_by_id, needle), lambda row: event_columns(row,self._source_title) if row.get('event_id') else (
            (
                "conflict" if row.get("conflicting")
                else row.get("precision") or "date"
            ),
            f"{row.get('date') or 'Undated'} — {row.get('text') or ''}",
            self._source_title(row.get("source_id")),
            row.get("review_state") or "",
        ))

    def _refresh_runs(self, rows):
        self._fill("runs", rows, lambda row: (
            row.get("stage") or "",
            row.get("error_text") or row.get("producer_name") or "",
            self._source_title(row.get("source_id")),
            row.get("status") or "",
        ))

    def _refresh_failed(self, error):
        self.nav_status.setText('Could not load intelligence: '+str(error))
        panel=self._panels.get('timeline')
        if panel and hasattr(panel,'timeline_filters') and self._active_view('timeline'):
            panel.timeline_filters.status.setText('Timeline filters were not applied: '+str(error))
            panel.timeline_summary.setText(panel.timeline_filters.status.text())
            panel.tree.clear()   # rows of the previous filter would look like this filter's result
            panel.interval_chart.set_events([], panel.timeline_filters.applied.zoom)
        widget = self._widget_for("overview")
        if widget:
            widget.setPlainText(f"Could not load Intelligence: {error}")

    def _refilter(self, _text):
        if self._last_snapshot and not (
            self._refresh_worker and self._refresh_worker.isRunning()
        ):
            self._apply_snapshot(self._last_snapshot)

    def _refresh_finished(self):
        worker = self.sender()
        if worker is not self._refresh_worker:
            if worker:
                worker.deleteLater()
            return
        self._refresh_worker = None
        if worker:
            worker.deleteLater()
        if self._pending_refresh:
            self._pending_refresh = False
            self.refresh()

    def closeEvent(self, event):
        if self._closing:
            super().closeEvent(event)
            return
        self._closing = True
        self.browser_state.shutdown()
        if self._intelligence_signal is not None:
            self._intelligence_signal.disconnect(self._state_changed)
        self._change_timer.stop()
        self._commands.shutdown()
        self._source_commands.shutdown()
        for _, inspector in self._rows.values():
            inspector.shutdown()
        for panel in self._panels.values():
            if hasattr(panel,'timeline_filters'):
                panel.timeline_filters.shutdown()
                panel.perspectives.shutdown()
            if hasattr(panel,'network_filters'):
                panel.network_filters.shutdown(); panel.perspectives.shutdown()
        self.pager.shutdown()
        self.scope.shutdown()
        self._navigator.shutdown()
        if self._refresh_worker and self._refresh_worker.isRunning():
            self._refresh_worker.requestInterruption()
        super().closeEvent(event)

    def _fill(self, view_id, rows, columns):
        pair = self._rows.get(view_id)
        if not pair or not self._active_view(view_id):
            return
        tree, raw = pair
        tree.clear()
        raw.clear()
        cells=[]
        for row in rows:
            values = list(columns(row))
            if len(values) > 2 and values[2] == row.get("source_id"):
                values[2] = self._source_title(row.get("source_id"))
            cells.append([str(value or '') for value in values])
        tree.set_artifact_rows(rows,cells)
        if not rows:
            spec = self.registry.get(view_id) if self.registry else None
            raw.show_record({"Next step": spec.empty_state if spec else "No items in this view."})
        tree.resizeColumnToContents(0)

    def _fill_entities(self, rows):
        pair = self._rows.get("entities")
        if pair and self._active_view("entities"):
            populate_entity_tree(*pair, rows, self._source_title)

    def _fill_citations(self, rows, links):
        pair = self._rows.get("citations")
        if not pair or not self._active_view("citations"):
            return
        populate_citation_tree(*pair, rows, links, self._source_title)
        if not rows:
            spec = self.registry.get("citations") if self.registry else None
            pair[1].show_record({"Next step": spec.empty_state if spec else "Run Citations."})

    def _active_view(self, view_id):
        widget = self.tabs.currentWidget()
        return widget is not None and widget.property("intelligence_view_id") == view_id

    def _show_raw(self, item, _previous):
        if not item:
            return
        row = item.data(0, Qt.ItemDataRole.UserRole)
        for tree, raw in self._rows.values():
            if tree is item.treeWidget():
                raw.show_record(row)
                break

    def _backfill(self):
        service = getattr(self.ctx, "source_intelligence_service", None)
        if not service:
            return
        selected = self.scope.currentData()
        ids = [selected] if selected else [
            identity for identity in self._source_titles if identity
        ]
        service.start_backfill_background(ids)

    def _purge_source(self):
        source_id = self.scope.currentData()
        if not source_id:
            reply = QMessageBox.warning(
                self, "Purge orphaned intelligence",
                "No source is selected. Permanently delete intelligence and embeddings "
                "whose source has already been removed from this project?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.nav_status.setText("Purging orphaned intelligence…")
                self._source_commands.start("purge_orphaned_sources")
            return
        title = self._source_title(source_id)
        reply = QMessageBox.warning(
            self, "Purge source intelligence",
            f"Permanently delete all intelligence and embeddings for '{title}'?\n\n"
            "The source stays in the project and can be processed again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.nav_status.setText("Purging source intelligence…")
            self._source_commands.start("purge_source", source_id=source_id)

    def _source_command_saved(self, operation):
        result, self._source_commands.result = self._source_commands.result, None
        if operation in {"purge_source", "purge_orphaned_sources"}:
            self.nav_status.setText(f"Purged {int(result or 0)} intelligence records.")
            self.refresh()

    def _review_issues(self):
        manager = getattr(self.ctx, "dialog_manager", None)
        if manager is None:
            self.nav_status.setText("The review dialog requires the app's DialogManager.")
            return
        dialog = manager.show(IntelligenceReviewDialog, key="intelligence_review", singleton=True,
                              factory=lambda: IntelligenceReviewDialog(self.ctx, parent=self))
        if dialog:
            dialog.set_source(self.scope.currentData())

    def _open_inspector(self, view_id):
        rows = self._selected_rows(view_id)
        manager = getattr(self.ctx, "dialog_manager", None)
        if len(rows) != 1 or manager is None:
            self.nav_status.setText("Select one record to open it in the expanded inspector.")
            return
        dialog = manager.show(ArtifactInspectorDialog, key="intelligence_expanded", singleton=True,
                              factory=lambda: self._expanded_dialog())
        if dialog:
            dialog.set_record(rows[0])

    def _expanded_dialog(self):
        dialog = ArtifactInspectorDialog(self.ctx, self)
        dialog.inspector.saved.connect(self.refresh)
        return dialog

    def _processing(self):
        manager = getattr(self.ctx, "dialog_manager", None)
        if manager:
            dialog = manager.show(SourceProcessingDialog, key="intelligence_processing", singleton=True,
                                  factory=lambda: SourceProcessingDialog(self.ctx, self))
            if dialog:
                dialog.set_source(self.scope.currentData())

    def _reset_view_layout(self, view_id):
        self.browser_state.reset_view(view_id)

    def _command_saved(self, operation):
        result, self._commands.result = self._commands.result, None
        if operation == "merge_entity_group":
            self._merge_finished(result)
        elif operation == "undo_entity_merges":
            self.nav_status.setText("Merge undone: the entities are separate again.")
        self.refresh()

    def _command_failed(self, error):
        self.nav_status.setText(error)

    def _source_title(self, source_id):
        return self._source_titles.get(source_id, source_id or "")

    def _widget_for(self, view_id):
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            if widget.property("intelligence_view_id") == view_id:
                return widget
        return None
