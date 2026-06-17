"""
plugins/zotero/gui/research_tab.py

Zotero Library tab for the Research Assistant dock.

Shows all Zotero items in a searchable card list (left) with a detail
pane (right). Uses the generic ItemCardList and DetailPane widgets from
gui.components.base so minimal raw PySide6 is needed here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.components.base import ItemCardList, DetailPane
from core.events.event_bus import EventBus
from core.events.domains.tool_events import CitationIntent, CitationPayload

if TYPE_CHECKING:
    from gui.app_context import AppContext
    from ..zotero_db import ZoteroDB
    from ..zotero_formatter import ZoteroFormatter


# ---------------------------------------------------------------------------
# Background loader
# ---------------------------------------------------------------------------

class _LibraryLoader(QThread):
    finished = Signal(list)

    def __init__(self, db: "ZoteroDB"):
        super().__init__()
        self._db = db

    def run(self):
        try:
            items = self._db.get_items()
        except Exception as exc:
            print(f"[ZoteroTab] Load error: {exc}")
            items = []
        self.finished.emit(items)


# ---------------------------------------------------------------------------
# Card render function — pure function, no PySide6 state
# ---------------------------------------------------------------------------

def _render_item(item: dict) -> dict:
    """Map a raw Zotero item dict to an ItemCardList display dict."""
    title = item.get("title") or "(No title)"
    authors = item.get("authors_display", "")
    year = item.get("year", "")
    itype = item.get("item_type", "")

    subtitle_parts = []
    if authors:
        subtitle_parts.append(authors)
    if year:
        subtitle_parts.append(year)

    has_pdf = bool(item.get("_has_pdf"))
    meta_parts = []
    if has_pdf:
        meta_parts.append("📎 PDF attached")

    type_labels = {
        "journalArticle": "Article",
        "book": "Book",
        "bookSection": "Chapter",
        "conferencePaper": "Conference",
        "thesis": "Thesis",
        "report": "Report",
        "webpage": "Web",
        "dataset": "Dataset",
    }
    badge = type_labels.get(itype, itype) if itype else ""

    return {
        "title": title,
        "subtitle": " · ".join(subtitle_parts),
        "badge": badge,
        "meta": " · ".join(meta_parts),
    }


# ---------------------------------------------------------------------------
# Main tab widget
# ---------------------------------------------------------------------------

class ZoteroResearchTab(QWidget):
    """
    Zotero Library browser for the Research Assistant dock tab.

    Left:  ItemCardList with all Zotero items (filterable).
    Right: DetailPane showing selected item metadata + action buttons.
    """

    def __init__(
        self,
        app_context: "AppContext",
        db: "ZoteroDB",
        formatter: "ZoteroFormatter",
        parent=None,
    ):
        super().__init__(parent)
        self._ctx = app_context
        self._bus = EventBus.get_instance()
        self._db = db
        self._formatter = formatter
        self._items: List[Dict] = []
        self._selected: Optional[Dict] = None
        self._loader: Optional[_LibraryLoader] = None
        self._theme: dict = {}
        self._build_ui()
        self._load_library()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 6, 8, 4)
        self._status_lbl = QLabel("Loading Zotero library…")
        self._status_lbl.setObjectName("zoteroStatus")
        toolbar.addWidget(self._status_lbl, 1)

        self._refresh_btn = QPushButton("🔄")
        self._refresh_btn.setToolTip("Reload library")
        self._refresh_btn.setFixedWidth(30)
        self._refresh_btn.clicked.connect(self._load_library)
        toolbar.addWidget(self._refresh_btn)
        root.addLayout(toolbar)

        # Main splitter: card list | detail pane
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._card_list = ItemCardList(
            render_fn=_render_item,
            placeholder="Search title, author, year…",
        )
        self._card_list.item_selected.connect(self._on_item_selected)
        splitter.addWidget(self._card_list)

        self._detail = DetailPane()
        splitter.addWidget(self._detail)

        splitter.setSizes([300, 250])
        root.addWidget(splitter, 1)

        # Bottom action bar (always visible)
        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(8, 4, 8, 8)

        self._import_btn = QPushButton("⬇ Import to Citations")
        self._import_btn.setToolTip("Add this item's metadata to the Citation Manager")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._on_import)
        action_bar.addWidget(self._import_btn, 2)

        self._bibtex_btn = QPushButton("Copy BibTeX")
        self._bibtex_btn.setEnabled(False)
        self._bibtex_btn.clicked.connect(self._on_copy_bibtex)
        action_bar.addWidget(self._bibtex_btn)

        self._cite_btn = QPushButton("Copy Citation")
        self._cite_btn.setEnabled(False)
        self._cite_btn.clicked.connect(self._on_copy_citation)
        action_bar.addWidget(self._cite_btn)

        root.addLayout(action_bar)

    # ------------------------------------------------------------------
    # Library loading
    # ------------------------------------------------------------------

    def _load_library(self):
        if not self._db.is_available():
            self._status_lbl.setText("⚠️ Zotero library not found")
            return
        self._refresh_btn.setEnabled(False)
        self._status_lbl.setText("Loading…")
        if self._loader and self._loader.isRunning():
            self._loader.terminate()
        self._loader = _LibraryLoader(self._db)
        self._loader.finished.connect(self._on_library_loaded)
        self._loader.start()

    def _on_library_loaded(self, items: List[Dict]):
        self._refresh_btn.setEnabled(True)
        # Tag each item with whether it has a PDF attachment (single pass)
        for item in items:
            try:
                atts = self._db.get_attachments(item["item_id"])
                item["_has_pdf"] = bool(atts)
            except Exception:
                item["_has_pdf"] = False

        self._items = items
        self._card_list.set_items(items)
        n = len(items)
        self._status_lbl.setText(f"📚 {n} item{'s' if n != 1 else ''} in library")

        if self._theme:
            self._card_list.apply_theme(self._theme)
            self._detail.apply_theme(self._theme)

    # ------------------------------------------------------------------
    # Item selection
    # ------------------------------------------------------------------

    def _on_item_selected(self, item: Dict):
        self._selected = item
        self._import_btn.setEnabled(True)
        self._bibtex_btn.setEnabled(True)
        self._cite_btn.setEnabled(True)
        self._show_detail(item)

    def _show_detail(self, item: Dict):
        """Populate the detail pane from the selected Zotero item."""
        cit = self._formatter.to_citation_dict(item)

        # Collect attachment info
        attachments = []
        try:
            attachments = self._db.get_attachments(item["item_id"])
        except Exception:
            pass

        # Build PDF "Open" buttons for each local attachment
        pdf_buttons = []
        for att in attachments:
            if att.get("local_path"):
                path = att["local_path"]
                name = att.get("title") or os.path.basename(path)
                pdf_buttons.append((f"📄 Open: {name[:40]}", lambda p=path: self._open_pdf(p)))

        sections = [
            {
                "heading": "Metadata",
                "fields": [
                    ("Title", item.get("title")),
                    ("Authors", item.get("authors_display")),
                    ("Year", item.get("year")),
                    ("Type", _type_label(item.get("item_type", ""))),
                    ("Publication", item.get("publicationTitle") or item.get("publisher")),
                    ("DOI", item.get("DOI")),
                    ("URL", item.get("url")),
                    ("ISBN", item.get("ISBN")),
                ],
            },
        ]

        # Abstract
        abstract = item.get("abstractNote", "")
        if abstract:
            sections.append({
                "heading": "Abstract",
                "fields": [("", abstract)],
            })

        # Tags
        try:
            tags = self._db.get_item_tags(item["item_id"])
            if tags:
                sections.append({
                    "heading": "Tags",
                    "fields": [("", " · ".join(tags))],
                })
        except Exception:
            pass

        # Attachments
        if pdf_buttons:
            sections.append({
                "heading": "Attachments",
                "buttons": pdf_buttons,
            })

        # Notes
        try:
            notes = self._db.get_notes(item["item_id"])
            if notes:
                import re, html as _html
                cleaned = [_html.unescape(re.sub(r"<[^>]+>", " ", n)).strip() for n in notes]
                cleaned = [c for c in cleaned if c]
                if cleaned:
                    from PySide6.QtWidgets import QTextEdit
                    note_widget = QTextEdit()
                    note_widget.setReadOnly(True)
                    note_widget.setMaximumHeight(100)
                    note_widget.setPlainText("\n\n---\n\n".join(cleaned))
                    sections.append({
                        "heading": "Notes",
                        "widget": note_widget,
                    })
        except Exception:
            pass

        self._detail.set_data(sections)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_import(self):
        item = self._selected
        if not item:
            return
        cit = self._formatter.to_citation_dict(item)
        self._bus.citation_action_requested.emit(
            CitationIntent.UPDATE_ENTRY,
            CitationPayload(data=cit),
        )
        self._status_lbl.setText(f"✓ Imported: {item.get('title', '')[:50]}")

    def _on_copy_bibtex(self):
        item = self._selected
        if not item:
            return
        bib = self._formatter.to_bibtex(item)
        QApplication.clipboard().setText(bib)
        self._status_lbl.setText("✓ BibTeX copied")

    def _on_copy_citation(self):
        item = self._selected
        if not item:
            return
        try:
            cm = self._ctx.citation_manager
            cit = self._formatter.to_citation_dict(item)
            text = cm.format_entry(cit)
        except Exception:
            cit = self._formatter.to_citation_dict(item)
            text = f"{cit.get('authors', '')} ({cit.get('year', '')}). {cit.get('title', '')}."
        QApplication.clipboard().setText(text)
        self._status_lbl.setText("✓ Citation copied")

    def _open_pdf(self, path: str):
        try:
            from core.events.domains.document_events import DocumentIntent, DocumentPayload
            self._bus.document_action_requested.emit(
                DocumentIntent.OPEN,
                DocumentPayload(path=path),
            )
        except Exception as exc:
            print(f"[ZoteroTab] Could not open PDF: {exc}")

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def update_theme(self, theme: dict):
        self._theme = theme
        t = theme
        bg = t.get("bg_main", "#002b36")
        text = t.get("text_main", "#93a1a1")
        muted = t.get("text_muted", "#586e75")
        border = t.get("border", "#586e75")
        panel = t.get("bg_panel", "#073642")
        accent = t.get("accent", "#268bd2")

        self.setStyleSheet(f"background: {bg}; color: {text};")
        self._status_lbl.setStyleSheet(f"color: {muted}; background: transparent;")
        self._refresh_btn.setStyleSheet(
            f"background: {panel}; color: {text}; border: 1px solid {border}; border-radius: 4px;"
        )
        btn_style = (
            f"background: {panel}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 5px 10px;"
        )
        primary_style = (
            f"background: {accent}; color: #fff; border: none; "
            f"border-radius: 4px; padding: 5px 10px; font-weight: bold;"
        )
        self._import_btn.setStyleSheet(primary_style)
        self._bibtex_btn.setStyleSheet(btn_style)
        self._cite_btn.setStyleSheet(btn_style)

        self._card_list.apply_theme(theme)
        self._detail.apply_theme(theme)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import os


def _type_label(itype: str) -> str:
    labels = {
        "journalArticle": "Journal Article",
        "book": "Book",
        "bookSection": "Book Section / Chapter",
        "conferencePaper": "Conference Paper",
        "thesis": "Thesis",
        "report": "Report",
        "webpage": "Webpage",
        "magazineArticle": "Magazine Article",
        "newspaperArticle": "Newspaper Article",
        "patent": "Patent",
        "dataset": "Dataset",
    }
    return labels.get(itype, itype)
