# gui/components/document_explorer.py
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QComboBox, QMenu,
                             QMessageBox, QInputDialog, QSizePolicy, QPushButton,
                             QFileDialog)
from PySide6.QtCore import Qt
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload, SourceEvent
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload, ProjectIntent, ProjectPayload
from gui.managers.dialog_manager import exec_as_modal, get_for_widget

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

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.pm = app_context.project_manager
        self.bus = EventBus.get_instance()

        self._build_ui()

        # Subscribe to global events so the list updates automatically
        self.bus.project_loaded.connect(self._on_project_loaded)
        self.bus.document_added.connect(self._on_pdf_changed)
        self.bus.pdf_renamed.connect(self._on_pdf_changed)
        self.bus.pdf_removed.connect(self._on_pdf_changed)
        self.bus.source_added.connect(self._on_source_changed)
        self.bus.source_renamed.connect(self._on_source_changed)
        self.bus.source_removed.connect(self._on_source_changed)

    def _on_project_loaded(self, event: ProjectEvent, payload: ProjectEventPayload):
        if event == ProjectEvent.LOADED:
            self.refresh_list()

    def _on_pdf_changed(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event in {DocumentEvent.DOCUMENT_ADDED, DocumentEvent.PDF_RENAMED, DocumentEvent.PDF_REMOVED}:
            self.refresh_list()

    def _on_source_changed(self, event: SourceEvent, payload: DocumentEventPayload):
        if event in {SourceEvent.ADDED, SourceEvent.RENAMED, SourceEvent.REMOVED}:
            self.refresh_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.doc_tag_filter = QComboBox()
        self.doc_tag_filter.setStyleSheet("padding: 4px; margin: 4px; font-weight: bold;")
        self.doc_tag_filter.addItem("All Tags", "ALL_TAGS")
        self.doc_tag_filter.currentIndexChanged.connect(self.refresh_list)
        layout.addWidget(self.doc_tag_filter)

        self.doc_list = QListWidget()
        self.doc_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        from PySide6.QtWidgets import QAbstractItemView
        self.doc_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.doc_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.doc_list.customContextMenuRequested.connect(self._on_context_menu)
        self.doc_list.itemClicked.connect(
            lambda item: self.bus.document_action_requested.emit(
                DocumentIntent.OPEN,
                DocumentPayload(
                    source_id=item.data(self.SOURCE_ID_ROLE),
                    path=item.data(self.PATH_ROLE),
                    source_type=item.data(self.SOURCE_TYPE_ROLE),
                )
            )
        )
        layout.addWidget(self.doc_list)

        self.add_pdf_btn = QPushButton("Add PDF")
        self.add_pdf_btn.setToolTip("Add PDFs to the current project")
        self.add_pdf_btn.clicked.connect(self._add_pdf)
        layout.addWidget(self.add_pdf_btn)

    def _add_pdf(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add PDFs",
            "",
            "Sources (*.pdf *.mp4 *.mov *.mkv *.webm *.avi *.m4v);;PDF Files (*.pdf);;Video Files (*.mp4 *.mov *.mkv *.webm *.avi *.m4v)",
        )
        if paths:
            self.bus.document_action_requested.emit(
                DocumentIntent.ADD_FILES,
                DocumentPayload(paths=paths),
            )

    def refresh_tag_filter(self):
        """Refreshes the available tags in the combo box."""
        current = self.doc_tag_filter.currentData()
        self.doc_tag_filter.blockSignals(True)
        self.doc_tag_filter.clear()
        self.doc_tag_filter.addItem("All Tags", "ALL_TAGS")

        for t in self.pm.get_all_tags():
            if t.get("name"):
                self.doc_tag_filter.addItem(t.get("name"), t.get("name"))

        index = self.doc_tag_filter.findData(current)
        if index >= 0: self.doc_tag_filter.setCurrentIndex(index)
        self.doc_tag_filter.blockSignals(False)

    def refresh_list(self):
        """Rebuilds the visual list of PDFs."""
        self.refresh_tag_filter()
        self.doc_list.blockSignals(True)
        self.doc_list.clear()

        selected_tag = self.doc_tag_filter.currentData()

        source_records = {s.get("path"): s for s in self.pm.list_sources()} if hasattr(self.pm, "list_sources") else {}
        sources = self.pm.list_source_entities() if hasattr(self.pm, "list_source_entities") else []
        if not sources:
            sources = [self._legacy_source(path) for path in self.pm.pdfs]

        for source in sources:
            if source.state.get("is_removed"):
                continue
            path = source.properties.get("path") or source.origin_id
            if not path:
                continue
            source_type = source.properties.get("source_type") or source_records.get(path, {}).get("source_type", "pdf")
            doc_tags = self.pm.get_tags_for_doc(path)
            if selected_tag and selected_tag != "ALL_TAGS":
                if selected_tag not in [t.get("name") for t in doc_tags]:
                    continue

            item = QListWidgetItem(self.doc_list)
            self.doc_list.addItem(item)

            widget = QWidget()
            widget.setStyleSheet("background: transparent;")
            w_layout = QHBoxLayout(widget)
            w_layout.setContentsMargins(5, 2, 5, 2)
            w_layout.setSpacing(4)

            icon = "🎬" if source_type == "video" else "📄"
            lbl = ElidedLabel(f"{icon} {source.properties.get('title') or os.path.basename(path)}")
            lbl.setStyleSheet("background: transparent;")
            lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            w_layout.addWidget(lbl, 1)

            tag_container = QWidget()
            tag_layout = QHBoxLayout(tag_container)
            tag_layout.setContentsMargins(0, 0, 0, 0)
            tag_layout.setSpacing(2)

            for t in doc_tags[:4]:
                dot = QLabel("●")
                dot.setStyleSheet(f"color: {t.get('color', '#888')}; font-size: 12px; background: transparent;")
                dot.setToolTip(t.get("name", ""))
                tag_layout.addWidget(dot)

            w_layout.addWidget(tag_container, 0)
            item.setSizeHint(widget.sizeHint())
            self.doc_list.setItemWidget(item, widget)
            item.setData(self.SOURCE_ID_ROLE, source.id)
            item.setData(self.PATH_ROLE, path)
            item.setData(self.SOURCE_TYPE_ROLE, source_type)

        self.doc_list.blockSignals(False)

    def _on_context_menu(self, pos):
        item_at_pos = self.doc_list.itemAt(pos)
        if not item_at_pos: return

        selected_items = self.doc_list.selectedItems()
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
            metrics_action = menu.addAction("📊 View Source Metrics")
            workspace_action = menu.addAction("📌 Add to Workspace")
            menu.addSeparator()
            rename_action = menu.addAction("✏️ Rename Source")
            remove_action = menu.addAction("🗑️ Remove Source from Project")
            extract_action = menu.addAction("✂️ Extract Pages to New PDF") if source_type == "pdf" else None
            self._inject_plugin_doc_items(menu, [doc_path])
            self._inject_dock_actions_doc(menu, [doc_path])

            chosen = menu.exec(global_pos)

            if chosen == metrics_action:
                self._open_source_metrics(doc_path)
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
                self._remove_pdf(doc_path)
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
            new_name += ".pdf" if source_type == "pdf" else os.path.splitext(old_name)[1]
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
    def _on_item_clicked(self, item):
        self.bus.document_action_requested.emit(
            DocumentIntent.OPEN,
            DocumentPayload(source_id=item.data(self.SOURCE_ID_ROLE), path=item.data(self.PATH_ROLE), source_type=item.data(self.SOURCE_TYPE_ROLE)),
        )
    def _remove_pdf(self, doc_path):
        source_type = self.pm.get_source_type(doc_path) if hasattr(self.pm, "get_source_type") else "pdf"
        reply = QMessageBox.question(self, "Remove Source", f"Remove '{os.path.basename(doc_path)}' from the project?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return

        if self.pm.remove_source(doc_path) if hasattr(self.pm, "remove_source") else self.pm.remove_pdf(doc_path):
            source = self.pm.get_source_entity_by_path(doc_path) if hasattr(self.pm, "get_source_entity_by_path") else None
            
            # Shout to the Event Bus
            self.bus.pdf_removed.emit(DocumentEvent.PDF_REMOVED, DocumentEventPayload(path=doc_path, source_id=source.id if source else None))
            self.bus.source_removed.emit(SourceEvent.REMOVED, DocumentEventPayload(path=doc_path, source_id=source.id if source else None, source_type=source_type))

            viewer = getattr(self._ctx, 'viewer', None)
            current = getattr(viewer, 'pdf_path', None) if viewer else None
            if current == doc_path:
                # Safely halt the background render thread BEFORE clearing the scene
                if viewer:
                    if hasattr(viewer, 'worker') and viewer.worker and viewer.worker.isRunning():
                        viewer.worker.stop()
                        viewer.worker.wait()
                        
                    if hasattr(viewer, 'scene') and viewer.scene: 
                        viewer.scene.clear()
                    viewer.doc = None
                # ------------------------------------------------------------------------------

                if self.doc_list.count() > 0:
                    first = self.doc_list.item(0)
                    self.bus.document_action_requested.emit(
                        DocumentIntent.OPEN,
                        DocumentPayload(source_id=first.data(self.SOURCE_ID_ROLE), path=first.data(self.PATH_ROLE), source_type=first.data(self.SOURCE_TYPE_ROLE)),
                    )

    def _legacy_source(self, path):
        from core.models.ontology_model import EntityModel, EntityType
        return EntityModel(
            id=path,
            entity_type=EntityType.SOURCE.value,
            origin_id=path,
            properties={"path": path, "title": os.path.basename(path)},
            state={"is_verified": True, "ai_generated": False},
        )
