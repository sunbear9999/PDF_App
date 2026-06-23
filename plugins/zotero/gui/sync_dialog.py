"""
plugins/zotero/gui/sync_dialog.py

ZoteroSyncDialog — smart matching between local Zotero items and project PDFs.

Shows project PDFs on the left and Zotero library items on the right.
Auto-suggests matches by title / filename similarity, lets the user confirm
or override each assignment, then writes citation data back to the project.
"""
from __future__ import annotations

import difflib
import os
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.events.event_bus import EventBus
from gui.utils.document_helpers import active_pdf_paths
from core.events.domains.tool_events import CitationIntent, CitationPayload

if TYPE_CHECKING:
    from core.project_manager import ProjectManager
    from ..zotero_db import ZoteroDB
    from ..zotero_formatter import ZoteroFormatter


# ---------------------------------------------------------------------------
# Background loader
# ---------------------------------------------------------------------------

class _LoadWorker(QThread):
    finished = Signal(list, str)

    def __init__(self, db: "ZoteroDB", config: dict | None = None):
        super().__init__()
        self._db = db
        self._config = config or {}

    def run(self):
        source = "desktop"
        try:
            items = self._db.get_items() if self._db and self._db.is_available() else []
        except Exception as exc:
            print(f"[ZoteroSync] Load error: {exc}")
            items = []
        if not items:
            try:
                from ..zotero_sync_adapter import PyZoteroClient
                client = PyZoteroClient(local_api_base_url=self._config.get("pyzotero_local_api_base_url", ""))
                items = client.list_local_items()
                if items:
                    source = "local_api"
            except Exception as exc:
                print(f"[ZoteroSync] Local API load error: {exc}")
                items = []
        self.finished.emit(items, source)


# ---------------------------------------------------------------------------
# Similarity helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return text.lower().strip().replace("_", " ").replace("-", " ")


def _title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _filename_similarity(pdf_path: str, zotero_item: dict) -> float:
    """Check if the PDF filename loosely matches the Zotero item title or attachment."""
    filename = os.path.splitext(os.path.basename(pdf_path))[0]
    title = zotero_item.get("title", "")
    score = _title_similarity(filename, title)

    # Also check known attachment filenames if the item has them
    for att in zotero_item.get("_attachments", []):
        att_name = os.path.splitext(os.path.basename(att.get("path", "")))[0]
        if att_name:
            score = max(score, _title_similarity(filename, att_name))

    return score


def _doi_similarity(project_citation: dict, zotero_item: dict) -> float:
    left = (project_citation or {}).get("doi") or (project_citation or {}).get("DOI")
    right = zotero_item.get("DOI") or (zotero_item.get("fields") or {}).get("DOI")
    if left and right and str(left).strip().lower() == str(right).strip().lower():
        return 1.0
    return 0.0


def _provenance_similarity(project_citation: dict, zotero_item: dict) -> float:
    key = (project_citation or {}).get("source_item_key")
    if key and key == zotero_item.get("key"):
        return 1.0
    fields = (project_citation or {}).get("fields") or {}
    if fields.get("key") and fields.get("key") == zotero_item.get("key"):
        return 1.0
    return 0.0


AUTO_MATCH_THRESHOLD = 0.45


def _auto_match(pdf_paths: List[str], zotero_items: List[dict], project_manager=None) -> Dict[str, Optional[dict]]:
    """
    Return a dict mapping pdf_path → best_zotero_item (or None).
    Only assigns when similarity is above AUTO_MATCH_THRESHOLD.
    """
    result: Dict[str, Optional[dict]] = {}
    for pdf in pdf_paths:
        best_item = None
        best_score = 0.0
        project_citation = project_manager.get_citation(pdf) if project_manager else {}
        for item in zotero_items:
            score = max(
                _provenance_similarity(project_citation, item),
                _doi_similarity(project_citation, item),
                _filename_similarity(pdf, item),
                _title_similarity((project_citation or {}).get("title", ""), item.get("title", "")),
            )
            if score > best_score:
                best_score = score
                best_item = item
        result[pdf] = best_item if best_score >= AUTO_MATCH_THRESHOLD else None
    return result


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class ZoteroSyncDialog(QDialog):
    """
    Three-pane sync dialog:
      Left:   Project PDFs (the ones that still need citation data)
      Center: Assignment table (PDF ↔ Zotero item pairs)
      Right:  Zotero library items (filterable)

    Workflow:
      1. Click a PDF in the left list
      2. Click a Zotero item in the right list → creates an assignment row
      OR press "Auto-Match" to let the dialog suggest pairs automatically
      3. Press OK → confirmed assignments write citation data to the project
    """

    def __init__(
        self,
        db: "ZoteroDB",
        formatter: "ZoteroFormatter",
        project_manager: Optional["ProjectManager"] = None,
        initial_pdf_paths: Optional[List[str]] = None,
        outbound_adapter=None,
        outbound_enabled: bool = False,
        outbound_collection_name: str = "",
        config=None,
        library_cache=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Sync Zotero Library with Project PDFs")
        self.resize(900, 600)
        self._db = db
        self._formatter = formatter
        self._pm = project_manager
        self._initial_pdf_paths = list(initial_pdf_paths or [])
        self._outbound_adapter = outbound_adapter
        self._outbound_enabled = outbound_enabled
        self._outbound_collection_name = outbound_collection_name
        self._config = config
        self._library_cache = library_cache
        self._zotero_items: List[dict] = []
        self._assignments: Dict[str, dict] = {}  # pdf_path → zotero item
        self._selected_pdf: Optional[str] = None
        self._worker: Optional[_LoadWorker] = None
        self._build_ui()
        self._load_project_pdfs()
        self._load_zotero_items()

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    def _stop_worker(self):
        worker = self._worker
        self._worker = None
        if not worker:
            return
        try:
            if worker.isRunning():
                worker.requestInterruption()
                if not worker.wait(1500):
                    worker.terminate()
                    worker.wait(500)
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        header = QLabel(
            "Match your project PDFs (left) to Zotero library items (right).\n"
            "Click a PDF, then click a Zotero item to create an assignment, "
            "or use Auto-Match for suggestions."
        )
        header.setWordWrap(True)
        root.addWidget(header)

        if self._outbound_adapter is not None and not self._outbound_adapter.can_write():
            readonly = QLabel(
                "Automatic PDF add is unavailable until PyZotero write settings are configured; "
                "metadata import/copy is available locally."
            )
            readonly.setWordWrap(True)
            readonly.setObjectName("zoteroReadOnlyNotice")
            root.addWidget(readonly)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left: project PDFs ---
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(QLabel("<b>Project PDFs</b>"))
        self._pdf_list = QListWidget()
        self._pdf_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._pdf_list.itemClicked.connect(self._on_pdf_clicked)
        left_lay.addWidget(self._pdf_list)
        splitter.addWidget(left)

        # --- Center: assignments ---
        center = QWidget()
        center_lay = QVBoxLayout(center)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.addWidget(QLabel("<b>Assignments</b>"))
        self._assign_table = QTableWidget(0, 2)
        self._assign_table.setHorizontalHeaderLabels(["PDF File", "Zotero Item"])
        self._assign_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._assign_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._assign_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        center_lay.addWidget(self._assign_table, 1)
        btn_row = QHBoxLayout()
        self._auto_btn = QPushButton("✨ Auto-Match")
        self._auto_btn.clicked.connect(self._do_auto_match)
        self._remove_btn = QPushButton("✕ Remove Selected")
        self._remove_btn.clicked.connect(self._remove_selected_assignment)
        self._outbound_btn = QPushButton("Add PDFs to Zotero")
        self._outbound_btn.clicked.connect(self._sync_pdfs_to_zotero)
        self._outbound_btn.setVisible(self._outbound_enabled)
        self._outbound_btn.setEnabled(
            bool(self._outbound_adapter and self._outbound_enabled and self._outbound_adapter.can_write())
        )
        btn_row.addWidget(self._auto_btn)
        btn_row.addWidget(self._remove_btn)
        btn_row.addWidget(self._outbound_btn)
        btn_row.addStretch()
        center_lay.addLayout(btn_row)
        splitter.addWidget(center)

        # --- Right: Zotero items ---
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(QLabel("<b>Zotero Library</b>"))
        self._status_lbl = QLabel("Loading…")
        right_lay.addWidget(self._status_lbl)
        self._zotero_list = QListWidget()
        self._zotero_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._zotero_list.itemClicked.connect(self._on_zotero_clicked)
        right_lay.addWidget(self._zotero_list)
        splitter.addWidget(right)

        splitter.setSizes([220, 340, 280])
        root.addWidget(splitter, 1)

        # --- Button box ---
        self._summary_lbl = QLabel("")
        root.addWidget(self._summary_lbl)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_project_pdfs(self):
        self._pdf_list.clear()
        if not self._pm:
            self._pdf_list.addItem(QListWidgetItem("(No project open)"))
            return
        active_paths = active_pdf_paths(self._pm)
        allowed = set(active_paths)
        selected = [path for path in self._initial_pdf_paths if path in allowed]
        paths = selected or active_paths
        for path in paths:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._pdf_list.addItem(item)

    def _load_zotero_items(self):
        self._status_lbl.setText("Loading Zotero library…")
        self._stop_worker()
        self._worker = _LoadWorker(self._db, self._config_snapshot())
        worker = self._worker
        worker.finished.connect(self._on_zotero_loaded)
        worker.finished.connect(lambda *_: self._clear_worker_ref(worker))
        worker.start()

    def _clear_worker_ref(self, worker):
        if self._worker is worker:
            self._worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass

    def _config_snapshot(self) -> dict:
        config = self._config
        if not config:
            return {}
        return {"pyzotero_local_api_base_url": config.get("pyzotero_local_api_base_url", "")}

    def _on_zotero_loaded(self, items: List[dict], source: str = "desktop"):
        # Merge in any items from the plugin write cache (recently added via API)
        if self._library_cache:
            seen_keys = {
                str(it.get("key") or it.get("item_id") or it.get("doc_id") or "")
                for it in items
            }
            for cached in self._library_cache.items():
                key = str(cached.get("key") or cached.get("item_id") or cached.get("doc_id") or "")
                if key and key not in seen_keys:
                    items.append(cached)
                    seen_keys.add(key)

        self._zotero_items = items
        self._zotero_list.clear()
        for item in items:
            # Pre-fetch attachments once so auto-match can use them
            try:
                if item.get("_source") != "local_api":
                    item["_attachments"] = self._db.get_attachments(item["item_id"])
                else:
                    item["_attachments"] = []
            except Exception:
                item["_attachments"] = []
            label = f"{item.get('title', '(no title)')}  [{item.get('year', '')}]"
            li = QListWidgetItem(label)
            li.setData(Qt.ItemDataRole.UserRole, item)
            authors = item.get("authors_display", "")
            li.setToolTip(f"{item.get('title', '')}\n{authors}")
            self._zotero_list.addItem(li)
        n = len(items)
        if n:
            source_label = "local API" if source == "local_api" else "desktop library"
            self._status_lbl.setText(f"{n} item{'s' if n != 1 else ''} from {source_label}")
        else:
            self._status_lbl.setText("No Zotero items found locally. If Zotero desktop is open, close it and refresh.")

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _on_pdf_clicked(self, list_item: QListWidgetItem):
        self._selected_pdf = list_item.data(Qt.ItemDataRole.UserRole)
        # Highlight existing assignment if any
        if self._selected_pdf in self._assignments:
            z = self._assignments[self._selected_pdf]
            title = z.get("title", "")
            for i in range(self._zotero_list.count()):
                zi = self._zotero_list.item(i)
                if zi.data(Qt.ItemDataRole.UserRole).get("title") == title:
                    self._zotero_list.setCurrentItem(zi)
                    break

    def _on_zotero_clicked(self, list_item: QListWidgetItem):
        if not self._selected_pdf:
            return
        zotero_item = list_item.data(Qt.ItemDataRole.UserRole)
        self._set_assignment(self._selected_pdf, zotero_item)

    def _set_assignment(self, pdf_path: str, zotero_item: dict):
        self._assignments[pdf_path] = zotero_item
        self._rebuild_table()
        self._update_summary()

    def _do_auto_match(self):
        if not self._pm or not self._zotero_items:
            return
        pdf_paths = [
            self._pdf_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._pdf_list.count())
        ]
        matches = _auto_match(pdf_paths, self._zotero_items, self._pm)
        changed = 0
        for pdf, zitem in matches.items():
            if zitem is not None and pdf not in self._assignments:
                self._assignments[pdf] = zitem
                changed += 1
        self._rebuild_table()
        self._update_summary()
        if changed == 0:
            self._summary_lbl.setText("No new auto-matches found (already assigned or no match above threshold).")
        else:
            self._summary_lbl.setText(f"✓ Auto-matched {changed} PDF{'s' if changed != 1 else ''}.")

    def _remove_selected_assignment(self):
        rows = self._assign_table.selectedItems()
        if not rows:
            return
        row = self._assign_table.currentRow()
        pdf_path = self._assign_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self._assignments.pop(pdf_path, None)
        self._rebuild_table()
        self._update_summary()

    def _sync_pdfs_to_zotero(self):
        if not self._pm or not self._outbound_adapter:
            return
        pdf_paths = [
            self._pdf_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._pdf_list.count())
        ]
        citations = {path: self._pm.get_citation(path) for path in pdf_paths}

        # Capture for closure — avoid holding refs to self inside the thread
        adapter = self._outbound_adapter
        collection_name = self._outbound_collection_name
        summary_lbl = self._summary_lbl

        self._summary_lbl.setText("Syncing to Zotero…")

        class _SyncWorker(QThread):
            finished = Signal(str)
            errored = Signal(str)

            def run(self):
                try:
                    result = adapter.sync_pdfs(
                        pdf_paths,
                        citations,
                        collection_name=collection_name,
                    )
                    self.finished.emit(result.message if hasattr(result, "message") else str(result))
                except Exception as exc:
                    self.errored.emit(str(exc))

        worker = _SyncWorker(self)
        # Keep a reference so the thread isn't GC'd mid-run
        self._zotero_sync_worker = worker
        worker.finished.connect(lambda msg: summary_lbl.setText(f"✅  {msg}"))
        worker.errored.connect(lambda err: summary_lbl.setText(f"⚠  Sync error: {err}"))
        worker.finished.connect(lambda _: setattr(self, "_zotero_sync_worker", None))
        worker.errored.connect(lambda _: setattr(self, "_zotero_sync_worker", None))
        worker.start()

    def update_theme(self, theme: dict):
        bg = theme.get("bg_panel", theme.get("bg_main", "#1e1e1e"))
        input_bg = theme.get("bg_input", "#2b2b2b")
        text = theme.get("text_main", "#ffffff")
        muted = theme.get("text_muted", "#aaaaaa")
        border = theme.get("border", "#444444")
        accent = theme.get("accent", "#4a8cff")
        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; color: {text}; }}
            QLabel {{ color: {text}; }}
            QLabel#zoteroReadOnlyNotice {{ color: {muted}; }}
            QListWidget, QTableWidget {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
            }}
            QHeaderView::section {{
                background-color: {bg};
                color: {text};
                border: 1px solid {border};
                padding: 4px;
            }}
            QPushButton {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 6px 10px;
            }}
            QPushButton:hover {{ background-color: {accent}; color: #ffffff; }}
            QDialogButtonBox QPushButton {{
                background-color: {accent};
                color: #ffffff;
                border: none;
            }}
        """)

    def _rebuild_table(self):
        self._assign_table.setRowCount(0)
        for pdf_path, zitem in self._assignments.items():
            row = self._assign_table.rowCount()
            self._assign_table.insertRow(row)
            filename_cell = QTableWidgetItem(os.path.basename(pdf_path))
            filename_cell.setData(Qt.ItemDataRole.UserRole, pdf_path)
            filename_cell.setToolTip(pdf_path)
            title_cell = QTableWidgetItem(zitem.get("title", ""))
            title_cell.setToolTip(
                f"{zitem.get('authors_display', '')} ({zitem.get('year', '')})"
            )
            self._assign_table.setItem(row, 0, filename_cell)
            self._assign_table.setItem(row, 1, title_cell)

    def _update_summary(self):
        n = len(self._assignments)
        if n == 0:
            self._summary_lbl.setText("")
        else:
            self._summary_lbl.setText(
                f"{n} assignment{'s' if n != 1 else ''} will be applied on OK."
            )

    # ------------------------------------------------------------------
    # Accept — apply assignments
    # ------------------------------------------------------------------

    def _on_accept(self):
        if not self._assignments:
            self.accept()
            return

        bus = EventBus.get_instance()
        applied = 0
        for pdf_path, zitem in self._assignments.items():
            cit = self._formatter.to_citation_dict(zitem)
            # Override doc_id to the PDF path so the data is stored under the
            # existing project document, not as a new "zotero:..." virtual entry.
            cit["doc_id"] = pdf_path
            bus.citation_action_requested.emit(
                CitationIntent.UPDATE_ENTRY,
                CitationPayload(data=cit),
            )
            applied += 1

        # Refresh the citation dock table so changes appear immediately
        bus.citation_action_requested.emit(CitationIntent.REFRESH_TABLE, CitationPayload())

        print(f"[ZoteroSync] Applied {applied} citation assignment(s).")
        self.accept()
