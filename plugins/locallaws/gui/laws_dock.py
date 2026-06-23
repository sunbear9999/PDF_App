"""
plugins/locallaws/gui/laws_dock.py

3-tab Laws & Precedent dock:
  Browse   — DuckDB-powered boolean search via background SearchWorker,
              multi-select, async fitz PDF export, tag filter,
              per-document hit-term tagging on export
  Download — pull municipal codes (by city or subject), case law via
              CourtListener Search API (targeted) or Bulk Archive (full court)
  Manage   — enable/disable DBs for RAG, per-DB tag assignment, delete DBs

Architecture
────────────
• SearchWorker (QThread) runs local_search() off the main thread.
• A 250 ms debounce timer prevents a new worker from firing on every keystroke.
• A monotonic _search_revision counter lets _on_search_results silently discard
  results from cancelled/superseded workers without any lock.
• The main thread NEVER does file I/O, DataFrame iteration, or regex loops.
"""
from __future__ import annotations

import html
import json
import os
import queue
import re
import tempfile
import threading
from typing import List, Optional

import pandas as pd
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QComboBox, QListWidget,
    QTextEdit, QPushButton, QSplitter, QLabel, QFileDialog, QListWidgetItem,
    QLineEdit, QScrollArea, QGroupBox, QCheckBox, QRadioButton, QSpinBox,
    QProgressBar, QMessageBox, QFrame, QAbstractItemView, QToolButton,
    QButtonGroup, QSizePolicy,
)

from .tag_panel import TagFilterWidget, TagManagerDialog, TagChip


# Maximum records returned by a single local_search call.
# Keeps the list widget responsive; users narrow via query.
_SEARCH_LIMIT = 2_000

# Debounce interval in milliseconds before a new SearchWorker is spawned.
_DEBOUNCE_MS = 250


# ---------------------------------------------------------------------------
# Background search worker
# ---------------------------------------------------------------------------

class _SearchWorker(QThread):
    """
    Runs law_manager.local_search() in a background thread.

    Carries a *revision* token so the receiver can silently discard results
    from superseded searches (see _on_search_results).
    """

    results_ready = Signal(list, int)   # (records, revision)

    def __init__(self, query: str, db_ids: list, law_manager, revision: int):
        super().__init__()
        self._query = query
        self._db_ids = db_ids
        self._law_manager = law_manager
        self._revision = revision

    def run(self) -> None:
        records = self._law_manager.local_search(
            self._query, self._db_ids, limit=_SEARCH_LIMIT
        )
        if not self.isInterruptionRequested():
            self.results_ready.emit(records, self._revision)


# ---------------------------------------------------------------------------
# Async fitz PDF worker (runs off main thread — no Qt rendering involved)
# ---------------------------------------------------------------------------

class _FitzPDFWorker(QThread):
    """
    Generates one or more PDFs using fitz.Story (PyMuPDF) in a background thread.
    records: list of {label, text, court?, url?, is_caselaw, _source_label?}
    save_dir: directory for output files (or None when save_path is explicit)
    save_path: explicit single output path (only used when len(records)==1)
    """

    progress = Signal(int, int, str)        # (current, total, filename)
    done = Signal(list, list)               # (generated_paths, matching_records)
    error = Signal(str)

    def __init__(self, records: list, save_dir: str, save_path: str = ""):
        super().__init__()
        self._records = records
        self._save_dir = save_dir
        self._save_path = save_path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            self.error.emit("PyMuPDF (fitz) is not installed. Run: pip install pymupdf")
            return

        generated: list[str] = []
        emitted_records: list[dict] = []
        total = len(self._records)

        for i, record in enumerate(self._records):
            if self._cancelled:
                break

            label = str(record.get("label", "Document"))
            raw_text = str(record.get("text", ""))
            is_caselaw = record.get("is_caselaw", False)

            safe_label = html.escape(label)
            if is_caselaw:
                subtitle = html.escape(record.get("court", ""))
                url = record.get("url", "")
                url_line = (
                    f'<p><a href="{html.escape(url)}">View on CourtListener</a></p>'
                    if url else ""
                )
            else:
                src = record.get("_source_label", "")
                subtitle = html.escape(src) if src else ""
                url_line = ""

            text_has_html = any(t in raw_text for t in ("<p", "<br", "<div", "<span"))
            body_html = raw_text if text_has_html else html.escape(raw_text).replace("\n", "<br>")

            full_html = (
                f"<h1 style='text-align:center;font-family:serif'>{safe_label}</h1>"
                f"<h2 style='text-align:center;color:#555;font-family:serif'>{subtitle}</h2>"
                f"{url_line}<hr>"
                f"<div style='font-size:11pt;line-height:1.6;font-family:serif'>{body_html}</div>"
            )

            if self._save_path and total == 1:
                out_path = self._save_path
            else:
                slug = "".join(
                    ch if ch.isalnum() or ch in "-_" else "_"
                    for ch in "_".join(label.split())[:60]
                )
                prefix = "CaseLaw" if is_caselaw else "Law"
                out_path = os.path.join(self._save_dir, f"{prefix}_{slug}.pdf")

            self.progress.emit(i + 1, total, os.path.basename(out_path))

            try:
                page_rect = fitz.paper_rect("letter")
                story = fitz.Story(html=full_html, user_css="body{margin:36pt}")
                writer = fitz.DocumentWriter(out_path)
                more = True
                while more:
                    dev = writer.begin_page(page_rect)
                    more, _ = story.place(page_rect)
                    story.draw(dev)
                    writer.end_page()
                writer.close()
                generated.append(out_path)
                emitted_records.append(record)
            except Exception as e:
                self.error.emit(f"Failed to write {os.path.basename(out_path)}: {e}")
                return

        self.done.emit(generated, emitted_records)


# ---------------------------------------------------------------------------
# Progress signaler (thread → main thread bridge for status queue)
# ---------------------------------------------------------------------------

class _ProgressSignaler(QObject):
    update = Signal(str, float)


# ---------------------------------------------------------------------------
# Main dock
# ---------------------------------------------------------------------------

class LocalLawsDock(QWidget):
    def __init__(self, api, law_manager, app_context=None, parent=None,
                 query_blueprint_id: str = "locallaws.query_assist"):
        super().__init__(parent)
        self.api = api
        self.law_manager = law_manager
        self.app_context = app_context
        self._query_blueprint_id = query_blueprint_id

        # Browse-tab state
        self._records: list[dict] = []
        self.active_db_label = ""
        self.is_caselaw = False
        self._current_db_data = None   # data from the combo box

        # Search worker + debounce
        self._search_revision: int = 0
        self._search_worker: Optional[_SearchWorker] = None
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(_DEBOUNCE_MS)
        self._search_debounce.timeout.connect(self._execute_search)

        # Download-tab state
        self._active_job = None
        self._ai_runner = None
        self._pending_tag_file_id: Optional[str] = None
        self._pending_auto_tag: Optional[tuple] = None

        # PDF export
        self._pdf_worker: Optional[_FitzPDFWorker] = None

        # Progress queue (background → main thread)
        self._status_queue: queue.Queue = queue.Queue()
        self._signaler = _ProgressSignaler()
        self._signaler.update.connect(self._apply_status_update)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_status_queue)
        self._poll_timer.start(80)

        self._build_ui()
        self.refresh_db_list()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_browse_tab(), "Browse")
        self._tabs.addTab(self._build_download_tab(), "Download")
        self._tabs.addTab(self._build_manage_tab(), "Manage")
        root.addWidget(self._tabs)

    # ---------- Browse tab ----------

    def _build_browse_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # DB selector row
        db_row = QHBoxLayout()
        db_row.addWidget(QLabel("Source:"))
        self.db_selector = QComboBox()
        self.db_selector.currentIndexChanged.connect(self._load_selected_db)
        db_row.addWidget(self.db_selector, 1)
        browse_refresh_btn = QPushButton("⟳")
        browse_refresh_btn.setToolTip("Refresh database list")
        browse_refresh_btn.setFixedWidth(30)
        browse_refresh_btn.clicked.connect(self.refresh_db_list)
        db_row.addWidget(browse_refresh_btn)
        layout.addLayout(db_row)

        # Tag filter bar
        tag_filter_row = QHBoxLayout()
        tag_filter_row.addWidget(QLabel("Tag filter:"))
        self._tag_filter = TagFilterWidget(parent=self)
        self._tag_filter.filter_changed.connect(self._on_tag_filter_changed)
        tag_filter_row.addWidget(self._tag_filter, 1)
        layout.addLayout(tag_filter_row)

        # Boolean search hint
        self._bool_hint_lbl = QLabel(
            '<b>Boolean search:</b> &nbsp;'
            '<code>term1 AND term2</code> &nbsp;·&nbsp; '
            '<code>term1 OR term2</code> &nbsp;·&nbsp; '
            '<code>NOT term</code> &nbsp;·&nbsp; '
            '<code>"exact phrase"</code> &nbsp;·&nbsp; '
            '<code>-exclude</code> &nbsp;—&nbsp; default: all words required'
        )
        self._bool_hint_lbl.setWordWrap(True)
        self._bool_hint_lbl.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(self._bool_hint_lbl)

        # Search row
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            'e.g.  "zoning variance" AND residential  |  excessive AND force NOT property'
        )
        self.search_input.setClearButtonEnabled(True)
        # textChanged → debounce timer (never filters on the main thread)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self._result_count_lbl = QLabel("")
        self._result_count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self._result_count_lbl)
        layout.addLayout(search_row)

        # List + viewer splitter
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.law_list = QListWidget()
        self.law_list.setAlternatingRowColors(True)
        self.law_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.law_list.itemSelectionChanged.connect(self._display_law)
        splitter.addWidget(self.law_list)

        viewer_widget = QWidget()
        vl = QVBoxLayout(viewer_widget)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(4)

        self.text_viewer = QTextEdit()
        self.text_viewer.setReadOnly(True)
        self.text_viewer.setPlaceholderText("Select a law or precedent to view its text…")
        vl.addWidget(self.text_viewer)

        btn_row = QHBoxLayout()
        self._export_sel_btn = QPushButton("📥  Export Selected to Project")
        self._export_sel_btn.clicked.connect(self._export_selected)
        self._export_sel_btn.setEnabled(False)
        self._export_sel_btn.setToolTip(
            "Export all selected items as PDFs and add to current project.\n"
            "Each document is automatically tagged with the search terms that matched it."
        )
        btn_row.addStretch()
        btn_row.addWidget(self._export_sel_btn)
        self._export_status_lbl = QLabel("")
        self._export_status_lbl.setVisible(False)
        btn_row.addWidget(self._export_status_lbl)
        vl.addLayout(btn_row)

        splitter.addWidget(viewer_widget)
        splitter.setSizes([350, 600])
        layout.addWidget(splitter)
        return w

    # ---------- Download tab ----------

    def _build_download_tab(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(60)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        scroll_w = QWidget()
        scroll_layout = QVBoxLayout(scroll_w)
        scroll_layout.setContentsMargins(8, 8, 8, 8)
        scroll_layout.setSpacing(8)

        # ── Municipal Codes group ──────────────────────────────────────────────
        mun_group = QGroupBox("📄  Municipal Codes (LOCUS)")
        mun_layout = QVBoxLayout(mun_group)
        mun_layout.setSpacing(4)

        mode_row = QHBoxLayout()
        self._mun_mode_city = QRadioButton("By City")
        self._mun_mode_city.setChecked(True)
        self._mun_mode_subject = QRadioButton("By Subject / Keyword")
        self._mun_mode_city.toggled.connect(self._update_mun_mode)
        mode_row.addWidget(self._mun_mode_city)
        mode_row.addWidget(self._mun_mode_subject)
        mode_row.addStretch()
        mun_layout.addLayout(mode_row)

        self._mun_city_widget = QWidget()
        city_form = QHBoxLayout(self._mun_city_widget)
        city_form.setContentsMargins(0, 0, 0, 0)
        city_form.addWidget(QLabel("State:"))
        self.state_input = QLineEdit()
        self.state_input.setPlaceholderText("e.g. CO")
        self.state_input.setMaximumWidth(80)
        city_form.addWidget(self.state_input)
        city_form.addWidget(QLabel("City:"))
        self.city_input = QLineEdit()
        self.city_input.setPlaceholderText("e.g. Parker")
        city_form.addWidget(self.city_input, 1)
        mun_layout.addWidget(self._mun_city_widget)

        self._mun_subject_widget = QWidget()
        subj_vlayout = QVBoxLayout(self._mun_subject_widget)
        subj_vlayout.setContentsMargins(0, 0, 0, 0)
        subj_vlayout.setSpacing(3)
        subj_form = QHBoxLayout()
        subj_form.addWidget(QLabel("State(s):"))
        self.state_subj_input = QLineEdit()
        self.state_subj_input.setPlaceholderText("CO  or  CO, CA, TX  or  ALL")
        subj_form.addWidget(self.state_subj_input, 1)
        subj_vlayout.addLayout(subj_form)
        subj_kw_form = QHBoxLayout()
        subj_kw_form.addWidget(QLabel("Subject:"))
        self.subject_input = QLineEdit()
        self.subject_input.setPlaceholderText(
            '"noise ordinance" OR nuisance  |  zoning AND residential'
        )
        subj_kw_form.addWidget(self.subject_input, 1)
        subj_vlayout.addLayout(subj_kw_form)
        mun_hint = QLabel(
            '<small><b>Boolean:</b>  <code>"noise ordinance"</code>  ·  '
            '<code>zoning AND residential</code>  ·  <code>nuisance OR "public safety"</code>  ·  '
            '<code>NOT agricultural</code> &nbsp;—&nbsp; '
            'Use <b>ALL</b> for every state (slow, keep query specific).</small>'
        )
        mun_hint.setWordWrap(True)
        mun_hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        subj_vlayout.addWidget(mun_hint)
        self._mun_subject_widget.setVisible(False)
        mun_layout.addWidget(self._mun_subject_widget)

        self.download_mun_btn = QPushButton("⬇  Download & Index")
        self.download_mun_btn.clicked.connect(self._run_municipal_ingestion)
        mun_layout.addWidget(self.download_mun_btn)
        scroll_layout.addWidget(mun_group)

        # ── Case Law group ────────────────────────────────────────────────────
        case_group = QGroupBox("🏛️  Federal / State Case Law (CourtListener)")
        case_layout = QVBoxLayout(case_group)
        case_layout.setSpacing(4)

        links_row = QHBoxLayout()
        self._api_link_lbl = QLabel()
        self._api_link_lbl.setOpenExternalLinks(True)
        self._docs_link_lbl = QLabel()
        self._docs_link_lbl.setOpenExternalLinks(True)
        links_row.addWidget(self._api_link_lbl)
        links_row.addStretch()
        links_row.addWidget(self._docs_link_lbl)
        case_layout.addLayout(links_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("CourtListener Token")
        self.api_key_input.setText(self.api.config.get("courtlistener_token", ""))
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        key_row.addWidget(self.api_key_input, 1)
        case_layout.addLayout(key_row)

        # Download mode toggle
        dl_mode_row = QHBoxLayout()
        dl_mode_row.addWidget(QLabel("Download mode:"))
        self._case_mode_search = QRadioButton("Search API (targeted, up to N results)")
        self._case_mode_bulk = QRadioButton("Bulk Archive (entire court, streaming)")
        self._case_mode_search.setChecked(True)
        self._case_mode_search.toggled.connect(self._update_case_mode)
        dl_mode_row.addWidget(self._case_mode_search)
        dl_mode_row.addWidget(self._case_mode_bulk)
        dl_mode_row.addStretch()
        case_layout.addLayout(dl_mode_row)

        bulk_note = QLabel(
            '<small><b>Bulk mode</b> streams the full court archive '
            '(<code>opinions/{court}.tar.gz</code>) in memory-safe batches — '
            'ideal for the 9th Circuit or similar large corpora. '
            'No query field needed; the court ID is sufficient.</small>'
        )
        bulk_note.setWordWrap(True)
        bulk_note.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        case_layout.addWidget(bulk_note)

        court_row = QHBoxLayout()
        court_row.addWidget(QLabel("Court(s):"))
        self.court_input = QLineEdit()
        self.court_input.setPlaceholderText("scotus  |  ca9  |  ca9,ca10  |  blank = all")
        court_row.addWidget(self.court_input, 1)
        case_layout.addLayout(court_row)

        # Search-API-only fields (hidden in bulk mode)
        self._case_search_widget = QWidget()
        search_form = QVBoxLayout(self._case_search_widget)
        search_form.setContentsMargins(0, 0, 0, 0)
        search_form.setSpacing(4)

        query_row = QHBoxLayout()
        query_row.addWidget(QLabel("Query:"))
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText('"excessive force" AND "qualified immunity"')
        query_row.addWidget(self.query_input, 1)
        search_form.addLayout(query_row)

        case_hint = QLabel(
            '<small><b>Boolean:</b>  <code>"phrase"</code>  ·  <code>AND</code>  <code>OR</code>  <code>NOT</code>  ·  '
            '<code>qualif*</code> (wildcard)  ·  <code>caseName:Miranda</code>  ·  <code>status:Precedential</code>'
            ' — e.g. <code>"excessive force" AND "section 1983"</code></small>'
        )
        case_hint.setWordWrap(True)
        case_hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        search_form.addWidget(case_hint)

        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("After:"))
        self.date_from_input = QLineEdit()
        self.date_from_input.setPlaceholderText("YYYY-MM-DD")
        date_row.addWidget(self.date_from_input, 1)
        date_row.addWidget(QLabel("Before:"))
        self.date_to_input = QLineEdit()
        self.date_to_input.setPlaceholderText("YYYY-MM-DD")
        date_row.addWidget(self.date_to_input, 1)
        search_form.addLayout(date_row)

        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("Max results:"))
        self.limit_input = QSpinBox()
        self.limit_input.setRange(1, 500)
        self.limit_input.setValue(25)
        limit_row.addWidget(self.limit_input)
        limit_row.addStretch()
        search_form.addLayout(limit_row)

        case_layout.addWidget(self._case_search_widget)

        self.download_case_btn = QPushButton("🔍  Search & Embed Case Law")
        self.download_case_btn.clicked.connect(self._run_caselaw_ingestion)
        case_layout.addWidget(self.download_case_btn)
        scroll_layout.addWidget(case_group)

        # ── AI Query Assistant group ──────────────────────────────────────────
        ai_group = QGroupBox("🤖  AI Query Assistant")
        ai_layout = QVBoxLayout(ai_group)
        ai_layout.setSpacing(4)

        ai_desc = QLabel(
            "Describe your legal issue — include location, legal theory, timeframe:"
        )
        ai_desc.setWordWrap(True)
        ai_layout.addWidget(ai_desc)

        self.ai_issue_edit = QTextEdit()
        self.ai_issue_edit.setPlaceholderText(
            "Examples:\n"
            '• "Excessive force §1983 — Ninth Circuit — after 2015"\n'
            '• "Fourth Amendment vehicle stops — recent SCOTUS"\n'
            '• "Zoning variance denials and due process in Colorado"\n'
            '• "Native Hawaiian ceded lands rights"'
        )
        self.ai_issue_edit.setMinimumHeight(52)
        self.ai_issue_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.ai_issue_edit.setAcceptRichText(False)
        ai_layout.addWidget(self.ai_issue_edit)

        ai_target_row = QHBoxLayout()
        ai_target_row.addWidget(QLabel("Auto-fill:"))
        self.ai_target_combo = QComboBox()
        self.ai_target_combo.addItems(["Case Law fields", "Municipal fields", "Both"])
        ai_target_row.addWidget(self.ai_target_combo)
        ai_target_row.addStretch()
        self.ai_suggest_btn = QPushButton("✨  Suggest Query")
        self.ai_suggest_btn.clicked.connect(self._run_ai_query_assist)
        ai_target_row.addWidget(self.ai_suggest_btn)
        ai_layout.addLayout(ai_target_row)

        ai_tips = QLabel(
            '<small><b>Tips:</b> mention state/circuit, legal theory, and era. '
            'AI writes boolean queries automatically for both case law and municipal codes.</small>'
        )
        ai_tips.setWordWrap(True)
        ai_tips.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        ai_layout.addWidget(ai_tips)

        self._ai_status_lbl = QLabel("")
        self._ai_status_lbl.setWordWrap(True)
        self._ai_status_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        ai_layout.addWidget(self._ai_status_lbl)

        ai_meta_row = QHBoxLayout()
        from gui.components.prompt_trace_button import PromptTraceButton
        self._ai_trace_btn = PromptTraceButton(app_context=self.app_context, parent=self)
        self._ai_trace_btn.setVisible(False)
        ai_meta_row.addWidget(self._ai_trace_btn)
        ai_meta_row.addStretch()
        self._ai_configure_btn = QPushButton("⚙ Configure AI Prompts")
        self._ai_configure_btn.setToolTip(
            "Open the Prompt Manager to view or edit the Legal Research Query Assist prompts."
        )
        self._ai_configure_btn.clicked.connect(self._open_prompt_manager)
        ai_meta_row.addWidget(self._ai_configure_btn)
        ai_layout.addLayout(ai_meta_row)

        scroll_layout.addWidget(ai_group)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_w)
        outer.addWidget(scroll, 1)

        # ── Fixed status bar at the bottom ────────────────────────────────────
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.Shape.StyledPanel)
        status_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        status_vl = QVBoxLayout(status_frame)
        status_vl.setContentsMargins(8, 4, 8, 4)
        status_vl.setSpacing(3)

        status_top = QHBoxLayout()
        self._status_lbl = QLabel("No active download.")
        self._status_lbl.setWordWrap(True)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedWidth(70)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_job)
        status_top.addWidget(self._status_lbl, 1)
        status_top.addWidget(self._cancel_btn)
        status_vl.addLayout(status_top)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(14)
        status_vl.addWidget(self._progress_bar)
        outer.addWidget(status_frame)
        return w

    # ---------- Manage tab ----------

    def _build_manage_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("<b>Installed Databases</b>"))
        hdr.addStretch()
        tag_mgr_btn = QPushButton("🏷  Manage Tags")
        tag_mgr_btn.setToolTip("Create, rename, recolor or delete law tags")
        tag_mgr_btn.clicked.connect(self._open_tag_manager)
        hdr.addWidget(tag_mgr_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_manage_tab)
        hdr.addWidget(refresh_btn)
        layout.addLayout(hdr)

        layout.addWidget(QLabel(
            "Check a database to include it in RAG searches. "
            "Use 🏷 to assign tags for per-tag RAG filtering."
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._manage_scroll_widget = QWidget()
        self._manage_scroll_layout = QVBoxLayout(self._manage_scroll_widget)
        self._manage_scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._manage_scroll_layout.setSpacing(4)
        scroll.setWidget(self._manage_scroll_widget)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        sel_all_btn = QPushButton("Enable All")
        sel_all_btn.clicked.connect(lambda: self._set_all_manage_checks(True))
        desel_btn = QPushButton("Disable All")
        desel_btn.clicked.connect(lambda: self._set_all_manage_checks(False))
        save_btn = QPushButton("💾  Save RAG Settings")
        save_btn.clicked.connect(self._save_manage_settings)
        btn_row.addWidget(sel_all_btn)
        btn_row.addWidget(desel_btn)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self._manage_checkbox_map: dict = {}
        self._refresh_manage_tab()
        return w

    # ------------------------------------------------------------------
    # Browse tab — search flow
    # ------------------------------------------------------------------

    _ALL_SOURCES_KEY = "__all_sources__"

    def refresh_db_list(self):
        self.db_selector.blockSignals(True)
        self.db_selector.clear()
        dbs = self.law_manager.get_installed_dbs()
        if not dbs:
            self.db_selector.addItem("No sources installed yet — use Download tab", None)
        else:
            self.db_selector.addItem("— Select a source to browse —", None)
            self.db_selector.addItem(
                f"🔍  All Sources  ({len(dbs)} database{'s' if len(dbs) != 1 else ''})",
                self._ALL_SOURCES_KEY,
            )
            for db in dbs:
                icon = "🏛️" if db["type"] == "caselaw" else "📄"
                status = "" if db.get("index_complete", True) else "  ⚠"
                self.db_selector.addItem(f"{icon}  {db['label']}{status}", db)
        self.db_selector.blockSignals(False)

        all_tags = self.law_manager.tag_db.get_all_tags()
        self._tag_filter.set_available_tags(all_tags)

    def _load_selected_db(self):
        """
        Store the selected DB info and trigger a background search.
        Never reads parquet on the main thread.
        """
        db_data = self.db_selector.currentData()
        self._current_db_data = db_data

        self.law_list.clear()
        self.text_viewer.clear()
        self._records = []
        self._export_sel_btn.setEnabled(False)
        self._result_count_lbl.setText("")

        if not db_data:
            return

        if db_data == self._ALL_SOURCES_KEY:
            self.active_db_label = "All Sources"
            self.is_caselaw = False
        else:
            self.active_db_label = db_data.get("label", "")
            self.is_caselaw = db_data.get("type") == "caselaw"

        self._execute_search()

    def _on_search_text_changed(self, _text: str) -> None:
        """Restart the debounce timer on every keystroke."""
        self._search_debounce.start()

    def _on_tag_filter_changed(self, _tag_ids: list, _logic: str) -> None:
        """Tag filter changed — fire search immediately (no debounce needed)."""
        self._execute_search()

    def _execute_search(self) -> None:
        """
        Cancel the previous SearchWorker (if any) and spawn a fresh one.

        Race-condition prevention
        ─────────────────────────
        1. requestInterruption() signals the old worker to stop reading.
           If it finishes anyway, _on_search_results discards it via revision check.
        2. _search_revision is incremented atomically on the main thread before
           the new worker is created.  The worker captures this value at birth
           and sends it back with results_ready.  Any result whose revision ≠
           _search_revision is silently discarded — no locks required.
        """
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.requestInterruption()

        db_data = self._current_db_data
        if not db_data:
            self.law_list.clear()
            self._result_count_lbl.setText("")
            return

        # Determine which db_ids to search
        if db_data == self._ALL_SOURCES_KEY:
            all_dbs = self.law_manager.get_installed_dbs()
            db_ids = [d["file_id"] for d in all_dbs]
        else:
            db_ids = [db_data["file_id"]]

        # Narrow to tag-filtered subset when a tag filter is active
        active_tag_ids = self._tag_filter.active_tag_ids
        tag_logic = self._tag_filter.logic
        if active_tag_ids:
            all_tags_map = {t["id"]: t["name"] for t in self.law_manager.tag_db.get_all_tags()}
            tag_names = [all_tags_map[tid] for tid in active_tag_ids if tid in all_tags_map]
            if tag_names:
                allowed = set(self.law_manager.tag_db.get_dbs_for_tags(tag_names, tag_logic))
                db_ids = [fid for fid in db_ids if fid in allowed]

        if not db_ids:
            self.law_list.clear()
            self._result_count_lbl.setText("0")
            return

        self._search_revision += 1
        query = self.search_input.text()

        worker = _SearchWorker(query, db_ids, self.law_manager, self._search_revision)
        worker.results_ready.connect(self._on_search_results)
        worker.start()
        self._search_worker = worker
        self._result_count_lbl.setText("…")

    def _on_search_results(self, records: list, revision: int) -> None:
        """Receive results from SearchWorker; discard stale revisions."""
        if revision != self._search_revision:
            return  # superseded by a newer search — discard

        # Annotate each record with which query atoms matched it (for per-doc tagging)
        self._annotate_matched_terms(self.search_input.text(), records)

        self._records = records
        self._render_law_list(records)

    def _render_law_list(self, records: list) -> None:
        """
        Clear and repopulate the list widget from *records*.
        No filtering loop — DuckDB already narrowed the set.
        """
        multi_source = len({r.get("_file_id", "") for r in records}) > 1
        self.law_list.blockSignals(True)
        self.law_list.clear()
        for record in records:
            if multi_source and record.get("_source_label"):
                display = f"[{record['_source_label']}]  {record['label']}"
            else:
                display = record["label"]
            item = QListWidgetItem(display)
            item.setToolTip(str(record.get("text", ""))[:300])
            item.setData(Qt.ItemDataRole.UserRole, record)
            self.law_list.addItem(item)
        self.law_list.blockSignals(False)

        n = len(records)
        suffix = f"  (capped at {_SEARCH_LIMIT:,})" if n == _SEARCH_LIMIT else ""
        self._result_count_lbl.setText(f"{n:,}{suffix}")

    # ------------------------------------------------------------------
    # Per-document hit-term annotation
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_query_atoms(query: str) -> list[str]:
        """
        Return the distinct searchable atoms in *query*: quoted phrases first,
        then individual words, operators (AND / OR / NOT) excluded.
        Strips surrounding punctuation from each atom.
        """
        query = (query or "").strip()
        if not query:
            return []

        atoms: list[str] = []
        seen: set[str] = set()

        # Quoted phrases
        for phrase in re.findall(r'"([^"]+)"', query):
            clean = phrase.strip()
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                atoms.append(clean)

        # Remaining single words
        remainder = re.sub(r'"[^"]+"', "", query)
        for tok in re.split(r"\s+", remainder.strip()):
            t = tok.strip().lstrip("-")
            if t and t.upper() not in ("AND", "OR", "NOT", "") and t.lower() not in seen:
                seen.add(t.lower())
                atoms.append(t)

        return atoms

    @staticmethod
    def _annotate_matched_terms(query: str, records: list) -> None:
        """
        For each record, set record["_matched_terms"] to the list of query atoms
        whose text appears in that record.

        This gives per-document attribution: if a query is  A OR B  and one
        record only contains A while another contains B, each gets only its
        matching atom as a tag — not both.
        """
        atoms = LocalLawsDock._extract_query_atoms(query)
        for rec in records:
            if not atoms:
                rec["_matched_terms"] = []
                continue
            haystack = (
                f"{rec.get('label', '')} {rec.get('text', '')} "
                f"{rec.get('header', '')} {rec.get('case_name', '')}"
            ).lower()
            rec["_matched_terms"] = [a for a in atoms if a.lower() in haystack]

    # ------------------------------------------------------------------
    # Browse — viewer & citation
    # ------------------------------------------------------------------

    def _display_law(self):
        items = self.law_list.selectedItems()
        if not items:
            self._export_sel_btn.setEnabled(False)
            return

        self._export_sel_btn.setEnabled(True)
        n = len(items)
        if n > 1:
            self._export_sel_btn.setText(f"📥  Export {n} Selected to Project")
            self.text_viewer.setPlainText(f"{n} items selected — click Export to generate PDFs.")
            return

        self._export_sel_btn.setText("📥  Export Selected to Project")
        law_data = items[0].data(Qt.ItemDataRole.UserRole)
        safe_label = html.escape(str(law_data["label"]))
        raw_text = str(law_data["text"])
        text_has_html = any(tag in raw_text for tag in ("<p", "<br", "<div", "<p>"))
        formatted_text = raw_text if text_has_html else html.escape(raw_text).replace("\n", "<br>")

        is_caselaw = law_data.get("is_caselaw", self.is_caselaw)
        if is_caselaw:
            url_html = (
                f'<p><a href="{law_data["url"]}" style="color:#1a73e8;">View on CourtListener ↗</a></p>'
                if law_data.get("url") else ""
            )
            court = html.escape(law_data.get("court", ""))
            html_payload = (
                f"<h3 style='margin-bottom:2px'>{safe_label}</h3>"
                f"<p style='color:gray;margin-top:0'><b>Court: {court}</b></p>"
                f"{url_html}<hr>"
                f"<div style='font-size:14px;line-height:1.5;font-family:sans-serif'>{formatted_text}</div>"
            )
        else:
            src_label = law_data.get("_source_label") or self.active_db_label
            safe_db = html.escape(src_label)
            html_payload = (
                f"<h3 style='margin-bottom:2px'>{safe_db}</h3>"
                f"<p style='color:gray;margin-top:0'><b>{safe_label}</b></p>"
                f"<hr>"
                f"<div style='font-size:14px;line-height:1.5;font-family:sans-serif'>{formatted_text}</div>"
            )
        self.text_viewer.setHtml(html_payload)

    def open_citation(self, citation: dict) -> bool:
        locator = citation.get("source_locator") or {}
        jurisdiction_id = locator.get("jurisdiction_id") or citation.get("source_id", "")
        for index in range(self.db_selector.count()):
            data = self.db_selector.itemData(index)
            if isinstance(data, dict) and data.get("file_id") == jurisdiction_id:
                self.db_selector.setCurrentIndex(index)
                break

        quote = str(citation.get("quote", "") or "").strip()
        normalized_quote = " ".join(quote.lower().split())

        def score(record):
            v = 0
            normalized_text = " ".join(str(record["text"]).lower().split())
            if normalized_quote and normalized_quote in normalized_text:
                v += 100
            if record.get("header") and citation.get("header", "").lower() == record["header"].lower():
                v += 40
            return v

        best = max(self._records, key=score, default=None)
        if not best or score(best) == 0:
            self.api.notify(
                "The cited law could not be located in the installed database.", level="warning"
            )
            return False

        self.search_input.clear()
        for index in range(self.law_list.count()):
            item = self.law_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == best:
                self.law_list.setCurrentItem(item)
                self.law_list.scrollToItem(item)
                self._highlight_quote(quote)
                return True
        return False

    def _highlight_quote(self, quote: str) -> None:
        self.text_viewer.setExtraSelections([])
        if not quote:
            return
        cursor = self.text_viewer.document().find(quote)
        if cursor.isNull():
            cursor = self.text_viewer.document().find(" ".join(quote.split()[:8]))
        if cursor.isNull():
            return
        sel = QTextEdit.ExtraSelection()
        sel.cursor = cursor
        sel.format = QTextCharFormat()
        sel.format.setBackground(QColor("#ffe082"))
        sel.format.setForeground(QColor("#202124"))
        self.text_viewer.setExtraSelections([sel])
        self.text_viewer.setTextCursor(cursor)
        self.text_viewer.ensureCursorVisible()

    # ------------------------------------------------------------------
    # Async PDF export (fitz.Story in QThread)
    # ------------------------------------------------------------------

    def _export_selected(self):
        """Export all selected items as PDFs and add them to the current project."""
        items = self.law_list.selectedItems()
        if not items:
            return

        records = [item.data(Qt.ItemDataRole.UserRole) for item in items]

        if len(records) == 1:
            law_data = records[0]
            safe = "_".join(str(law_data["label"]).split())[:60]
            safe = "".join(ch for ch in safe if ch.isalnum() or ch in "-_")
            prefix = "CaseLaw" if law_data.get("is_caselaw") else "Law"
            default = f"{prefix}_{safe}.pdf"
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save as PDF", default, "PDF Documents (*.pdf)"
            )
            if not save_path:
                return
            save_dir = ""
        else:
            save_dir = QFileDialog.getExistingDirectory(
                self, f"Save {len(records)} PDFs to folder"
            )
            if not save_dir:
                return
            save_path = ""

        if self._pdf_worker and self._pdf_worker.isRunning():
            self.api.notify("A PDF export is already in progress.", level="warning")
            return

        self._export_sel_btn.setEnabled(False)
        self._export_status_lbl.setText("Generating PDFs…")
        self._export_status_lbl.setVisible(True)

        self._pdf_worker = _FitzPDFWorker(records, save_dir, save_path)
        self._pdf_worker.progress.connect(self._on_pdf_progress)
        self._pdf_worker.done.connect(self._on_pdf_done)
        self._pdf_worker.error.connect(self._on_pdf_error)
        self._pdf_worker.start()

    def _on_pdf_progress(self, current: int, total: int, filename: str) -> None:
        self._export_status_lbl.setText(f"Writing {current}/{total}: {filename}…")

    def _on_pdf_done(self, paths: list, records: list) -> None:
        """
        Called on the main thread after _FitzPDFWorker completes.

        For each generated PDF:
          1. Add it to the project via the event bus.
          2. Sync any plugin-level law tags into the project tag manager.
          3. After a brief delay (to let ADD_FILES settle), assign each document
             the specific query atoms that matched it — not all atoms from the
             full query, only the ones whose text actually appears in that record.
        """
        self._pdf_worker = None
        self._export_sel_btn.setEnabled(True)
        self._export_status_lbl.setVisible(False)

        if not paths:
            self.api.notify("No PDFs were generated.", level="warning")
            return

        pm = getattr(self.app_context, "project_manager", None)
        in_project = pm and getattr(pm, "project_filepath", None)

        if in_project:
            try:
                from core.events.domains.document_events import DocumentIntent, DocumentPayload
                self.api.event_bus.document_action_requested.emit(
                    DocumentIntent.ADD_FILES, DocumentPayload(paths=paths)
                )
            except Exception as e:
                self.api.notify(f"Could not add to project: {e}", level="warning")

            # Sync plugin-level law tags into the project tag manager
            try:
                all_law_tags = self.law_manager.tag_db.get_all_tags()
                if all_law_tags:
                    self.api.tags.sync_tags(all_law_tags)
            except Exception:
                pass

            # Delay per-doc tagging so ADD_FILES event is fully processed first.
            # paths and records are in the same order (FitzPDFWorker preserves it).
            paired = list(zip(paths, records))
            QTimer.singleShot(400, lambda: self._apply_per_doc_tags(paired))

        n = len(paths)
        self.api.notify(
            f"{'Saved and added' if in_project else 'Saved'} "
            f"{n} PDF{'s' if n != 1 else ''} to project.",
            level="success",
        )

    def _apply_per_doc_tags(self, paired: list[tuple[str, dict]]) -> None:
        """
        Assign per-document hit-term tags to each exported PDF.

        Each record carries _matched_terms set by _annotate_matched_terms.
        Terms are lower-cased, stripped of operators and punctuation, and
        created in the project tag manager if they don't exist yet.
        """
        for pdf_path, record in paired:
            terms = record.get("_matched_terms") or []
            for term in terms:
                clean = term.strip().lower()
                if not clean:
                    continue
                try:
                    tag_id = self.api.tags.ensure_tag(clean)
                    if tag_id is not None:
                        self.api.tags.assign_to_doc(pdf_path, tag_id)
                except Exception:
                    pass

    def _on_pdf_error(self, error: str) -> None:
        self._pdf_worker = None
        self._export_sel_btn.setEnabled(True)
        self._export_status_lbl.setVisible(False)
        self.api.notify(f"PDF export failed: {error}", level="error")

    # ------------------------------------------------------------------
    # Download tab logic
    # ------------------------------------------------------------------

    def _update_mun_mode(self, city_mode: bool):
        self._mun_city_widget.setVisible(city_mode)
        self._mun_subject_widget.setVisible(not city_mode)

    def _update_case_mode(self, search_mode: bool):
        self._case_search_widget.setVisible(search_mode)
        if search_mode:
            self.download_case_btn.setText("🔍  Search & Embed Case Law")
        else:
            self.download_case_btn.setText("⬇  Bulk Download & Embed Court")

    def _run_municipal_ingestion(self):
        if self._mun_mode_city.isChecked():
            state = self.state_input.text().strip()
            city = self.city_input.text().strip()
            if not state or not city:
                self.api.notify("Please enter both State and City.", level="warning")
                return
            file_id = f"{city.title()}_{state.upper()}"
            auto_tags = [city.title(), state.upper(), "municipal"]
            self._pending_auto_tag = (file_id, auto_tags)
            self._start_job(
                self.law_manager.index_real_jurisdiction,
                state, city, self._make_progress_callback(),
                job_name=f"Indexing {city.title()}, {state.upper()} laws",
            )
        else:
            states_raw = self.state_subj_input.text().strip()
            subject = self.subject_input.text().strip()
            if not states_raw or not subject:
                self.api.notify("Please enter State(s) and a Subject keyword.", level="warning")
                return
            if states_raw.upper() == "ALL":
                answer = QMessageBox.question(
                    self, "Search All States",
                    "Searching ALL states pulls from the entire LOCUS dataset and may take\n"
                    "a very long time. Make sure your subject query is specific.\n\nContinue?",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            import re as _re
            subject_tags = [
                t.strip('"') for t in _re.split(r'\s+(?:AND|OR|NOT)\s+|\s+', subject)
                if t.strip('"')
            ][:4]
            states_display = states_raw.upper() if len(states_raw) <= 20 else f"{states_raw[:20].upper()}…"
            self._pending_auto_tag = (None, subject_tags)
            self._start_job(
                self.law_manager.index_by_subject,
                states_raw, subject, self._make_progress_callback(),
                job_name=f"Indexing '{subject}' — {states_display}",
            )

    def _run_caselaw_ingestion(self):
        token = self.api_key_input.text().strip()
        if not token:
            QMessageBox.warning(self, "API Key Missing", "A CourtListener API token is required.")
            return
        court = self.court_input.text().strip()
        if not court:
            self.api.notify("Please enter a Court ID (e.g. scotus, ca9).", level="warning")
            return
        self.api.config.set("courtlistener_token", token)

        if self._case_mode_bulk.isChecked():
            # Bulk archive path — no query needed
            self._pending_auto_tag = (None, [court.upper(), "caselaw", "bulk"])
            self._start_job(
                self.law_manager.stream_bulk_caselaw,
                court, token, self._make_progress_callback(),
                job_name=f"Bulk download: {court.upper()}",
            )
        else:
            # Search API path
            query = self.query_input.text().strip()
            limit = self.limit_input.value()
            date_from = self.date_from_input.text().strip()
            date_to = self.date_to_input.text().strip()
            import re as _re
            query_tags = [
                t.strip('"') for t in _re.split(r'\s+(?:AND|OR|NOT)\s+|\s+', query)
                if t.strip('"')
            ][:4]
            auto_tags = [court.upper(), "caselaw"] + query_tags
            self._pending_auto_tag = (None, auto_tags)
            self._start_job(
                self.law_manager.index_courtlistener_caselaw,
                court, query, limit, token, date_from, date_to,
                self._make_progress_callback(),
                job_name=f"Fetching case law: {court.upper()}",
            )

    def _make_progress_callback(self):
        q = self._status_queue

        def _cb(msg: str, pct: float):
            q.put((msg, pct))

        return _cb

    def _start_job(self, fn, *args, job_name=""):
        self._set_download_ui_busy(True)
        self._status_lbl.setText(f"Starting: {job_name}…")
        self._progress_bar.setValue(0)
        self._active_job = self.api.tasks.run_background(
            fn, *args,
            job_name=job_name,
            on_done=self._on_job_done,
            on_error=self._on_job_error,
            pass_cancel_check=True,
        )

    def _cancel_job(self):
        if self._active_job and hasattr(self._active_job, "kill"):
            self._active_job.kill()
        elif self._active_job and hasattr(self._active_job, "requestInterruption"):
            self._active_job.requestInterruption()
        self._status_lbl.setText("Cancelling…")
        self._cancel_btn.setEnabled(False)

    def _on_job_done(self, result: dict):
        self._set_download_ui_busy(False)
        if isinstance(result, dict) and result.get("success"):
            self._status_lbl.setText("✅  Done! Database indexed and ready.")
            self._progress_bar.setValue(100)
            self.api.notify("Indexing complete!", level="success")
            self._apply_auto_tags(result)
            self.refresh_db_list()
            self._refresh_manage_tab()
        else:
            err = (result or {}).get("error", "Unknown error") if isinstance(result, dict) else str(result)
            self._status_lbl.setText(f"⚠  {err}")
            self._progress_bar.setValue(0)
            self.api.notify(f"Indexing error: {err}", level="error")
        self._pending_auto_tag = None

    def _apply_auto_tags(self, result: dict) -> None:
        pending = getattr(self, "_pending_auto_tag", None)
        if not pending:
            return
        file_id, tag_names = pending
        if not file_id:
            file_id = result.get("label") or result.get("file_id") or ""
        if not file_id or not tag_names:
            return
        try:
            self.law_manager.auto_tag_db(file_id, *tag_names)
        except Exception:
            pass

    def _on_job_error(self, error: str):
        self._set_download_ui_busy(False)
        self._status_lbl.setText(f"⚠  Error: {error}")
        self._progress_bar.setValue(0)
        self.api.notify(f"Indexing error: {error}", level="error")
        self._pending_auto_tag = None

    def _set_download_ui_busy(self, busy: bool):
        self.download_mun_btn.setEnabled(not busy)
        self.download_case_btn.setEnabled(not busy)
        self.ai_suggest_btn.setEnabled(not busy)
        self._cancel_btn.setEnabled(busy)
        if not busy:
            self._active_job = None

    # ---------- Status polling ----------

    def _poll_status_queue(self):
        try:
            while not self._status_queue.empty():
                msg, pct = self._status_queue.get_nowait()
                self._signaler.update.emit(msg, float(pct))
        except Exception:
            pass

    def _apply_status_update(self, msg: str, pct: float):
        self._status_lbl.setText(msg)
        self._progress_bar.setValue(int(min(max(pct, 0.0), 1.0) * 100))

    # ------------------------------------------------------------------
    # AI Query Assistant
    # ------------------------------------------------------------------

    def _run_ai_query_assist(self):
        issue = self.ai_issue_edit.toPlainText().strip()
        if not issue:
            self.api.notify("Please describe the legal issue first.", level="warning")
            return
        if not self.api.llm.ai_enabled:
            self.api.notify(
                "AI is not available — check that Ollama is running and a model is selected.",
                level="warning",
            )
            return

        bp_def = self.api.blueprints.get(self._query_blueprint_id)
        if bp_def is None:
            self.api.notify("Legal Query Assist blueprint not registered.", level="error")
            return

        _target_map = {
            "Case Law fields": "Case Law only — include ONLY the 'caselaw' JSON key, omit 'municipal'",
            "Municipal fields": "Municipal only — include ONLY the 'municipal' JSON key, omit 'caselaw'",
            "Both": (
                "Both — include BOTH 'caselaw' AND 'municipal' JSON keys. "
                "Never omit either key when this target is selected."
            ),
        }
        target = _target_map.get(self.ai_target_combo.currentText(), self.ai_target_combo.currentText())
        query_template = self.api.prompts.pm.get_prompt("Legal Research Query Assist") or (
            "Legal issue: {legal_issue}\nTarget: {query_target}"
        )
        formatted_query = (
            query_template
            .replace("{legal_issue}", issue)
            .replace("{query_target}", target)
        )

        self._ai_status_lbl.setText("🤖  Running Legal Query Assist…")
        self.ai_suggest_btn.setEnabled(False)

        blueprint = bp_def.create()
        self._ai_runner = self.api.workflow_runner.run_blueprint(
            blueprint,
            initial_state={"legal_query_prompt": formatted_query},
            job_name="Legal Query Assist",
            is_express=True,
        )
        self._ai_runner.action_complete.connect(self._on_ai_runner_complete)
        self._ai_runner.error.connect(self._on_ai_runner_error)

    def _on_ai_runner_complete(self, final_state: dict):
        last_trace_id = getattr(self._ai_runner, "trace_id", None) if self._ai_runner else None
        self._ai_runner = None
        if last_trace_id and hasattr(self, "_ai_trace_btn"):
            self._ai_trace_btn.set_trace_id(last_trace_id)

        raw = final_state.get("legal_query_suggestion", "")
        if not raw:
            self._ai_status_lbl.setText("⚠  AI returned no result.")
            self.ai_suggest_btn.setEnabled(True)
            return
        if isinstance(raw, dict):
            result = raw
        else:
            try:
                result = json.loads(str(raw))
            except Exception as e:
                self._ai_status_lbl.setText(f"⚠  Could not parse AI response: {e}")
                self.ai_suggest_btn.setEnabled(True)
                return
        self._apply_ai_suggestion(result)

    def _on_ai_runner_error(self, error: str):
        if hasattr(self, "_ai_trace_btn"):
            self._ai_trace_btn.setVisible(False)
        self._ai_runner = None
        self._ai_suggestion_error(error)

    def _apply_ai_suggestion(self, result: dict):
        self.ai_suggest_btn.setEnabled(True)
        if not isinstance(result, dict) or "error" in result:
            err = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
            self._ai_status_lbl.setText(f"⚠  {err}")
            return

        applied = []
        case_data = result.get("caselaw", {})
        if case_data:
            if case_data.get("court"):
                self.court_input.setText(case_data["court"])
                applied.append("Court ID")
            if case_data.get("query"):
                self.query_input.setText(case_data["query"])
                applied.append("Query")
            if case_data.get("date_from"):
                self.date_from_input.setText(case_data["date_from"])
                applied.append("Date from")
            if case_data.get("max_results"):
                self.limit_input.setValue(int(case_data["max_results"]))
                applied.append("Max results")

        mun_data = result.get("municipal", {})
        if mun_data:
            if mun_data.get("state"):
                self.state_subj_input.setText(mun_data["state"])
                self.state_input.setText(mun_data["state"])
                applied.append("State")
            if mun_data.get("subject"):
                self.subject_input.setText(mun_data["subject"])
                self._mun_mode_subject.setChecked(True)
                applied.append("Subject")

        if applied:
            self._ai_status_lbl.setText(f"✅  Auto-filled: {', '.join(applied)}")
        else:
            self._ai_status_lbl.setText("⚠  AI returned no recognizable fields.")

    def _ai_suggestion_error(self, error: str):
        self.ai_suggest_btn.setEnabled(True)
        self._ai_status_lbl.setText(f"⚠  AI error: {error}")

    def _open_prompt_manager(self):
        pm = getattr(getattr(self.api, "prompts", None), "pm", None)
        if not pm:
            self.api.notify("Prompt Manager not available.", level="warning")
            return
        from gui.components.dialogs.prompt_editor_dialog import PromptEditorDialog
        dialog = PromptEditorDialog(pm, self, app_context=self.app_context)
        dialog.exec()

    # ------------------------------------------------------------------
    # Manage tab logic
    # ------------------------------------------------------------------

    def _refresh_manage_tab(self):
        for i in reversed(range(self._manage_scroll_layout.count())):
            w = self._manage_scroll_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        self._manage_checkbox_map.clear()

        active_dbs = self.api.config.get("active_laws", [])
        dbs = self.law_manager.get_installed_dbs()

        if not dbs:
            self._manage_scroll_layout.addWidget(
                QLabel("No databases installed yet. Use the Download tab.")
            )
            return

        for db in dbs:
            is_complete = db.get("index_complete", True)
            status_icon = "✅" if is_complete else "⚠"
            tags = db.get("tags", [])

            container = QWidget()
            cl = QVBoxLayout(container)
            cl.setContentsMargins(0, 2, 0, 6)
            cl.setSpacing(2)

            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            icon = "🏛️" if db["type"] == "caselaw" else "📄"
            chk = QCheckBox(f"{icon}  {db['label']}  ({db['size_mb']} MB)  {status_icon}")
            chk.setChecked(db["file_id"] in active_dbs)
            chk.setToolTip(
                "Fully indexed — ready for AI search." if is_complete
                else "Partially indexed. Use 'Finish Indexing' to complete."
            )
            self._manage_checkbox_map[chk] = db["file_id"]
            rl.addWidget(chk, 1)

            tag_btn = QToolButton()
            tag_btn.setText("🏷")
            tag_btn.setToolTip("Assign/remove tags for this database")
            tag_btn.clicked.connect(
                lambda _, fid=db["file_id"], lbl=db["label"]: self._edit_db_tags(fid, lbl)
            )
            rl.addWidget(tag_btn)

            if not is_complete:
                finish_btn = QPushButton("▶ Finish Indexing")
                finish_btn.setFixedWidth(130)
                finish_btn.clicked.connect(lambda _, fid=db["file_id"]: self._resume_indexing(fid))
                rl.addWidget(finish_btn)

            del_btn = QPushButton("🗑 Delete")
            del_btn.setFixedWidth(80)
            del_btn.clicked.connect(lambda _, fid=db["file_id"]: self._delete_db(fid))
            rl.addWidget(del_btn)
            cl.addWidget(row)

            if tags:
                chip_row = QWidget()
                chip_layout = QHBoxLayout(chip_row)
                chip_layout.setContentsMargins(20, 0, 0, 0)
                chip_layout.setSpacing(4)
                chip_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
                for tag in tags:
                    chip = TagChip(
                        tag["id"], tag["name"], tag.get("color", "#607d8b"),
                        removable=False, parent=chip_row
                    )
                    chip_layout.addWidget(chip)
                chip_layout.addStretch()
                cl.addWidget(chip_row)

            self._manage_scroll_layout.addWidget(container)

    def _edit_db_tags(self, file_id: str, label: str) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Tags for: {label}")
        dlg.setMinimumWidth(320)
        vl = QVBoxLayout(dlg)

        all_tags = self.law_manager.tag_db.get_all_tags()
        current_tag_ids = {t["id"] for t in self.law_manager.tag_db.get_tags_for_db(file_id)}

        checks = {}
        for tag in all_tags:
            chk = QCheckBox(tag["name"])
            chk.setChecked(tag["id"] in current_tag_ids)
            c = QColor(tag.get("color", "#607d8b"))
            lum = 0.299 * c.redF() + 0.587 * c.greenF() + 0.114 * c.blueF()
            fg = "#111" if lum > 0.55 else "#eee"
            chk.setStyleSheet(
                f"QCheckBox {{ background: {tag['color']}; color: {fg}; "
                f"border-radius: 4px; padding: 3px 8px; }}"
            )
            checks[tag["id"]] = chk
            vl.addWidget(chk)

        if not all_tags:
            vl.addWidget(QLabel("No tags yet — use 'Manage Tags' to create some."))

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        vl.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_ids = [tid for tid, chk in checks.items() if chk.isChecked()]
        self.law_manager.tag_db.set_tags_for_db(file_id, new_ids)
        self._refresh_manage_tab()
        self.refresh_db_list()

    def _open_tag_manager(self) -> None:
        dlg = TagManagerDialog(
            self.law_manager.tag_db,
            on_changed=self._refresh_manage_tab,
            parent=self,
        )
        dlg.exec()
        self.refresh_db_list()

    def _set_all_manage_checks(self, state: bool):
        for chk in self._manage_checkbox_map:
            chk.setChecked(state)

    def _save_manage_settings(self):
        active = [fid for chk, fid in self._manage_checkbox_map.items() if chk.isChecked()]
        self.api.config.set("active_laws", active)
        self.api.notify(f"{len(active)} database(s) enabled for RAG.", level="success")

    def _delete_db(self, file_id: str):
        confirm = QMessageBox.question(
            self, "Confirm Delete",
            f'Permanently delete the database "{file_id}"?\nThis cannot be undone.',
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.law_manager.remove_db(file_id)
        active = self.api.config.get("active_laws", [])
        if file_id in active:
            active.remove(file_id)
            self.api.config.set("active_laws", active)
        self._refresh_manage_tab()
        self.refresh_db_list()
        self.api.notify(f"Deleted {file_id}.", level="info")

    def _resume_indexing(self, file_id: str):
        self.api.notify(f"Resuming indexing for {file_id}…", level="info")

        def _on_done(result):
            if isinstance(result, dict) and result.get("success"):
                n = result.get("newly_indexed", 0)
                msg = f"Indexing complete — {n} new record(s) embedded." if n else "Already fully indexed."
                self.api.notify(msg, level="success")
            else:
                err = (result or {}).get("error", "Unknown") if isinstance(result, dict) else str(result)
                self.api.notify(f"Indexing error: {err}", level="error")
            self._refresh_manage_tab()
            self.refresh_db_list()

        fn = lambda prog, cancel: self.law_manager.resume_indexing(file_id, prog, cancel)
        self.api.tasks.run_background(
            fn,
            job_name=f"Resume: {file_id}",
            on_done=_on_done,
            on_error=lambda e: (
                self.api.notify(f"Indexing error: {e}", level="error"),
                self._refresh_manage_tab(),
            ),
            pass_cancel_check=True,
        )

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def update_theme(self, theme: dict) -> None:
        bg = theme.get("bg_panel", "#202124")
        text = theme.get("text_main", "#ffffff")
        border = theme.get("border", "#3c4043")
        input_bg = theme.get("bg_input", "#292a2d")
        accent = theme.get("accent", "#1a73e8")
        alt_bg = theme.get("bg_alt", "#252628")
        muted = theme.get("text_muted", theme.get("text_secondary", "#9aa0a6"))

        self._bool_hint_lbl.setStyleSheet(
            f"background:{input_bg}; color:{muted}; border:1px solid {border}; "
            f"border-radius:4px; padding:4px 6px; font-size:11px;"
        )

        if hasattr(self, "_ai_configure_btn"):
            self._ai_configure_btn.setStyleSheet(
                f"background:transparent; color:{muted}; border:1px solid {border}; "
                f"border-radius:4px; padding:2px 8px; font-size:11px;"
            )
        if hasattr(self, "_ai_trace_btn"):
            self._ai_trace_btn.theme = theme
            self._ai_trace_btn.apply_theme()

        self._api_link_lbl.setText(
            f'<a href="https://www.courtlistener.com/profile/api/" '
            f'style="color:{accent};text-decoration:none;font-weight:bold">🔑 Get API Token</a>'
        )
        self._docs_link_lbl.setText(
            f'<a href="https://www.courtlistener.com/help/api/rest/v4/case-law/" '
            f'style="color:{accent};text-decoration:none;font-weight:bold">📚 Court IDs &amp; Guide</a>'
        )

        self.setStyleSheet(f"""
            QWidget {{ background-color: {bg}; color: {text}; }}
            QTabWidget::pane {{ border: 1px solid {border}; }}
            QTabBar::tab {{
                background: {input_bg}; color: {text};
                padding: 6px 14px; border: 1px solid {border}; border-bottom: none;
            }}
            QTabBar::tab:selected {{ background: {accent}; color: #ffffff; }}
            QGroupBox {{
                color: {accent}; font-weight: bold;
                border: 1px solid {border}; border-radius: 6px;
                margin-top: 14px; padding-top: 10px;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QComboBox, QLineEdit, QSpinBox, QTextEdit, QListWidget {{
                background-color: {input_bg}; color: {text};
                border: 1px solid {border}; border-radius: 4px; padding: 4px;
            }}
            QListWidget::item:selected {{ background-color: {accent}; color: #ffffff; }}
            QListWidget::item:alternate {{ background-color: {alt_bg}; }}
            QTextEdit[readOnly="true"] {{ background-color: {input_bg}; }}
            QPushButton {{
                background-color: {input_bg}; color: {text};
                border: 1px solid {border}; border-radius: 4px; padding: 6px 10px;
            }}
            QPushButton:hover:!disabled {{ background-color: {accent}; color: #ffffff; border-color: {accent}; }}
            QPushButton:disabled {{ color: gray; border-color: {border}; }}
            QProgressBar {{
                border: 1px solid {border}; border-radius: 3px;
                background-color: {input_bg}; text-align: center; color: {text};
            }}
            QProgressBar::chunk {{ background-color: {accent}; border-radius: 2px; }}
            QCheckBox {{ color: {text}; padding: 3px; }}
            QRadioButton {{ color: {text}; padding: 3px; }}
            QSplitter::handle {{ background-color: {border}; }}
            QScrollArea {{ border: none; }}
            QFrame[frameShape="4"] {{ color: {border}; }}
        """)
