"""
plugins/zotero/gui/item_detail.py

Detail panel for a selected Zotero item.
Shows all metadata fields, notes, attachments, and action buttons.
Emits signals for "Import to Project" and "Open in Viewer" actions.
"""
from __future__ import annotations

import html
from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ..zotero_db import ZoteroDB
    from ..zotero_formatter import ZoteroFormatter


class ItemDetailPanel(QWidget):
    """
    Shows full metadata for a single Zotero item.

    Signals:
      import_requested(item_dict)         — user clicked "Import to Project"
      open_pdf_requested(local_path: str) — user clicked "Open in Viewer"
      copy_bibtex_requested(item_dict)
      copy_citation_requested(item_dict)
    """

    import_requested = Signal(object)
    open_pdf_requested = Signal(str)
    copy_bibtex_requested = Signal(object)
    copy_citation_requested = Signal(object)

    def __init__(self, db: "ZoteroDB", formatter: "ZoteroFormatter", parent=None):
        super().__init__(parent)
        self._db = db
        self._formatter = formatter
        self._current_item: Optional[Dict] = None
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable metadata area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 8, 8, 4)
        scroll.setWidget(self._content)
        outer.addWidget(scroll, 1)

        self._placeholder = QLabel("Select an item to see details")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setObjectName("placeholder")
        self._content_layout.addWidget(self._placeholder)
        self._content_layout.addStretch()

        # Action buttons bar (always visible)
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(8, 4, 8, 8)

        self.btn_import = QPushButton("⬇ Import to Project")
        self.btn_import.setEnabled(False)
        self.btn_import.clicked.connect(self._on_import)

        self.btn_bibtex = QPushButton("Copy BibTeX")
        self.btn_bibtex.setEnabled(False)
        self.btn_bibtex.clicked.connect(self._on_copy_bibtex)

        self.btn_citation = QPushButton("Copy Citation")
        self.btn_citation.setEnabled(False)
        self.btn_citation.clicked.connect(self._on_copy_citation)

        btn_bar.addWidget(self.btn_import, 2)
        btn_bar.addWidget(self.btn_bibtex, 1)
        btn_bar.addWidget(self.btn_citation, 1)
        outer.addLayout(btn_bar)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_item(self, item: Optional[Dict]) -> None:
        """Populate the panel with data for a Zotero item, or clear if None."""
        self._current_item = item
        self._clear_content()

        enabled = item is not None
        self.btn_import.setEnabled(enabled)
        self.btn_bibtex.setEnabled(enabled)
        self.btn_citation.setEnabled(enabled)

        if item is None:
            self._placeholder.show()
            self._content_layout.addStretch()
            return

        self._placeholder.hide()
        self._build_metadata_section(item)
        self._build_attachments_section(item)
        self._build_notes_section(item)
        self._content_layout.addStretch()

    def apply_theme(self, theme: dict) -> None:
        self._theme = theme
        bg = theme.get("bg_input", "#00212b")
        text = theme.get("text_main", "#93a1a1")
        muted = theme.get("text_muted", "#586e75")
        accent = theme.get("accent", "#268bd2")
        border = theme.get("border", "#586e75")
        panel = theme.get("bg_panel", "#073642")

        self.setStyleSheet(f"background: {bg}; color: {text};")
        self._content.setStyleSheet(f"background: {bg};")

        btn_style = (
            f"background: {panel}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 6px 10px;"
        )
        primary_style = (
            f"background: {accent}; color: #fff; border: none; "
            f"border-radius: 4px; padding: 6px 10px; font-weight: bold;"
        )
        self.btn_import.setStyleSheet(primary_style)
        self.btn_bibtex.setStyleSheet(btn_style)
        self.btn_citation.setStyleSheet(btn_style)

        if hasattr(self, "_placeholder"):
            self._placeholder.setStyleSheet(f"color: {muted}; background: transparent;")

    # ------------------------------------------------------------------
    # Content builders
    # ------------------------------------------------------------------

    def _build_metadata_section(self, item: Dict) -> None:
        title_lbl = QLabel(item.get("title", "(No title)"))
        title_lbl.setWordWrap(True)
        title_lbl.setObjectName("itemTitle")
        self._content_layout.addWidget(title_lbl)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(4)

        def _row(label: str, value: str):
            if not value:
                return
            val_lbl = QLabel(value)
            val_lbl.setWordWrap(True)
            form.addRow(label + ":", val_lbl)

        _row("Type", _human_type(item.get("item_type", "")))
        _row("Authors", item.get("authors_display", ""))
        _row("Year", item.get("year", ""))
        journal = item.get("publicationTitle") or item.get("publisher", "")
        _row("Publication", journal)
        vol = item.get("volume", "")
        iss = item.get("issue", "")
        vol_str = f"{vol}({iss})" if vol and iss else vol or iss
        _row("Vol/Issue", vol_str)
        _row("Pages", item.get("pages", ""))
        _row("DOI", item.get("DOI", ""))
        _row("URL", item.get("url", ""))
        _row("ISBN", item.get("ISBN", ""))
        _row("Place", item.get("place", ""))
        _row("Edition", item.get("edition", ""))
        _row("Series", item.get("series", ""))
        _row("Language", item.get("language", ""))

        abstract = item.get("abstractNote", "")
        if abstract:
            ab_lbl = QLabel("Abstract")
            ab_lbl.setObjectName("sectionLabel")
            self._content_layout.addWidget(ab_lbl)
            ab_text = QLabel(abstract)
            ab_text.setWordWrap(True)
            ab_text.setObjectName("abstractText")
            self._content_layout.addWidget(ab_text)

        tags = self._db.get_item_tags(item["item_id"]) if self._db.is_available() else []
        if tags:
            _row("Tags", " · ".join(tags))

        self._content_layout.addLayout(form)

    def _build_attachments_section(self, item: Dict) -> None:
        if not self._db.is_available():
            return
        attachments = self._db.get_attachments(item["item_id"])
        if not attachments:
            return

        sep = QLabel("Attachments")
        sep.setObjectName("sectionLabel")
        self._content_layout.addWidget(sep)

        for att in attachments:
            row = QHBoxLayout()
            name = att.get("title") or att.get("path") or "attachment"
            lbl = QLabel(f"📄 {name}")
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            if att.get("local_path"):
                btn = QPushButton("Open")
                btn.setFixedWidth(60)
                btn.clicked.connect(
                    lambda checked, p=att["local_path"]: self.open_pdf_requested.emit(p)
                )
                row.addWidget(btn)
            self._content_layout.addLayout(row)

    def _build_notes_section(self, item: Dict) -> None:
        if not self._db.is_available():
            return
        notes = self._db.get_notes(item["item_id"])
        if not notes:
            return

        sep = QLabel("Notes")
        sep.setObjectName("sectionLabel")
        self._content_layout.addWidget(sep)

        for note_html in notes:
            plain = _strip_html(note_html)
            if not plain.strip():
                continue
            te = QTextEdit()
            te.setPlainText(plain)
            te.setReadOnly(True)
            te.setMaximumHeight(100)
            self._content_layout.addWidget(te)

    def _clear_content(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget and widget is not self._placeholder:
                widget.deleteLater()
            elif item.layout():
                _clear_layout(item.layout())

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_import(self):
        if self._current_item:
            self.import_requested.emit(self._current_item)

    def _on_copy_bibtex(self):
        if self._current_item:
            bib = self._formatter.to_bibtex(self._current_item)
            QApplication.clipboard().setText(bib)
            self.copy_bibtex_requested.emit(self._current_item)

    def _on_copy_citation(self):
        if self._current_item:
            self.copy_citation_requested.emit(self._current_item)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _strip_html(text: str) -> str:
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(clean).strip()


def _human_type(ztype: str) -> str:
    labels = {
        "journalArticle": "Journal Article",
        "book": "Book",
        "bookSection": "Book Section",
        "conferencePaper": "Conference Paper",
        "thesis": "Thesis",
        "report": "Report",
        "webpage": "Webpage",
        "magazineArticle": "Magazine Article",
        "newspaperArticle": "Newspaper Article",
        "patent": "Patent",
        "dataset": "Dataset",
    }
    return labels.get(ztype, ztype)


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget() if item else None
        if w:
            w.deleteLater()
        elif item and item.layout():
            _clear_layout(item.layout())
