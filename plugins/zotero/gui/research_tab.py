"""
plugins/zotero/gui/research_tab.py

Zotero Library tab for the Research Assistant dock.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.components.base import ItemCardList, DetailPane
from core.events.event_bus import EventBus
from core.events.domains.document_events import DocumentIntent, DocumentPayload
from gui.managers.dialog_manager import exec_as_modal, get_for_widget

if TYPE_CHECKING:
    from gui.app_context import AppContext
    from ..zotero_db import ZoteroDB
    from ..zotero_formatter import ZoteroFormatter
    from ..zotero_library_cache import ZoteroLibraryCache


# ---------------------------------------------------------------------------
# Background loader — fetches items, collections, and attachments off the main thread
# ---------------------------------------------------------------------------

class _LibraryLoader(QThread):
    # items, collections, source, error
    finished = Signal(list, list, str, str)

    def __init__(
        self,
        db: "ZoteroDB",
        config: dict,
        cache_items: list,
        project_pdf_set: Set[str],
        collection_id: Optional[int] = None,
    ):
        super().__init__()
        self._db = db
        self._config = config or {}
        self._cache_items = cache_items or []
        self._project_pdf_set = project_pdf_set or set()
        self._collection_id = collection_id

    def run(self):
        source = "desktop"
        error = ""
        collections: List[Dict] = []

        # --- Priority: local API (Zotero running) → SQLite (Zotero closed) ---
        #
        # When Zotero is open it holds a write lock on zotero.sqlite.  Using
        # the local REST API avoids that lock entirely and always returns fresh
        # data.  When Zotero is closed the API is unreachable, so we fall back
        # to reading the SQLite directly (which is safe since no lock is held).

        # 1. Try local API first
        api_items: List[Dict] = []
        api_collections: List[Dict] = []
        local_api_ok = False
        try:
            api_items, api_collections = self._load_local_api_items_and_collections()
            local_api_ok = True
            source = "local_api"
        except Exception as exc:
            print(f"[ZoteroTab] Local API unavailable ({exc}), falling back to SQLite")

        if local_api_ok:
            items = api_items
            collections = api_collections
        else:
            # 2. Fallback: direct SQLite (only safe when Zotero is NOT running)
            try:
                if self._db and self._db.is_available():
                    items = self._db.get_items(collection_id=self._collection_id)
                    collections = self._db.get_collections()
                else:
                    items = []
                    error = "Zotero library not found. Open Zotero at least once to create the local database."
            except Exception as exc:
                error = f"Zotero library read failed: {exc}"
                print(f"[ZoteroTab] SQLite load error: {exc}")
                items = []

        # Pre-fetch attachments (off the main thread) and compute in-project status
        for item in items:
            if item.get("_source") == "local_api":
                item.setdefault("_attachments", [])
                item["_has_pdf"] = bool(item.get("_has_pdf"))
                item["_in_project"] = False
            else:
                try:
                    atts = self._db.get_attachments(item["item_id"])
                except Exception:
                    atts = []
                item["_attachments"] = atts
                item["_has_pdf"] = bool(atts)
                item["_in_project"] = any(
                    att.get("local_path") in self._project_pdf_set
                    for att in atts
                    if att.get("local_path")
                )

        # Merge in-memory write cache so recently-added items appear immediately
        if self._cache_items and self._collection_id is None:
            seen_keys = {
                str(it.get("key") or it.get("item_id") or it.get("doc_id") or "")
                for it in items
            }
            for cached in self._cache_items:
                key = str(cached.get("key") or cached.get("item_id") or cached.get("doc_id") or "")
                if key and key not in seen_keys:
                    cached.setdefault("_attachments", [])
                    cached.setdefault("_in_project", False)
                    items.append(cached)
                    seen_keys.add(key)

        self.finished.emit(items, collections, source, error)

    def _load_local_api_items_and_collections(self):
        """Return (items, collections) from the Zotero local REST API.

        Raises RuntimeError if the API is not reachable (Zotero not running).
        """
        from ..zotero_sync_adapter import PyZoteroClient, DEFAULT_ZOTERO_LOCAL_API_BASE_URL
        url = self._config.get("pyzotero_local_api_base_url", DEFAULT_ZOTERO_LOCAL_API_BASE_URL)
        client = PyZoteroClient(local_api_base_url=url)
        # Probe first — raises RuntimeError via _local_get_json if unreachable
        items = client.list_local_items()
        collections = client.list_local_collections()
        return items, collections


# ---------------------------------------------------------------------------
# Card render function — reads _in_project/_has_pdf flags set by the loader
# ---------------------------------------------------------------------------

def _render_item(item: dict) -> dict:
    title = item.get("title") or "(No title)"
    authors = item.get("authors_display", "")
    year = item.get("year", "")
    itype = item.get("item_type", "")

    subtitle_parts = []
    if authors:
        subtitle_parts.append(authors)
    if year:
        subtitle_parts.append(year)

    # In-project indicator takes priority over "has PDF"
    if item.get("_in_project"):
        status_badge = "✓ In Project"
    elif item.get("_has_pdf"):
        status_badge = "📎 PDF"
    else:
        status_badge = ""

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
    type_badge = type_labels.get(itype, itype) if itype else ""
    badge = " · ".join(b for b in [type_badge, status_badge] if b)

    return {
        "title": title,
        "subtitle": " · ".join(subtitle_parts),
        "badge": badge,
        "meta": "",
    }


# ---------------------------------------------------------------------------
# Main tab widget
# ---------------------------------------------------------------------------

class ZoteroResearchTab(QWidget):
    """
    Zotero Library browser for the Research Assistant dock tab.

    Features:
    - Collection filter combo (All Items + per-collection browsing)
    - In-project status indicators on item cards
    - "Add PDF to Project" button for items whose local PDF is not yet in the project
    - Live refresh of project status on document_added / pdf_removed events
    """

    def __init__(
        self,
        app_context: "AppContext",
        db: "ZoteroDB",
        formatter: "ZoteroFormatter",
        config=None,
        library_cache: "Optional[ZoteroLibraryCache]" = None,
        parent=None,
    ):
        super().__init__(parent)
        self._ctx = app_context
        self._bus = EventBus.get_instance()
        self._db = db
        self._formatter = formatter
        self._config = config
        self._library_cache = library_cache
        self._items: List[Dict] = []
        self._selected: Optional[Dict] = None
        self._loader: Optional[_LibraryLoader] = None
        self._theme: dict = {}
        self._project_pdfs: Set[str] = self._get_project_pdfs()
        self._build_ui()
        self._subscribe_events()
        self._load_library()

    # ------------------------------------------------------------------
    # Project PDF helpers
    # ------------------------------------------------------------------

    def _get_project_pdfs(self) -> Set[str]:
        pm = getattr(self._ctx, "project_manager", None)
        if pm:
            return set(getattr(pm, "pdfs", []))
        return set()

    def _subscribe_events(self):
        self._bus.document_added.connect(self._on_project_changed)
        self._bus.pdf_removed.connect(self._on_project_changed)

    def closeEvent(self, event):
        try:
            self._bus.document_added.disconnect(self._on_project_changed)
            self._bus.pdf_removed.disconnect(self._on_project_changed)
        except Exception:
            pass
        self._stop_loader()
        super().closeEvent(event)

    def _stop_loader(self):
        loader = self._loader
        self._loader = None
        if loader is None:
            return
        try:
            if loader.isRunning():
                loader.requestInterruption()
                if not loader.wait(1500):
                    loader.terminate()
                    loader.wait(500)
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 6, 8, 4)
        toolbar.setSpacing(6)

        self._collection_combo = QComboBox()
        self._collection_combo.setMinimumWidth(130)
        self._collection_combo.addItem("All Items", None)
        self._collection_combo.setToolTip("Filter by Zotero collection")
        self._collection_combo.currentIndexChanged.connect(self._on_collection_changed)
        toolbar.addWidget(self._collection_combo)

        self._status_lbl = QLabel("Loading Zotero library…")
        self._status_lbl.setObjectName("zoteroStatus")
        toolbar.addWidget(self._status_lbl, 1)

        self._refresh_btn = QPushButton("🔄")
        self._refresh_btn.setToolTip("Reload library")
        self._refresh_btn.setFixedWidth(30)
        self._refresh_btn.clicked.connect(self._load_library)
        toolbar.addWidget(self._refresh_btn)
        root.addLayout(toolbar)

        # --- splitter ---
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

        # --- action bar ---
        action_bar = QHBoxLayout()
        action_bar.setContentsMargins(8, 4, 8, 8)

        self._add_btn = QPushButton("+ Add PDF to Project")
        self._add_btn.setToolTip("Copy the attached Zotero PDF into the current project")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._on_add_to_project)
        action_bar.addWidget(self._add_btn, 2)

        self._import_btn = QPushButton("Match to Project PDF")
        self._import_btn.setToolTip("Link Zotero metadata to an existing project PDF via sync")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._on_import)
        action_bar.addWidget(self._import_btn)

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

    def _selected_collection_id(self) -> Optional[int]:
        idx = self._collection_combo.currentIndex()
        if idx <= 0:
            return None
        return self._collection_combo.itemData(idx)

    def _on_collection_changed(self, _idx: int):
        self._load_library()

    def _load_library(self):
        self._refresh_btn.setEnabled(False)
        self._status_lbl.setText("Loading…")
        self._stop_loader()

        cache_items = self._library_cache.items() if self._library_cache else []
        collection_id = self._selected_collection_id()
        self._loader = _LibraryLoader(
            self._db,
            self._config_snapshot(),
            cache_items,
            set(self._project_pdfs),
            collection_id=collection_id,
        )
        loader = self._loader
        loader.finished.connect(self._on_library_loaded)
        loader.finished.connect(lambda *_: self._clear_loader_ref(loader))
        loader.start()

    def _clear_loader_ref(self, loader):
        if self._loader is loader:
            self._loader = None
        try:
            loader.deleteLater()
        except RuntimeError:
            pass

    def _config_snapshot(self) -> dict:
        config = self._config
        if not config:
            return {}
        keys = (
            "pyzotero_local_api_base_url",
            "pyzotero_library_id",
            "pyzotero_library_type",
            "pyzotero_api_key",
        )
        return {key: config.get(key, "") for key in keys}

    def _on_library_loaded(
        self,
        items: List[Dict],
        collections: List[Dict],
        source: str = "desktop",
        error: str = "",
    ):
        self._refresh_btn.setEnabled(True)
        self._items = items
        self._card_list.set_items(items)

        # Repopulate collection combo, preserving the current selection
        self._collection_combo.blockSignals(True)
        current_id = self._selected_collection_id()
        self._collection_combo.clear()
        self._collection_combo.addItem("All Items", None)
        for col in collections:
            col_id = col.get("id") or col.get("collectionID") or col.get("key")
            col_name = col.get("name") or col.get("collectionName", "")
            if col_name and col_id is not None:
                self._collection_combo.addItem(col_name, col_id)
        if current_id is not None:
            for i in range(self._collection_combo.count()):
                if self._collection_combo.itemData(i) == current_id:
                    self._collection_combo.setCurrentIndex(i)
                    break
        self._collection_combo.blockSignals(False)

        n = len(items)
        if n:
            src_label = "local API" if source == "local_api" else "desktop library"
            self._status_lbl.setText(f"📚 {n} item{'s' if n != 1 else ''} ({src_label})")
        else:
            msg = "No Zotero items found."
            if error:
                msg += f" {error}"
            self._status_lbl.setText(msg)

        if self._theme:
            self._card_list.apply_theme(self._theme)
            self._detail.apply_theme(self._theme)

        # Refresh detail pane if the selected item is still in the list
        if self._selected:
            doc_id = self._selected.get("doc_id") or self._selected.get("item_id")
            updated = next(
                (it for it in self._items
                 if (it.get("doc_id") or it.get("item_id")) == doc_id),
                None,
            )
            if updated:
                self._selected = updated
                self._show_detail(updated)
                self._update_add_btn_state()

    # ------------------------------------------------------------------
    # Project status refresh (triggered by document_added / pdf_removed)
    # ------------------------------------------------------------------

    def _on_project_changed(self, *_):
        self._refresh_project_status()

    def _refresh_project_status(self):
        """Recompute in-project flags from live project state without reloading Zotero."""
        self._project_pdfs = self._get_project_pdfs()
        for item in self._items:
            atts = item.get("_attachments", [])
            item["_in_project"] = any(
                att.get("local_path") in self._project_pdfs
                for att in atts
                if att.get("local_path")
            )
        self._card_list.set_items(self._items)
        if self._selected:
            self._show_detail(self._selected)
        self._update_add_btn_state()

    # ------------------------------------------------------------------
    # Item selection
    # ------------------------------------------------------------------

    def _on_item_selected(self, item: Dict):
        self._selected = item
        self._import_btn.setEnabled(True)
        self._bibtex_btn.setEnabled(True)
        self._cite_btn.setEnabled(True)
        self._update_add_btn_state()
        self._show_detail(item)

    def _update_add_btn_state(self):
        item = self._selected
        if not item:
            self._add_btn.setEnabled(False)
            self._add_btn.setText("+ Add PDF to Project")
            return
        if item.get("_in_project"):
            self._add_btn.setEnabled(False)
            self._add_btn.setText("✓ PDF Already in Project")
        elif item.get("_has_pdf"):
            self._add_btn.setEnabled(True)
            self._add_btn.setText("+ Add PDF to Project")
        else:
            self._add_btn.setEnabled(False)
            self._add_btn.setText("No Local PDF Available")

    def _show_detail(self, item: Dict):
        # Use pre-fetched attachments; fall back to DB query only if missing
        attachments = item.get("_attachments") or []
        if not attachments and item.get("_source") != "local_api":
            try:
                attachments = self._db.get_attachments(item["item_id"])
            except Exception:
                pass

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

        abstract = item.get("abstractNote", "")
        if abstract:
            sections.append({"heading": "Abstract", "fields": [("", abstract)]})

        try:
            tags = self._db.get_item_tags(item["item_id"])
            if tags:
                sections.append({"heading": "Tags", "fields": [("", " · ".join(tags))]})
        except Exception:
            pass

        # Attachment section — show in-project status + open/add buttons
        if attachments:
            att_fields = []
            att_buttons = []
            for att in attachments:
                path = att.get("local_path")
                name = att.get("title") or os.path.basename(att.get("path") or "attachment")
                if path:
                    if path in self._project_pdfs:
                        att_fields.append(("PDF", f"✓ In Project — {name[:45]}"))
                    else:
                        att_buttons.append((
                            f"+ Add to Project: {name[:38]}",
                            lambda checked=False, p=path: self._add_pdf_to_project(p),
                        ))
                    att_buttons.append((
                        f"📄 Open: {name[:42]}",
                        lambda checked=False, p=path: self._open_pdf(p),
                    ))
                else:
                    att_fields.append(("PDF", f"⚠ No local file — {name[:40]}"))
            sections.append({
                "heading": "Attachments",
                "fields": att_fields,
                "buttons": att_buttons,
            })

        try:
            notes = self._db.get_notes(item["item_id"])
            if notes:
                import re
                import html as _html
                cleaned = [_html.unescape(re.sub(r"<[^>]+>", " ", n)).strip() for n in notes]
                cleaned = [c for c in cleaned if c]
                if cleaned:
                    from PySide6.QtWidgets import QTextEdit
                    note_widget = QTextEdit()
                    note_widget.setReadOnly(True)
                    note_widget.setMaximumHeight(100)
                    note_widget.setPlainText("\n\n---\n\n".join(cleaned))
                    sections.append({"heading": "Notes", "widget": note_widget})
        except Exception:
            pass

        self._detail.set_data(sections)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_add_to_project(self):
        item = self._selected
        if not item:
            return
        for att in item.get("_attachments") or []:
            path = att.get("local_path")
            if path and path not in self._project_pdfs:
                self._add_pdf_to_project(path)
                return

    def _add_pdf_to_project(self, path: str):
        try:
            self._bus.document_action_requested.emit(
                DocumentIntent.ADD_FILES,
                DocumentPayload(paths=[path]),
            )
            self._status_lbl.setText(f"✓ Added: {os.path.basename(path)}")
        except Exception as exc:
            self._status_lbl.setText(f"Error adding PDF: {exc}")
            print(f"[ZoteroTab] Add to project error: {exc}")

    def _on_import(self):
        item = self._selected
        if not item:
            return
        try:
            from .sync_dialog import ZoteroSyncDialog
            dialog = ZoteroSyncDialog(
                db=self._db,
                formatter=self._formatter,
                project_manager=getattr(self._ctx, "project_manager", None),
                parent=self,
            )
            dm = getattr(self._ctx, "dialog_manager", None) or get_for_widget(self)
            if dm:
                dm.show_instance(dialog)
            else:
                exec_as_modal(dialog)
        except Exception as exc:
            print(f"[ZoteroTab] Could not open sync dialog: {exc}")
        self._status_lbl.setText("Use sync to match Zotero metadata to project PDFs")

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
        self._collection_combo.setStyleSheet(
            f"QComboBox {{ background: {panel}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 2px 6px; }} "
            f"QComboBox QAbstractItemView {{ background: {panel}; color: {text}; "
            f"border: 1px solid {border}; selection-background-color: {accent}; }}"
        )
        btn_style = (
            f"background: {panel}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 5px 10px;"
        )
        primary_style = (
            f"background: {accent}; color: #fff; border: none; "
            f"border-radius: 4px; padding: 5px 10px; font-weight: bold;"
        )
        self._add_btn.setStyleSheet(primary_style)
        self._import_btn.setStyleSheet(btn_style)
        self._bibtex_btn.setStyleSheet(btn_style)
        self._cite_btn.setStyleSheet(btn_style)

        self._card_list.apply_theme(theme)
        self._detail.apply_theme(theme)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
