# gui/components/document_explorer.py
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QComboBox, QMenu,
                             QMessageBox, QInputDialog, QSizePolicy, QPushButton,
                             QFileDialog, QToolButton)
from PySide6.QtCore import Qt, QThread, Signal

REFRESH_DELAY_MS = 150   # many sources added at once: one list refresh after the burst
VIEWER_STOP_TIMEOUT_MS = 1000
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload, SourceEvent
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload, ProjectIntent, ProjectPayload
from gui.managers.dialog_manager import exec_as_modal, get_for_widget
from gui.components.media_export_actions import MediaExportActions
from gui.components.document_source_tree import (
    DocumentSourceTree, DocumentTreeItem, tag_folder_icon,
)
from gui.components.tag_tree import build_tag_folders


class _SourceRemovalWorker(QThread):
    removed = Signal(object)
    failed = Signal(str)

    def __init__(self, project_manager, payload, parent=None):
        super().__init__(parent)
        self.pm, self.payload = project_manager, payload

    def run(self):
        try:
            if not self.pm.remove_source_data(
                self.payload["path"], self.payload.get("source_id")
            ):
                raise RuntimeError("The project database rejected the source removal.")
            self.removed.emit(self.payload)
        except Exception as exc:
            self.failed.emit(str(exc))

class ElidedLabel(QLabel):
    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(1, super().minimumSizeHint().height())

    def sizeHint(self):
        from PySide6.QtCore import QSize
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + 10, super().sizeHint().height())

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        metrics = self.fontMetrics()
        elided = metrics.elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)

class DocumentExplorer(QWidget):
    SOURCE_ID_ROLE = Qt.ItemDataRole.UserRole
    PATH_ROLE = Qt.ItemDataRole.UserRole + 1
    SOURCE_TYPE_ROLE = Qt.ItemDataRole.UserRole + 2
    ITEM_KIND_ROLE = Qt.ItemDataRole.UserRole + 3

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.pm = app_context.project_manager
        self.bus = EventBus.get_instance()
        self._remove_workers = set()
        self._removing_paths = set()

        self._build_ui()
        self.export_actions = MediaExportActions(self)

        # Subscribe to global events so the list updates automatically
        self.bus.project_loaded.connect(self._on_project_loaded)
        self.bus.document_added.connect(self._on_pdf_changed)
        self.bus.pdf_renamed.connect(self._on_pdf_changed)
        self.bus.pdf_removed.connect(self._on_pdf_changed)
        self.bus.source_added.connect(self._on_source_changed)
        self.bus.source_renamed.connect(self._on_source_changed)
        self.bus.source_removed.connect(self._on_source_changed)
        self.bus.tag_data_updated.connect(self._on_tag_changed)

    def _on_project_loaded(self, event: ProjectEvent, payload: ProjectEventPayload):
        if event == ProjectEvent.LOADED:
            enabled = self.pm.get_metadata("document_explorer_tags_as_folders", "0") == "1"
            self.tag_folder_toggle.blockSignals(True)
            self.tag_folder_toggle.setChecked(enabled)
            self.tag_folder_toggle.blockSignals(False)
            self.doc_tag_filter.setEnabled(not enabled)
            self.refresh_list()

    def _on_pdf_changed(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event in {DocumentEvent.DOCUMENT_ADDED, DocumentEvent.PDF_RENAMED, DocumentEvent.PDF_REMOVED}:
            self.schedule_refresh()

    def _on_source_changed(self, event: SourceEvent, payload: DocumentEventPayload):
        if event in {SourceEvent.ADDED, SourceEvent.RENAMED, SourceEvent.REMOVED}:
            self.schedule_refresh()

    def _on_tag_changed(self, event, _payload):
        from core.events.domains.metadata_events import TagEvent
        if event in {TagEvent.ASSIGNMENTS_CHANGED, TagEvent.ALL_TAGS}:
            self.schedule_refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        controls = QHBoxLayout()
        self.doc_tag_filter = QComboBox()
        self.doc_tag_filter.setStyleSheet("padding: 4px; margin: 4px; font-weight: bold;")
        self.doc_tag_filter.addItem("All Tags", "ALL_TAGS")
        self.doc_tag_filter.currentIndexChanged.connect(self.refresh_list)
        controls.addWidget(self.doc_tag_filter, 1)
        self.tag_folder_toggle = QToolButton()
        self.tag_folder_toggle.setText("Tags as folders")
        self.tag_folder_toggle.setCheckable(True)
        self.tag_folder_toggle.setToolTip("Group sources into collapsible, inferred tag folders")
        self.tag_folder_toggle.toggled.connect(self._toggle_tag_folders)
        controls.addWidget(self.tag_folder_toggle)
        layout.addLayout(controls)

        self.doc_list = DocumentSourceTree()
        self.doc_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        from PySide6.QtWidgets import QAbstractItemView
        self.doc_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.doc_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.doc_list.customContextMenuRequested.connect(self._on_context_menu)
        self.doc_list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.doc_list)

        self.add_pdf_btn = QPushButton("Add PDF")
        self.add_pdf_btn.setToolTip("Add PDFs to the current project")
        self.add_pdf_btn.clicked.connect(self._add_pdf)
        layout.addWidget(self.add_pdf_btn)

    def _icon_for_source_type(self, source_type: str) -> str:
        registry = getattr(self._ctx, "source_format_registry", None)
        spec = registry.get(source_type) if registry else None
        return spec.icon if spec else "📄"

    def _file_dialog_filter(self) -> str:
        """Build the Open Files filter string from every registered source
        format, instead of a hardcoded pdf/video-only extension list."""
        registry = getattr(self._ctx, "source_format_registry", None)
        if registry is None:
            return "Sources (*.pdf *.mp4 *.mov *.mkv *.webm *.avi *.m4v)"
        specs = registry.all()
        all_exts = sorted({ext for spec in specs for ext in spec.extensions})
        parts = [f"Sources ({' '.join(f'*{e}' for e in all_exts)})"]
        for spec in specs:
            if spec.extensions:
                parts.append(f"{spec.label} ({' '.join(f'*{e}' for e in spec.extensions)})")
        return ";;".join(parts)

    def _add_pdf(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Sources",
            "",
            self._file_dialog_filter(),
        )
        if paths:
            from gui.components.dialogs.processing_import_dialog import ProcessingImportDialog
            policy = getattr(self._ctx, "processing_policy_service", None)
            default = (
                policy.get_project_settings().get("default_processing_mode", "balanced")
                if policy else "balanced"
            )
            dialog = ProcessingImportDialog(default, self, app_context=self._ctx, paths=paths)
            if exec_as_modal(dialog):
                self.bus.document_action_requested.emit(
                    DocumentIntent.ADD_FILES,
                    DocumentPayload(
                        paths=paths,
                        context={"processing_selection": dialog.selection()},
                    ),
                )

    def refresh_tag_filter(self):
        """Refreshes the available tags in the combo box."""
        current = self.doc_tag_filter.currentData()
        self.doc_tag_filter.blockSignals(True)
        self.doc_tag_filter.clear()
        self.doc_tag_filter.addItem("All Tags", "ALL_TAGS")

        self._available_tags = self.pm.get_all_tags()
        for t in self._available_tags:
            if t.get("name"):
                self.doc_tag_filter.addItem(t.get("name"), t.get("name"))

        index = self.doc_tag_filter.findData(current)
        if index >= 0: self.doc_tag_filter.setCurrentIndex(index)
        self.doc_tag_filter.blockSignals(False)

    def schedule_refresh(self, *_):
        """Many sources added at once refresh the list once, after the burst."""
        timer = getattr(self, "_refresh_timer", None)
        if timer is None:
            from PySide6.QtCore import QTimer
            timer = self._refresh_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(REFRESH_DELAY_MS)
            timer.timeout.connect(self.refresh_list)
        timer.start()

    def refresh_list(self):
        """Rebuilds the visual list of PDFs."""
        self.refresh_tag_filter()
        self.doc_list.blockSignals(True)
        self.doc_list.clear()

        selected_tag = (
            "ALL_TAGS" if self.tag_folder_toggle.isChecked()
            else self.doc_tag_filter.currentData()
        )

        source_records = {s.get("path"): s for s in self.pm.list_sources()} if hasattr(self.pm, "list_sources") else {}
        tag_map = self.pm.get_doc_tag_map() if hasattr(self.pm, "get_doc_tag_map") else {}
        sources = self.pm.list_source_entities() if hasattr(self.pm, "list_source_entities") else []
        if not sources:
            sources = [self._legacy_source(path) for path in self.pm.pdfs]

        records = []
        for source in sources:
            if source.state.get("is_removed"):
                continue
            path = source.properties.get("path") or source.origin_id
            if not path:
                continue
            source_type = source.properties.get("source_type") or source_records.get(path, {}).get("source_type", "pdf")
            doc_tags = tag_map.get(path, [])
            if selected_tag and selected_tag != "ALL_TAGS":
                if selected_tag not in [t.get("name") for t in doc_tags]:
                    continue

            records.append({
                "source_id": source.id, "path": path, "source_type": source_type,
                "title": source.properties.get("title") or os.path.basename(path),
                "tags": doc_tags,
            })

        if self.tag_folder_toggle.isChecked():
            self._add_tag_folders(build_tag_folders(records, self._available_tags))
        else:
            for record in records:
                self._add_source_item(self.doc_list, record)

        self.doc_list.blockSignals(False)

    def _add_tag_folders(self, folders, parent=None):
        parent = parent or self.doc_list
        for folder in folders:
            item = DocumentTreeItem(parent, [f"{folder.name} ({self._folder_count(folder)})"])
            item.setIcon(0, tag_folder_icon(folder.color))
            item.setData(0, self.ITEM_KIND_ROLE, "folder")
            item.setToolTip(0, "Collapsible tag category")
            for child in folder.children:
                self._add_tag_folders([child], item)
            for source in folder.sources:
                self._add_source_item(item, source)

    def _folder_count(self, folder):
        return len(folder.sources) + sum(self._folder_count(child) for child in folder.children)

    def _add_source_item(self, parent, source):
        icon = self._icon_for_source_type(source["source_type"])
        item = DocumentTreeItem(parent, [f"{icon} {source['title']}"])
        tag_names = [tag.get("name", "") for tag in source.get("tags", []) if tag.get("name")]
        if tag_names:
            item.setToolTip(0, "Tags: " + ", ".join(tag_names))
        item.setData(0, self.ITEM_KIND_ROLE, "source")
        item.setData(0, self.SOURCE_ID_ROLE, source["source_id"])
        item.setData(0, self.PATH_ROLE, source["path"])
        item.setData(0, self.SOURCE_TYPE_ROLE, source["source_type"])

    def _toggle_tag_folders(self, checked):
        self.doc_tag_filter.setEnabled(not checked)
        self.pm.set_metadata("document_explorer_tags_as_folders", "1" if checked else "0")
        self.refresh_list()

    def showEvent(self, event):
        super().showEvent(event)
        self._register_tag_folder_action()

    def _register_tag_folder_action(self):
        keys = getattr(self._ctx, "keybinding_registry", None)
        if keys and not getattr(self, "_tag_folder_shortcut", None):
            self._tag_folder_shortcut = keys.bind(
                "document_explorer.toggle_tag_folders",
                self.tag_folder_toggle.toggle,
                self,
            )
        registry = getattr(self._ctx, "action_registry", None)
        if registry and registry.get("document_explorer.toggle_tag_folders") is None:
            import weakref
            from gui.registry.action_spec import ActionSpec
            owner = weakref.ref(self)

            def toggle():
                explorer = owner()
                if explorer is not None:
                    explorer.tag_folder_toggle.toggle()

            spec = ActionSpec(
                action_id="document_explorer.toggle_tag_folders",
                label="Toggle Tag Folders",
                tooltip="Show Document Explorer sources in collapsible tag folders",
                callback=toggle,
                mounts=["command_palette"],
            )
            registry.register(spec)
            self._tag_folder_action = spec
            self.destroyed.connect(
                lambda *_args, target=registry, current=spec: target.unregister(
                    "document_explorer.toggle_tag_folders", current
                )
            )

    def _on_context_menu(self, pos):
        item_at_pos = self.doc_list.itemAt(pos)
        if not item_at_pos or item_at_pos.data(0, self.ITEM_KIND_ROLE) != "source": return

        selected_items = [
            item for item in self.doc_list.selectedItems()
            if item.data(0, self.ITEM_KIND_ROLE) == "source"
        ]
        if item_at_pos not in selected_items:
            self.doc_list.clearSelection()
            item_at_pos.setSelected(True)
            selected_items = [item_at_pos]

        menu = QMenu(self)

        global_pos = self.doc_list.viewport().mapToGlobal(pos)

        # BATCH MODE
        if len(selected_items) > 1:
            mass_tag_menu = menu.addMenu(f"🏷️ Mass Assign Tag to {len(selected_items)} Docs")
            tags = self.pm.get_all_tags()
            if not tags: mass_tag_menu.addAction("No tags created yet").setEnabled(False)
            else:
                for t in tags:
                    action = mass_tag_menu.addAction(t.get("name"))
                    action.triggered.connect(lambda checked=False, t_id=t.get("id"): self._mass_assign_tag(selected_items, t_id))
            paths = [i.data(self.PATH_ROLE) for i in selected_items]
            self._inject_plugin_doc_items(menu, paths)
            self._inject_dock_actions_doc(menu, paths)
            menu.exec(global_pos)

        # SINGLE MODE
        else:
            doc_path = item_at_pos.data(self.PATH_ROLE)
            source_type = item_at_pos.data(self.SOURCE_TYPE_ROLE) or "pdf"
            manage_tags_action = menu.addAction("🏷️ Manage Tags for This Document")
            menu.addSeparator()
            intelligence_actions = self._add_intelligence_menu(menu, doc_path)
            metrics_action = menu.addAction("📊 View Source Metrics")
            workspace_action = menu.addAction("📌 Add to Workspace")
            menu.addSeparator()
            rename_action = menu.addAction("✏️ Rename Source")
            remove_action = menu.addAction("🗑️ Remove Source from Project")
            extract_action = menu.addAction("✂️ Extract Pages to New PDF") if source_type == "pdf" else None
            if source_type in {"video", "audio"}:
                self.export_actions.append_to_menu(menu, (doc_path, item_at_pos.data(self.SOURCE_ID_ROLE), source_type))
            self._inject_plugin_doc_items(menu, [doc_path])
            self._inject_dock_actions_doc(menu, [doc_path])

            chosen = menu.exec(global_pos)

            if chosen == metrics_action:
                self._open_source_metrics(doc_path)
            elif chosen in intelligence_actions:
                self._handle_intelligence_action(
                    doc_path, intelligence_actions[chosen],
                    source_id=item_at_pos.data(self.SOURCE_ID_ROLE),
                    source_type=source_type,
                )
            elif chosen == workspace_action:
                self._add_to_workspace(doc_path)
            elif chosen == manage_tags_action:
                from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
                _dlg = TagAssignmentDialog(doc_path, "doc", self)
                _dlg.accepted.connect(self.refresh_list)
                _dm = get_for_widget(self)
                if _dm:
                    _dm.show_instance(_dlg)
                else:
                    if exec_as_modal(_dlg):
                        self.refresh_list()
            elif chosen == rename_action:
                self._rename_pdf(doc_path)
            elif chosen == remove_action:
                self._remove_pdf(
                    doc_path, source_id=item_at_pos.data(self.SOURCE_ID_ROLE),
                    source_type=source_type,
                )
            elif extract_action is not None and chosen == extract_action:
                from gui.components.dialogs.extract_pages_dialog import ExtractPagesDialog
                _dlg2 = ExtractPagesDialog(doc_path, self.pm, self)
                _dlg2.accepted.connect(self.refresh_list)
                _dm2 = get_for_widget(self)
                if _dm2:
                    _dm2.show_instance(_dlg2)
                else:
                    if exec_as_modal(_dlg2):
                        self.refresh_list()
        menu.deleteLater()

    def save_selected_transcript(self):
        items = [
            item for item in self.doc_list.selectedItems()
            if item.data(self.ITEM_KIND_ROLE) == "source"
        ]
        if len(items) == 1:
            item = items[0]
            self.export_actions.save_source(item.data(self.PATH_ROLE), item.data(self.SOURCE_ID_ROLE),
                                            item.data(self.SOURCE_TYPE_ROLE))

    def _add_intelligence_menu(self, menu: QMenu, doc_path: str) -> dict:
        svc = getattr(self._ctx, "source_intelligence_service", None)
        actions = {}
        intel_menu = menu.addMenu("🧠 Intelligence")
        if not svc:
            intel_menu.addAction("Intelligence service unavailable").setEnabled(False)
            return actions
        run_missing = intel_menu.addAction("Run missing passes")
        rerun_stale = intel_menu.addAction("Re-run stale passes")
        pause = intel_menu.addAction("Pause processing")
        resume = intel_menu.addAction("Resume processing")
        cancel = intel_menu.addAction("Cancel queued/running passes")
        change_preset = intel_menu.addAction("Change preset…")
        inspect = intel_menu.addAction("Inspect outputs…")
        archive_derived = intel_menu.addAction("Archive derived intelligence")
        purge_derived = intel_menu.addAction("Permanently purge derived intelligence…")
        actions[run_missing] = ("run_missing", None)
        actions[rerun_stale] = ("rerun_stale", None)
        actions[pause] = ("pause", None)
        actions[resume] = ("resume", None)
        actions[cancel] = ("cancel", None)
        actions[change_preset] = ("change_preset", None)
        actions[inspect] = ("inspect", None)
        actions[archive_derived] = ("archive_derived", None)
        actions[purge_derived] = ("purge_derived", None)
        return actions

    def _handle_intelligence_action(
        self, doc_path: str, action_spec, *, source_id=None, source_type="pdf"
    ) -> None:
        svc = getattr(self._ctx, "source_intelligence_service", None)
        if not svc or not action_spec:
            return
        kind, stage = action_spec
        source_id = source_id or doc_path
        source = {
            "id": source_id, "path": doc_path, "source_type": source_type,
        }
        if kind == "stage":
            svc.run_stage(source_id, stage)
        elif kind == "run_missing":
            svc.run_missing_background(source_id)
        elif kind == "rerun_stale":
            svc.rerun_stale_background(source_id)
        elif kind == "pause":
            svc.pause_source(source_id)
        elif kind == "resume":
            svc.resume_source(source_id)
        elif kind == "cancel":
            svc.cancel_source_jobs(source_id)
        elif kind == "change_preset":
            current = (
                svc.policy_service.processing_mode_for_source(source)
                if source and svc.policy_service else "balanced"
            )
            labels = ["minimal", "balanced", "deep", "manual"]
            value, ok = QInputDialog.getItem(
                self, "Processing preset", "Preset", labels,
                max(0, labels.index(current) if current in labels else 1), False,
            )
            if ok:
                svc.set_source_processing_mode(source_id, value)
        elif kind == "inspect":
            manager = getattr(self._ctx, "dock_manager", None)
            if manager:
                manager.spawn("intelligence")
        elif kind == "archive_derived":
            svc.archive_derived_background(source_id)
        elif kind == "purge_derived":
            answer = QMessageBox.warning(
                self,
                "Permanently purge intelligence?",
                "This permanently removes derived artifacts and cannot be undone. "
                "Original sources and annotations are preserved.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Yes:
                svc.delete_derived_background(source_id)

    def _inject_plugin_doc_items(self, menu: "QMenu", doc_paths: list) -> None:
        """Append plugin-registered document_list context menu actions."""
        action_registry = getattr(self._ctx, "action_registry", None)
        if not action_registry:
            return
        from gui.registry.context_menu_registry import ContextMenuContext
        ctx = ContextMenuContext(
            context_type="document_list:item",
            selected_ids=doc_paths,
            payload={"paths": doc_paths},
            event_bus=self.bus,
        )
        specs = list(action_registry.iter_mount("context_menu:document_list:item"))
        if not specs:
            return
        menu.addSeparator()
        for spec in specs:
            if spec.separator_before:
                menu.addSeparator()
            label = f"{spec.icon} {spec.label}".strip() if spec.icon else spec.label
            action = menu.addAction(label)
            if spec.tooltip:
                action.setToolTip(spec.tooltip)
            if spec.callback:
                action.triggered.connect(lambda checked=False, cb=spec.callback, c=ctx: cb(c))

    def _inject_dock_actions_doc(self, menu: "QMenu", doc_paths: list) -> None:
        """Append DockActionSpec actions for DOCUMENT_LIST_ITEM mount."""
        from core.engine.dock_mounts import DOCUMENT_LIST_ITEM
        from gui.components.dock_action_mixin import inject_dock_actions_into_menu
        source_path = doc_paths[0] if doc_paths else None
        inject_dock_actions_into_menu(
            menu,
            DOCUMENT_LIST_ITEM,
            dock_id="document_list",
            zone="context_menu:item",
            app_context=self._ctx,
            source_path=source_path,
            extra={"paths": doc_paths},
        )

    def _open_source_metrics(self, doc_path: str) -> None:
        from core.events.domains.evaluation_events import SourceEvalEvent, SourceEvalIntent, SourceEvalPayload
        eval_svc = getattr(self._ctx, "source_evaluation_service", None)
        if not eval_svc:
            return

        score = eval_svc.get_score_for_path(doc_path)
        ledger = eval_svc.get_ledger_for_path(doc_path)

        if score is not None and ledger is not None:
            self._show_metrics_dialog(doc_path, score, ledger)
            return

        # Not evaluated yet — request evaluation, then open dialog on completion
        self.bus.source_eval_action_requested.emit(
            SourceEvalIntent.RUN_EVALUATION,
            SourceEvalPayload(pdf_path=doc_path),
        )
        self.bus.status_message_requested.emit("Evaluating source… please wait.", 4000)

        def _on_complete(event, payload):
            if event == SourceEvalEvent.EVALUATION_COMPLETE and getattr(payload, "pdf_path", "") == doc_path:
                self.bus.source_eval_state_changed.disconnect(_on_complete)
                if payload.score is not None:
                    self._show_metrics_dialog(
                        doc_path, payload.score, payload.ledger,
                        payload.is_retracted, payload.needs_manual_review,
                    )

        self.bus.source_eval_state_changed.connect(_on_complete)

    def _show_metrics_dialog(
        self,
        doc_path: str,
        score: int,
        ledger: list,
        is_retracted: bool = False,
        needs_review: bool = False,
    ) -> None:
        from gui.components.dialogs.source_metrics_dialog import SourceMetricsDialog
        theme = None
        tm = getattr(self._ctx, "theme_manager", None)
        if tm:
            try:
                theme = tm.get_theme()
            except Exception:
                pass
        dlg = SourceMetricsDialog(
            score=score,
            ledger=ledger,
            pdf_path=doc_path,
            is_retracted=is_retracted,
            needs_manual_review=needs_review,
            theme=theme,
            source_intelligence_service=getattr(self._ctx, "source_intelligence_service", None),
            parent=self,
        )
        from gui.managers.dialog_manager import get_for_widget
        dm = get_for_widget(self)
        if dm:
            dm.show_instance(dlg)
        else:
            dlg.show()

    def _add_to_workspace(self, doc_path: str) -> None:
        """Add a project document to the workspace as a source node.

        Metrics (score, highlights, citation count) are stored in entity_properties
        so the ontology render_blocks system displays them as metric badges —
        the same mechanism claim nodes use for evidence counts and confidence.
        Relations to cited sources already present in the workspace are included
        so edges are drawn automatically.
        """
        from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload

        db = getattr(self.pm, "db_graph", None)
        if not db:
            return

        source_entity = db.get_source_entity_by_path(doc_path)
        if not source_entity:
            self.bus.status_message_requested.emit(
                "Run discovery extraction first to index this document.", 4000
            )
            return

        # Count highlights for this document
        highlights_count = sum(
            1 for h in (self.pm.get_highlights() or {}).values()
            if h.get("doc_id") == doc_path
        )

        # Find all source entities this document cites
        relations = db.get_relations_for_entity(
            source_entity.id, as_source=True, as_target=False
        )
        cited_ids = list({
            rel.target_id for rel in relations
            if getattr(rel, "relation_type", "") == "relation.references"
        })

        # Score and ledger from the evaluation service
        eval_svc = getattr(self._ctx, "source_evaluation_service", None)
        score = eval_svc.get_score_for_path(doc_path) if eval_svc else None
        ledger = eval_svc.get_ledger_for_path(doc_path) if eval_svc else None

        # Node display text: title + author/year only — metrics appear as badges
        title = (source_entity.properties.get("title") or os.path.basename(doc_path))[:80]
        authors = str(source_entity.properties.get("authors") or "")[:50]
        year = str(source_entity.properties.get("year") or "")
        by_line = f"{authors} {year}".strip()
        label = f"{title}\n{by_line}" if by_line else title

        # Entity properties — scalar values only for IMPORT_GRAPH serialisation.
        # Metrics go in here so render_blocks can pick them up as metric badges.
        props = {
            k: v for k, v in source_entity.properties.items()
            if isinstance(v, (str, int, float, bool))
        }
        props.update({
            "pdf_path": doc_path,
            "path": doc_path,
            "in_project": True,
            "highlights_count": highlights_count,
            "citations_count": len(cited_ids),
        })
        if score is not None:
            props["source_score"] = score
        if ledger:
            props["score_ledger"] = ledger

        entity = {"id": source_entity.id, "type": "entity.source", "text": label, **props}

        # Relations to cited sources — IMPORT_GRAPH now resolves these against
        # existing workspace nodes, so edges to already-present sources are drawn.
        relations_payload = [
            {"source": source_entity.id, "target": cid,
             "type": "relation.references", "label": "cites"}
            for cid in cited_ids
        ]

        self.bus.workspace_action_requested.emit(
            WorkspaceIntent.IMPORT_GRAPH,
            WorkspacePayload(extra={"graph": {
                "entities": [entity],
                "relations": relations_payload,
            }}),
        )
        self.bus.status_message_requested.emit(
            f"Added '{os.path.basename(doc_path)}' to workspace.", 3000
        )

    def _mass_assign_tag(self, selected_items, tag_id):
        for item in selected_items:
            self.pm.assign_tag_to_doc(item.data(self.PATH_ROLE), tag_id)
        self.refresh_list()

    def _rename_pdf(self, old_path):
        self.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload())
        old_name = os.path.basename(old_path)
        source_type = self.pm.get_source_type(old_path) if hasattr(self.pm, "get_source_type") else "pdf"
        new_name, ok = QInputDialog.getText(self, "Rename Source", "Enter new name for the source:", text=old_name)

        if not ok or not new_name.strip() or new_name == old_name: return
        if "." not in os.path.basename(new_name):
            # Always keep the source's real extension — was special-cased to
            # ".pdf" for source_type == "pdf", which happened to be a no-op
            # for pdf files (same result) but was wrong in spirit; every
            # other format already fell through to the real extension.
            new_name += os.path.splitext(old_name)[1]
        new_path = os.path.join(os.path.dirname(old_path), new_name)

        if os.path.exists(new_path):
            QMessageBox.warning(self, "Error", "A file with that name already exists in this folder.")
            return

        if self.pm.rename_source(old_path, new_path) if hasattr(self.pm, "rename_source") else self.pm.rename_pdf(old_path, new_path):
            old_source = self.pm.get_source_entity_by_path(old_path) if hasattr(self.pm, "get_source_entity_by_path") else None
            new_source = self.pm.get_source_entity_by_path(new_path) if hasattr(self.pm, "get_source_entity_by_path") else None
            # Shout to the Event Bus so Workspaces/Chromadb can update!
            self.bus.pdf_renamed.emit(
                DocumentEvent.PDF_RENAMED,
                DocumentEventPayload(
                    old_path=old_path,
                    new_path=new_path,
                    old_source_id=old_source.id if old_source else None,
                    new_source_id=new_source.id if new_source else None,
                ),
            )
            self.bus.source_renamed.emit(
                SourceEvent.RENAMED,
                DocumentEventPayload(
                    old_path=old_path,
                    new_path=new_path,
                    old_source_id=old_source.id if old_source else None,
                    new_source_id=new_source.id if new_source else None,
                    source_type=source_type,
                ),
            )

            viewer = getattr(self._ctx, 'viewer', None)
            current = getattr(viewer, 'pdf_path', None) if viewer else None
            if current in (old_path, new_path):
                self.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=new_path, source_id=new_source.id if new_source else None, source_type=source_type))
    def _on_item_clicked(self, item, _column=0):
        if item.data(self.ITEM_KIND_ROLE) != "source":
            item.setExpanded(not item.isExpanded())
            return
        self.bus.document_action_requested.emit(
            DocumentIntent.OPEN,
            DocumentPayload(source_id=item.data(self.SOURCE_ID_ROLE), path=item.data(self.PATH_ROLE), source_type=item.data(self.SOURCE_TYPE_ROLE)),
        )
    def _remove_pdf(self, doc_path, *, source_id=None, source_type="pdf"):
        if doc_path in self._removing_paths:
            return
        reply = QMessageBox.question(self, "Remove Source", f"Remove '{os.path.basename(doc_path)}' from the project?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return
        if not self._prepare_viewer_for_removal(doc_path):
            return
        self.pm.prepare_source_removal(doc_path)
        payload = {
            "path": doc_path, "source_id": source_id, "source_type": source_type,
        }
        worker = _SourceRemovalWorker(self.pm, payload, self)
        worker.removed.connect(self._source_removal_finished, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(
            lambda error, path=doc_path: self._source_removal_failed(path, error),
            Qt.ConnectionType.QueuedConnection,
        )
        worker.finished.connect(
            lambda current=worker: self._discard_remove_worker(current),
            Qt.ConnectionType.QueuedConnection,
        )
        self._removing_paths.add(doc_path)
        self._remove_workers.add(worker)
        worker.start()

    def _prepare_viewer_for_removal(self, path):
        viewer = getattr(self._ctx, "viewer", None)
        if not viewer or getattr(viewer, "pdf_path", None) != path:
            return True
        worker = getattr(viewer, "worker", None)
        if worker and worker.isRunning():
            worker.stop()
            if not worker.wait(VIEWER_STOP_TIMEOUT_MS):
                QMessageBox.warning(
                    self, "Source is still closing",
                    "The viewer is still finishing a page. Try removing the source again in a moment.",
                )
                return False
        if getattr(viewer, "scene", None):
            viewer.scene.clear()
        viewer.doc = None
        return True

    def _source_removal_finished(self, payload):
        path = payload["path"]
        self._removing_paths.discard(path)
        event_payload = DocumentEventPayload(
            path=path, source_id=payload.get("source_id"),
            source_type=payload.get("source_type") or "pdf",
        )
        self.bus.pdf_removed.emit(DocumentEvent.PDF_REMOVED, event_payload)
        self.bus.source_removed.emit(SourceEvent.REMOVED, event_payload)
        self.schedule_refresh()

    def _source_removal_failed(self, path, error):
        self._removing_paths.discard(path)
        if path not in self.pm.pdfs and path.lower().endswith(".pdf"):
            self.pm.pdfs.append(path)
        QMessageBox.warning(self, "Could not remove source", error)

    def _discard_remove_worker(self, worker):
        self._remove_workers.discard(worker)
        worker.deleteLater()

    def _legacy_source(self, path):
        from core.models.ontology_model import EntityModel, EntityType
        return EntityModel(
            id=path,
            entity_type=EntityType.SOURCE.value,
            origin_id=path,
            properties={"path": path, "title": os.path.basename(path)},
            state={"is_verified": True, "ai_generated": False},
        )


def _stage_icon(status: str) -> str:
    return {
        "complete": "✓",
        "running": "…",
        "queued": "◷",
        "stale": "!",
        "failed": "×",
        "disabled": "-",
    }.get(str(status or ""), "○")
