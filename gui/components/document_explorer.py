# gui/components/document_explorer.py
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QComboBox, QMenu,
                             QMessageBox, QInputDialog, QSizePolicy)
from PySide6.QtCore import Qt
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentEvent, DocumentEventPayload, DocumentIntent, DocumentPayload
from core.events.domains.project_events import ProjectEvent, ProjectEventPayload, ProjectIntent, ProjectPayload

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

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.pm = main_window.project_manager
        self.bus = EventBus.get_instance()

        self._build_ui()

        # Subscribe to global events so the list updates automatically
        self.bus.project_loaded.connect(self._on_project_loaded)
        self.bus.pdf_renamed.connect(self._on_pdf_changed)
        self.bus.pdf_removed.connect(self._on_pdf_changed)

    def _on_project_loaded(self, event: ProjectEvent, payload: ProjectEventPayload):
        if event == ProjectEvent.LOADED:
            self.refresh_list()

    def _on_pdf_changed(self, event: DocumentEvent, payload: DocumentEventPayload):
        if event in {DocumentEvent.PDF_RENAMED, DocumentEvent.PDF_REMOVED}:
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
                DocumentPayload(source_id=item.data(self.SOURCE_ID_ROLE), path=item.data(self.PATH_ROLE))
            )
        )
        layout.addWidget(self.doc_list)

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

        sources = self.pm.list_source_entities() if hasattr(self.pm, "list_source_entities") else []
        if not sources:
            sources = [self._legacy_source(path) for path in self.pm.pdfs]

        for source in sources:
            if source.state.get("is_removed"):
                continue
            path = source.properties.get("path") or source.origin_id
            if not path or path not in set(self.pm.pdfs):
                continue
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

            lbl = ElidedLabel(source.properties.get("title") or os.path.basename(path))
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

        # BATCH MODE
        if len(selected_items) > 1:
            mass_tag_menu = menu.addMenu(f"🏷️ Mass Assign Tag to {len(selected_items)} Docs")
            tags = self.pm.get_all_tags()
            if not tags: mass_tag_menu.addAction("No tags created yet").setEnabled(False)
            else:
                for t in tags:
                    action = mass_tag_menu.addAction(t.get("name"))
                    action.triggered.connect(lambda checked=False, t_id=t.get("id"): self._mass_assign_tag(selected_items, t_id))
            menu.exec(self.doc_list.viewport().mapToGlobal(pos))

        # SINGLE MODE
        else:
            doc_path = item_at_pos.data(self.PATH_ROLE)
            manage_tags_action = menu.addAction("🏷️ Manage Tags for This Document")
            menu.addSeparator()
            rename_action = menu.addAction("✏️ Rename PDF")
            remove_action = menu.addAction("🗑️ Remove PDF from Project")
            extract_action = menu.addAction("✂️ Extract Pages to New PDF")

            chosen = menu.exec(self.doc_list.viewport().mapToGlobal(pos))

            if chosen == manage_tags_action:
                from gui.components.dialogs.tag_manager_dialog import TagAssignmentDialog
                if TagAssignmentDialog(doc_path, "doc", self.main_window).exec():
                    self.refresh_list()
                    # Trigger LLM tag filters to update
            elif chosen == rename_action:
                self._rename_pdf(doc_path)
            elif chosen == remove_action:
                self._remove_pdf(doc_path)
            elif chosen == extract_action:
                from gui.components.dialogs.extract_pages_dialog import ExtractPagesDialog
                if ExtractPagesDialog(doc_path, self.pm, self.main_window).exec():
                    self.refresh_list()

    def _mass_assign_tag(self, selected_items, tag_id):
        for item in selected_items:
            self.pm.assign_tag_to_doc(item.data(self.PATH_ROLE), tag_id)
        self.refresh_list()

    def _rename_pdf(self, old_path):
        self.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload())
        old_name = os.path.basename(old_path)
        new_name, ok = QInputDialog.getText(self, "Rename PDF", "Enter new name for the PDF:", text=old_name)

        if not ok or not new_name.strip() or new_name == old_name: return
        if not new_name.lower().endswith(".pdf"): new_name += ".pdf"
        new_path = os.path.join(os.path.dirname(old_path), new_name)

        if os.path.exists(new_path):
            QMessageBox.warning(self, "Error", "A file with that name already exists in this folder.")
            return

        if self.pm.rename_pdf(old_path, new_path):
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

            if self.main_window.current_file_path == old_path or self.main_window.current_file_path == new_path:
                self.main_window.current_file_path = None
                self.bus.document_action_requested.emit(DocumentIntent.OPEN, DocumentPayload(path=new_path, source_id=new_source.id if new_source else None))
    def _on_item_clicked(self, item):
        self.bus.document_action_requested.emit(
            DocumentIntent.OPEN,
            DocumentPayload(source_id=item.data(self.SOURCE_ID_ROLE), path=item.data(self.PATH_ROLE)),
        )
    def _remove_pdf(self, doc_path):
        reply = QMessageBox.question(self, "Remove PDF", f"Remove '{os.path.basename(doc_path)}' from the project?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return

        if self.pm.remove_pdf(doc_path):
            source = self.pm.get_source_entity_by_path(doc_path) if hasattr(self.pm, "get_source_entity_by_path") else None
            
            # Shout to the Event Bus
            self.bus.pdf_removed.emit(DocumentEvent.PDF_REMOVED, DocumentEventPayload(path=doc_path, source_id=source.id if source else None))

            if self.main_window.current_file_path == doc_path:
                self.main_window.current_file_path = None
                
                # --- FIX: Safely halt the background render thread BEFORE clearing the scene ---
                viewer = getattr(self.main_window, 'viewer', None)
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
                        DocumentPayload(source_id=first.data(self.SOURCE_ID_ROLE), path=first.data(self.PATH_ROLE)),
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
