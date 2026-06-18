"""
plugins/zotero/gui/zotero_dock.py

Root Zotero Library dock widget.
Assembles CollectionTree, ItemTable, and ItemDetailPanel into a
three-panel layout and coordinates all inter-widget signals.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.components.base.core import BaseDock
from core.events.domains.tool_events import CitationIntent, CitationPayload
from gui.managers.dialog_manager import exec_as_modal, get_for_widget

from .collection_tree import CollectionTree
from .item_table import ItemTable
from .item_detail import ItemDetailPanel

if TYPE_CHECKING:
    from gui.app_context import AppContext
    from ..zotero_db import ZoteroDB
    from ..zotero_formatter import ZoteroFormatter


class _LoadWorker(QThread):
    """Background loader: fetches collections, tags, and items from ZoteroDB."""

    finished = Signal(list, list, list)  # collections, tags, items

    def __init__(self, db: "ZoteroDB", collection_id=None, tag=None):
        super().__init__()
        self._db = db
        self._collection_id = collection_id
        self._tag = tag

    def run(self):
        try:
            collections = self._db.get_collections()
            tags = self._db.get_tags()
            items = self._db.get_items(
                collection_id=self._collection_id,
                tag=self._tag,
            )
        except Exception as exc:
            print(f"[ZoteroDock] Load error: {exc}")
            collections, tags, items = [], [], []
        self.finished.emit(collections, tags, items)


class ZoteroDock(BaseDock):
    """
    Zotero Library browser dock.

    Layout (vertical splitter):
      Top pane: horizontal splitter
        Left:  CollectionTree (collections + tags)
        Right: ItemTable (item list + search)
      Bottom pane: ItemDetailPanel (metadata + actions)
    """

    def __init__(
        self,
        app_context: "AppContext",
        db: "ZoteroDB",
        formatter: "ZoteroFormatter",
        parent=None,
    ):
        super().__init__(app_context, parent)
        self._db = db
        self._formatter = formatter
        self._worker: Optional[_LoadWorker] = None
        self._attachment_ids: set = set()
        self._active_collection: Optional[int] = None
        self._active_tag: Optional[str] = None
        self._theme: dict = {}
        self._build_ui()
        self._load_library()

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    def _stop_worker(self):
        worker = self._worker
        if not worker:
            return
        try:
            if worker.isRunning():
                worker.requestInterruption()
                if not worker.wait(1500):
                    worker.terminate()
                    worker.wait(1500)
        except RuntimeError:
            pass
        self._worker = None

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()
        self._status_lbl = QLabel("Loading Zotero library…")
        self._status_lbl.setObjectName("statusLabel")
        toolbar.addWidget(self._status_lbl, 1)
        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self._load_library)
        toolbar.addWidget(self.refresh_btn)
        outer.addLayout(toolbar)

        # Vertical splitter: [top: left+right] / [bottom: detail]
        v_split = QSplitter(Qt.Orientation.Vertical)

        # Top pane: collection tree + item table
        top_widget = QWidget()
        h_split = QSplitter(Qt.Orientation.Horizontal, top_widget)

        self.collection_tree = CollectionTree()
        self.collection_tree.collection_selected.connect(self._on_collection_selected)
        self.collection_tree.tag_selected.connect(self._on_tag_selected)
        h_split.addWidget(self.collection_tree)
        h_split.setStretchFactor(0, 1)

        self.item_table = ItemTable()
        self.item_table.item_selected.connect(self._on_item_selected)
        h_split.addWidget(self.item_table)
        h_split.setStretchFactor(1, 3)
        h_split.setSizes([200, 500])

        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(h_split)
        v_split.addWidget(top_widget)

        # Bottom pane: detail panel
        self.detail = ItemDetailPanel(self._db, self._formatter)
        self.detail.import_requested.connect(self._on_import_item)
        self.detail.open_pdf_requested.connect(self._on_open_pdf)
        self.detail.copy_bibtex_requested.connect(self._on_bibtex_copied)
        self.detail.copy_citation_requested.connect(self._on_copy_citation)
        v_split.addWidget(self.detail)
        v_split.setSizes([400, 250])

        outer.addWidget(v_split, 1)

        # Unavailability notice shown when no DB found
        self._no_db_label = QLabel(
            "⚠️  Zotero library not found.\n\n"
            "Install Zotero and let it build its library, or set the\n"
            "ZOTERO_DB_PATH environment variable to your zotero.sqlite path."
        )
        self._no_db_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_db_label.setWordWrap(True)
        self._no_db_label.setObjectName("noDatabaseLabel")
        self._no_db_label.hide()
        outer.addWidget(self._no_db_label)

    # ------------------------------------------------------------------
    # Library loading
    # ------------------------------------------------------------------

    def _load_library(self):
        if not self._db.is_available():
            self._status_lbl.setText("⚠️  No Zotero database found")
            self._no_db_label.show()
            return

        self._no_db_label.hide()
        self.refresh_btn.setEnabled(False)
        self._status_lbl.setText("Loading…")

        if self._worker and self._worker.isRunning():
            self._stop_worker()

        self._worker = _LoadWorker(self._db, self._active_collection, self._active_tag)
        self._worker.finished.connect(self._on_load_finished)
        self._worker.finished.connect(lambda *_: self._clear_worker_ref())
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _clear_worker_ref(self):
        self._worker = None

    def _on_load_finished(self, collections: list, tags: list, items: list):
        self.refresh_btn.setEnabled(True)

        # Determine which items have PDF attachments (batch query)
        self._attachment_ids = self._fetch_attachment_ids(items)

        self.collection_tree.populate_collections(collections)
        self.collection_tree.populate_tags(tags)
        self.item_table.set_items(items, self._attachment_ids)

        n = len(items)
        self._status_lbl.setText(f"{n} item{'s' if n != 1 else ''} in library")

        # Re-apply theme to child widgets
        if self._theme:
            self._apply_theme_to_children(self._theme)

    def _fetch_attachment_ids(self, items: list) -> set:
        """Return set of item_ids that have at least one PDF attachment."""
        ids = set()
        if not self._db.is_available():
            return ids
        for item in items:
            atts = self._db.get_attachments(item["item_id"])
            if atts:
                ids.add(item["item_id"])
        return ids

    # ------------------------------------------------------------------
    # Filter handlers
    # ------------------------------------------------------------------

    def _on_collection_selected(self, collection_id: Optional[int]):
        self._active_collection = collection_id
        self._active_tag = None
        self._load_library()

    def _on_tag_selected(self, tag: Optional[str]):
        self._active_tag = tag
        self._active_collection = None
        self._load_library()

    def _on_item_selected(self, item: Optional[Dict]):
        self.detail.show_item(item)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _on_import_item(self, item: Dict):
        """Open project-scoped matching instead of creating a Zotero-only row."""
        try:
            from .sync_dialog import ZoteroSyncDialog
            dialog = ZoteroSyncDialog(
                db=self._db,
                formatter=self._formatter,
                project_manager=getattr(self.app_context, "project_manager", None),
                parent=self,
            )
            dm = getattr(self.app_context, "dialog_manager", None) or get_for_widget(self)
            if dm:
                dm.show_instance(dialog)
            else:
                exec_as_modal(dialog)
            self._status_lbl.setText("Use sync to match Zotero metadata to project PDFs")
        except Exception as exc:
            QMessageBox.warning(self, "Zotero Sync", f"Could not open sync dialog: {exc}")

    def _on_open_pdf(self, local_path: str):
        self._open_pdf_in_viewer(local_path)

    def _open_pdf_in_viewer(self, path: str):
        """Open a PDF path in the app's document viewer via the event bus."""
        try:
            from core.events.domains.document_events import DocumentIntent, DocumentPayload
            self.bus.document_action_requested.emit(
                DocumentIntent.OPEN,
                DocumentPayload(path=path),
            )
        except Exception as exc:
            print(f"[ZoteroDock] Could not open PDF: {exc}")

    def _on_bibtex_copied(self, item: Dict):
        self._status_lbl.setText(f"✓ BibTeX copied for: {item.get('title', '')[:40]}")

    def _on_copy_citation(self, item: Dict):
        """Copy a formatted citation string to the clipboard."""
        from PySide6.QtWidgets import QApplication
        cit = self._formatter.to_citation_dict(item)
        # Simple inline format using the app's CitationManager
        try:
            cm = self.app_context.citation_manager
            cm.set_style("APA")
            formatted = cm.format_entry(cit)
        except Exception:
            formatted = (
                f"{cit.get('authors', '')} ({cit.get('year', '')}). "
                f"{cit.get('title', '')}."
            )
        QApplication.clipboard().setText(formatted)
        self._status_lbl.setText("✓ Citation copied to clipboard")

    # ------------------------------------------------------------------
    # BaseDock lifecycle overrides
    # ------------------------------------------------------------------

    def on_project_loaded(self):
        # Re-check if a project PDF matches any Zotero attachment
        pass

    def on_project_cleared(self):
        self.detail.show_item(None)

    def apply_theme(self, theme: dict):
        self._theme = theme
        self._apply_theme_to_children(theme)

    def _apply_theme_to_children(self, theme: dict):
        bg = theme.get("bg_panel", "#073642")
        text = theme.get("text_main", "#93a1a1")
        border = theme.get("border", "#586e75")
        muted = theme.get("text_muted", "#586e75")
        accent = theme.get("accent", "#268bd2")

        self.setStyleSheet(f"background: {bg}; color: {text};")
        self._status_lbl.setStyleSheet(f"color: {muted}; background: transparent;")
        self.refresh_btn.setStyleSheet(
            f"background: {bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 4px 8px;"
        )
        self._no_db_label.setStyleSheet(
            f"color: {muted}; background: transparent; padding: 20px;"
        )
        self.collection_tree.apply_theme(theme)
        self.item_table.apply_theme(theme)
        self.detail.apply_theme(theme)
