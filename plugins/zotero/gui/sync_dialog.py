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
from core.events.domains.tool_events import CitationIntent, CitationPayload

if TYPE_CHECKING:
    from core.project_manager import ProjectManager
    from ..zotero_db import ZoteroDB
    from ..zotero_formatter import ZoteroFormatter


# ---------------------------------------------------------------------------
# Background loader
# ---------------------------------------------------------------------------

class _LoadWorker(QThread):
    finished = Signal(list)

    def __init__(self, db: "ZoteroDB"):
        super().__init__()
        self._db = db

    def run(self):
        try:
            items = self._db.get_items()
        except Exception as exc:
            print(f"[ZoteroSync] Load error: {exc}")
            items = []
        self.finished.emit(items)


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


AUTO_MATCH_THRESHOLD = 0.45


def _auto_match(pdf_paths: List[str], zotero_items: List[dict]) -> Dict[str, Optional[dict]]:
    """
    Return a dict mapping pdf_path → best_zotero_item (or None).
    Only assigns when similarity is above AUTO_MATCH_THRESHOLD.
    """
    result: Dict[str, Optional[dict]] = {}
    for pdf in pdf_paths:
        best_item = None
        best_score = 0.0
        for item in zotero_items:
            score = _filename_similarity(pdf, item)
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
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Sync Zotero Library with Project PDFs")
        self.resize(900, 600)
        self._db = db
        self._formatter = formatter
        self._pm = project_manager
        self._zotero_items: List[dict] = []
        self._assignments: Dict[str, dict] = {}  # pdf_path → zotero item
        self._selected_pdf: Optional[str] = None
        self._worker: Optional[_LoadWorker] = None
        self._build_ui()
        self._load_project_pdfs()
        self._load_zotero_items()

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
        btn_row.addWidget(self._auto_btn)
        btn_row.addWidget(self._remove_btn)
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
        for path in self._pm.pdfs:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._pdf_list.addItem(item)

    def _load_zotero_items(self):
        if not self._db.is_available():
            self._status_lbl.setText("⚠️ Zotero library not found")
            return
        self._status_lbl.setText("Loading Zotero library…")
        self._worker = _LoadWorker(self._db)
        self._worker.finished.connect(self._on_zotero_loaded)
        self._worker.start()

    def _on_zotero_loaded(self, items: List[dict]):
        self._zotero_items = items
        self._zotero_list.clear()
        for item in items:
            # Pre-fetch attachments once so auto-match can use them
            try:
                item["_attachments"] = self._db.get_attachments(item["item_id"])
            except Exception:
                item["_attachments"] = []
            label = f"{item.get('title', '(no title)')}  [{item.get('year', '')}]"
            li = QListWidgetItem(label)
            li.setData(Qt.ItemDataRole.UserRole, item)
            authors = item.get("authors_display", "")
            li.setToolTip(f"{item.get('title', '')}\n{authors}")
            self._zotero_list.addItem(li)
        n = len(items)
        self._status_lbl.setText(f"{n} item{'s' if n != 1 else ''}")

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
        matches = _auto_match(self._pm.pdfs, self._zotero_items)
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
