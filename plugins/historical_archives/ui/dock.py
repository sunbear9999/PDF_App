"""Historical Archives search, preview, saved-search, and import UI."""
from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListView,
    QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..models import ArchiveQuery, ArchiveResult
from ..services.query_planner import QueryPlanner
from .results_page import ArchiveResultsPage
from .search_page import ArchiveSearchPage
from .search_form import ProviderSearchForm


EMPTY_HELP = "Search old newspapers, books, photographs, maps, and public records.\n" \
    "Try: railroad wreck Marble · “Chair Creek” · cars left the track"


class HistoricalArchivesDock(QWidget):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        runtime = getattr(app_context, "plugin_historical_archives")
        self._api = runtime["api"]
        self._coordinator = runtime["coordinator"]
        self._importer = runtime["importer"]
        self._repository = runtime["repository"]
        self._cache = runtime["cache"]
        self._ctx = app_context
        self._job = None
        self._selected = None
        self._importing_result = None
        self._active_saved_id = None
        self._saved = []
        self._planner = QueryPlanner()
        self._build_ui()
        self._refresh_saved()

    def focus_search(self):
        self._tabs.setCurrentWidget(self._search_tab)
        self._query.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        self._tabs = QTabWidget()
        self._tabs.setAccessibleName("Historical Archives workflow")
        self._search_tab = self._build_search_tab()
        self._results_tab = self._build_results_tab()
        self._tabs.addTab(self._search_tab, "Search")
        self._tabs.addTab(self._results_tab, "Results and Import")
        root.addWidget(self._tabs, 1)
        self._status = QLabel(EMPTY_HELP)
        self._status.setWordWrap(True)
        self._status.setAccessibleName("Historical Archives status")
        root.addWidget(self._status)

    def _build_search_tab(self):
        page = ArchiveSearchPage(
            self._api.config,
            self._build_search_controls,
            self._build_variant_controls,
            self._build_saved_controls,
        )
        self._search_splitter = page.splitter
        return page

    def _build_results_tab(self):
        page = ArchiveResultsPage(
            self._api.config,
            lambda: self._tabs.setCurrentWidget(self._search_tab),
            self._cancel_search,
            self._selection_changed,
            self._open_original,
            self._import_selected,
        )
        self._results_splitter = page.splitter
        self._results_summary = page.summary
        self._results_cancel_btn = page.cancel_button
        self._model, self._table = page.model, page.table
        self._preview = page.preview
        self._preview_splitter = page.preview.splitter
        self._preview_title, self._image = page.preview.title, page.preview.image
        self._ocr, self._metadata = page.preview.ocr, page.preview.metadata
        self._open_btn, self._import_btn = (
            page.preview.open_button,
            page.preview.import_button,
        )
        self._import_status = page.preview.action_status
        return page

    def _build_search_controls(self, root):
        network_row = QHBoxLayout()
        self._network = QLabel()
        self._network.setWordWrap(True)
        network_row.addWidget(self._network, 1)
        self._network_btn = QPushButton()
        self._network_btn.clicked.connect(self._request_network_access)
        network_row.addWidget(self._network_btn)
        root.addLayout(network_row)
        self._refresh_network_label()
        search_row = QHBoxLayout()
        self._query = QLineEdit()
        self._query.setPlaceholderText("Search historical archives or paste an IIIF manifest URL")
        self._query.setAccessibleName("Historical archive search query")
        self._query.returnPressed.connect(self._start_search)
        self._query.textChanged.connect(self._update_variant_preview)
        search_row.addWidget(self._query, 1)
        self._search_btn = QPushButton("Search")
        self._search_btn.clicked.connect(self._start_search)
        search_row.addWidget(self._search_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_search)
        search_row.addWidget(self._cancel_btn)
        root.addLayout(search_row)
        self._search_form = ProviderSearchForm(
            self._coordinator.providers,
            int(self._api.config.get("max_results", 25)),
        )
        self._search_form.changed.connect(self._update_variant_preview)
        root.addWidget(self._search_form)
        self._advanced_toggle = self._search_form.advanced_toggle
        self._advanced = self._search_form.advanced
        self._date_from = self._search_form.date_from
        self._date_to = self._search_form.date_to
        self._location = self._search_form.location
        self._publication = self._search_form.publication
        self._exact = self._search_form.exact_phrase
        self._source_type = self._search_form.result_type
        self._max_results = self._search_form.max_results

    def _build_variant_controls(self, root):
        variant_heading = QLabel("OCR-aware query variants (uncheck to disable)")
        variant_heading.setObjectName("sectionHeading")
        root.addWidget(variant_heading)
        self._variant_model = QStandardItemModel(self)
        self._variants = QListView()
        self._variants.setModel(self._variant_model)
        self._variants.setAccessibleName("Generated historical search variants")
        root.addWidget(self._variants, 1)

    def _build_saved_controls(self, root):
        saved_row = QHBoxLayout()
        self._saved_combo = QComboBox()
        self._saved_combo.setAccessibleName("Saved historical searches")
        saved_row.addWidget(self._saved_combo, 1)
        save_btn = QPushButton("Save Search")
        save_btn.clicked.connect(self._save_search)
        saved_row.addWidget(save_btn)
        run_btn = QPushButton("Run Saved")
        run_btn.clicked.connect(self._run_saved)
        saved_row.addWidget(run_btn)
        delete_btn = QPushButton("Delete Saved")
        delete_btn.clicked.connect(self._delete_saved)
        saved_row.addWidget(delete_btn)
        clear_cache = QPushButton("Clear Cache")
        clear_cache.clicked.connect(self._clear_cache)
        saved_row.addWidget(clear_cache)
        root.addLayout(saved_row)

    def _build_query(self):
        query = self._search_form.build_query(self._query.text())
        query = self._planner.interpret(query).query
        variants = []
        for row in range(self._variant_model.rowCount()):
            item = self._variant_model.item(row)
            variants.append({
                "text": item.data(Qt.ItemDataRole.UserRole),
                "reason": item.data(Qt.ItemDataRole.UserRole + 1),
                "enabled": item.checkState() == Qt.CheckState.Checked,
            })
        if not variants:
            variants = [item.__dict__ for item in self._planner.expand(query)]
        return ArchiveQuery.from_dict({**query.to_dict(), "variants": variants})

    def _update_variant_preview(self):
        self._variant_model.clear()
        try:
            raw = self._search_form.build_query(self._query.text())
            interpretation = self._planner.interpret(raw)
            self._search_form.set_interpretation(interpretation.inferred)
            variants = self._planner.expand(interpretation.query)
        except ValueError as exc:
            self._search_form.set_interpretation(error=str(exc))
            return
        for variant in variants:
            item = QStandardItem(f"{variant.text} — {variant.reason}")
            item.setEditable(False)
            item.setCheckable(True)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(variant.text, Qt.ItemDataRole.UserRole)
            item.setData(variant.reason, Qt.ItemDataRole.UserRole + 1)
            self._variant_model.appendRow(item)

    def _start_search(self, _checked=False, *, saved_id=None):
        self._active_saved_id = saved_id
        self._refresh_network_label()
        if not self._api.network.is_allowed:
            self._status.setText(
                "The plugin is enabled, but its separate network permission is off. "
                "Use Grant network access above."
            )
            return
        try:
            query = self._build_query()
        except ValueError as exc:
            self._status.setText(f"Fix the Boolean query: {exc}")
            return
        provider_ids = self._search_form.selected_provider_ids()
        if not provider_ids:
            self._status.setText("Select at least one archive to search.")
            return
        if provider_ids == ["iiif"] and not query.raw_query.startswith("https://"):
            self._status.setText("IIIF search requires a complete public HTTPS manifest URL.")
            return
        self._model.reset_results([])
        self._selected = None
        self._import_btn.setEnabled(False)
        self._results_summary.setText("Searching selected archives…")
        self._tabs.setCurrentWidget(self._results_tab)
        self._set_running(True)
        self._status.setText("Searching selected archives…")
        self._job = self._api.tasks.run_background(
            self._coordinator.search,
            query,
            provider_ids,
            job_name="Historical archive search",
            on_done=self._search_done,
            on_error=self._task_error,
            on_progress=self._progress,
            on_partial=self._partial_result,
            pass_task_context=True,
        )

    def _partial_result(self, payload):
        if payload.get("type") == "result":
            self._model.append_result(ArchiveResult.from_dict(payload["result"]))
            count = self._model.rowCount()
            self._results_summary.setText(f"{count} result{'s' if count != 1 else ''}")
            if count == 1:
                self._table.selectRow(0)
        elif payload.get("type") == "provider_error":
            self._status.setText(
                f"{payload.get('provider')} failed; other archive results remain available."
            )

    def _search_done(self, batch):
        self._model.reset_results(batch.results)
        self._search_form.add_result_suggestions(batch.results)
        if self._active_saved_id:
            self._repository.mark_search_run(self._active_saved_id, len(batch.results))
        errors = "; ".join(f"{key}: {value}" for key, value in batch.errors.items())
        suffix = f" Provider issues: {errors}" if errors else ""
        self._status.setText(f"Found {len(batch.results)} results.{suffix}")
        count = len(batch.results)
        self._results_summary.setText(f"{count} result{'s' if count != 1 else ''}")
        self._tabs.setTabText(1, f"Results and Import ({count})")
        if count and self._table.currentIndex().row() < 0:
            self._table.selectRow(0)
        self._set_running(False)
        self._job = None

    def _selection_changed(self, current, _previous):
        result = self._model.result_at(current.row())
        self._selected = result
        if result is None:
            self._import_btn.setEnabled(False)
            self._preview.set_import_status(
                "Select a result to review it before importing."
            )
            return
        self._preview.display_result(result)
        self._open_btn.setEnabled(result.persistent_url.startswith("https://"))
        self._import_btn.setEnabled(True)
        self._import_btn.setText("Import to Project")
        self._preview.set_import_status(
            "Ready to import this source into the open project."
        )
        self._load_thumbnail(result)

    def _load_thumbnail(self, result):
        self._image.setText("Loading preview…")
        if not result.thumbnail_url:
            self._image.setText("No page thumbnail is available.")
            return
        def fetch(task_context=None):
            return self._api.network.get_bytes(
                result.thumbnail_url,
                max_bytes=5 * 1024 * 1024,
                task_context=task_context,
                allow_unlisted_host=result.provider_id == "iiif",
            ).content
        self._api.tasks.run_background(
            fetch,
            job_name="Archive page preview",
            on_done=lambda data, key=result.provider_key: self._show_thumbnail(data, key),
            on_error=lambda _error: self._image.setText("Preview unavailable."),
            pass_task_context=True,
        )

    def _show_thumbnail(self, data, provider_key):
        if not self._selected or self._selected.provider_key != provider_key:
            return
        self._preview.display_thumbnail(data)

    def _import_selected(self):
        if not self._selected:
            self._preview.set_import_status(
                "Select a result before importing.", "error"
            )
            return
        if not getattr(self._api.project_manager, "project_filepath", None):
            message = "Open or create a Papyrus project before importing this source."
            self._preview.set_import_status(message, "error")
            self._status.setText(message)
            return
        known_sizes = [
            asset.size for asset in self._selected.assets
            if asset.kind in {"pdf", "image"} and asset.size
        ]
        threshold = 50 * 1024 * 1024
        if known_sizes and min(known_sizes) >= threshold:
            size_mb = min(known_sizes) / (1024 * 1024)
            answer = QMessageBox.question(
                self,
                "Download Large Archive Source",
                f"The smallest known source asset is {size_mb:.1f} MB. Continue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._importing_result = self._selected
        self._import_btn.setEnabled(False)
        self._import_btn.setText("Importing…")
        self._table.setEnabled(False)
        self._tabs.setTabEnabled(0, False)
        message = "Starting import: resolving the downloadable archive file…"
        self._preview.set_import_status(message, "running")
        self._status.setText(message)
        self._job = self._api.tasks.run_background(
            self._importer.import_result,
            self._importing_result,
            job_name="Import historical source",
            on_done=self._import_done,
            on_error=self._import_error,
            on_progress=self._progress,
            pass_task_context=True,
        )

    def _import_done(self, imported):
        if self._importing_result:
            self._model.mark_imported(self._importing_result.provider_key)
        warnings = list(imported.get("warnings") or [])
        message = (
            "Reused the existing project source."
            if imported.get("reused")
            else "Import complete — source, citation, provenance, and available OCR added."
        )
        if imported.get("path"):
            try:
                self._api.sources.open(path=imported["path"])
            except Exception as exc:
                warnings.append(f"The imported source could not be opened automatically: {exc}")
        if warnings:
            message += " " + " ".join(warnings)
        self._finish_import_ui(message, "warning" if warnings else "success")

    def _import_error(self, error):
        self._finish_import_ui(f"Import failed: {error}", "error", retry=True)

    def _finish_import_ui(self, message, state, retry=False):
        self._table.setEnabled(True)
        self._tabs.setTabEnabled(0, True)
        self._import_btn.setEnabled(retry)
        self._import_btn.setText("Retry Import" if retry else "Imported ✓")
        self._preview.set_import_status(message, state)
        self._status.setText(message)
        self._importing_result = None
        self._job = None

    def _save_search(self):
        if not getattr(self._api.project_manager, "project_filepath", None):
            self._status.setText("Open a project before saving a project-scoped search.")
            return
        query = self._build_query()
        if not query.original_query:
            return
        name, ok = QInputDialog.getText(self, "Save Historical Search", "Search name:", text=query.original_query)
        if ok:
            self._repository.save_search(name, query)
            self._refresh_saved()

    def _run_saved(self):
        index = self._saved_combo.currentIndex()
        if not 0 <= index < len(self._saved):
            return
        query = self._saved[index]["query"]
        self._query.setText(query.raw_query or query.original_query)
        self._search_form.load_query(query)
        self._start_search(saved_id=self._saved[index]["id"])

    def _delete_saved(self):
        index = self._saved_combo.currentIndex()
        if not 0 <= index < len(self._saved):
            return
        answer = QMessageBox.question(self, "Delete Saved Search", "Delete this saved search?")
        if answer == QMessageBox.StandardButton.Yes:
            self._repository.delete_search(self._saved[index]["id"])
            self._refresh_saved()

    def _refresh_saved(self):
        self._saved_combo.clear()
        if not getattr(self._api.project_manager, "project_filepath", None):
            self._saved = []
            return
        try:
            self._saved = self._repository.list_searches()
        except RuntimeError:
            self._saved = []
        self._saved_combo.addItems([item["name"] for item in self._saved])

    def _clear_cache(self):
        answer = QMessageBox.question(
            self,
            "Clear Historical Archives Cache",
            "Delete cached archive search responses and metadata?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._cache.clear()
        self._status.setText("Historical Archives metadata cache cleared.")

    def _open_original(self):
        if self._selected and self._selected.persistent_url.startswith("https://"):
            QDesktopServices.openUrl(QUrl(self._selected.persistent_url))

    def _cancel_search(self):
        if self._job:
            self._job.kill()
        self._status.setText("Cancelling…")
        self._set_running(False)

    def _progress(self, current, total, message):
        text = f"{message} ({current}/{total})" if total else message
        self._status.setText(text)
        if self._importing_result is not None:
            self._preview.set_import_status(text, "running")

    def _task_error(self, error):
        self._status.setText(str(error))
        self._results_summary.setText("Search failed")
        self._set_running(False)
        self._job = None

    def _set_running(self, running):
        self._search_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        self._results_cancel_btn.setEnabled(running)

    def _refresh_network_label(self):
        if self._api.network.is_allowed:
            self._network.setText(
                "Online access granted. Searches send only the terms and filters shown here "
                "to the selected public archives."
            )
            self._network_btn.setText("Revoke access…")
        else:
            self._network.setText(
                "Plugin enabled; network permission off. Archive requests remain blocked."
            )
            self._network_btn.setText("Grant network access…")

    def _request_network_access(self):
        core = getattr(self._ctx, "core", None)
        if core is None:
            self._status.setText("Open Plugins → Plugin Manager to change network access.")
            return
        from gui.components.plugin_network_access import set_plugin_network_permission
        desired = not self._api.network.is_allowed
        changed = set_plugin_network_permission(
            self,
            core,
            "historical_archives",
            self._api.network.allowed_hosts,
            desired,
        )
        self._refresh_network_label()
        if changed:
            self._status.setText(
                "Network access granted." if desired else "Network access revoked."
            )
