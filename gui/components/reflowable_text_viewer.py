"""
gui/components/reflowable_text_viewer.py

Shared read-only viewer for every reflowable text format (docx, epub, txt,
md, html, rtf, doc) — these all render as flowed text with no fixed
pagination, so one widget class serves all of them (one instance per
format, each filtering source_opened events to its own source_type) rather
than a dedicated viewer per format.

Uses ReadableTextRenderer for consistent styling across all text formats,
respecting global display settings (font size, colors, line height).
Subscribes to SourceViewerSettingsService broadcasts for real-time updates.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout, QWidget

from core.events.domains.document_events import SourceEvent
from core.events.domains.gui_events import GuiEvent
from core.events.event_bus import EventBus
from core.models.display_settings import SourceViewerDisplaySettings
from gui.components.raw_block_view import RawBlockView
from gui.components.selection_source_loader import SelectionSourceLoader
from gui.components.text_annotation_manager import TextAnnotationManager


class ReflowableTextViewer(QWidget):
    """Read-only flowed-text viewer for one registered source_type."""

    def __init__(self, app_context=None, parent=None, source_type: str = ""):
        super().__init__(parent)
        self._ctx = app_context
        self._source_type = source_type
        self.bus = EventBus.get_instance()
        self.current_source_path = None
        self._display_settings: SourceViewerDisplaySettings = SourceViewerDisplaySettings()
        settings_service = getattr(app_context, 'source_viewer_settings_service', None)
        if settings_service:
            self._display_settings = settings_service.get_settings()
        self._reading_mode_active = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        self._title_label = QLabel("")
        self._title_label.setStyleSheet("font-weight: bold; padding: 4px;")
        toolbar.addWidget(self._title_label, 1)
        layout.addLayout(toolbar)

        self.browser = QTextBrowser()
        self.browser.setReadOnly(True)
        self.browser.setOpenExternalLinks(False)
        self.browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.browser)

        self.raw_view = RawBlockView(self.browser)
        from gui.components.cursor_pager import CursorPager
        self.pager = CursorPager(app_context, self)
        self.pager.register_actions('intelligence.source_text', 'toolbar:source:text')
        self.pager.requested.connect(self._load_page)
        layout.addWidget(self.pager)
        self.snapshot = {}
        self._pending_anchor = None
        self.loader = SelectionSourceLoader(app_context, self, self._snapshot_ready,
                                             self._load_failed, self._clear_source)
        self.annot_manager = TextAnnotationManager(self, app_context)

        # Subscribe to global display settings changes
        self.bus.gui_action_requested.connect(self._on_gui_action)
        self.bus.source_opened.connect(self._on_source_opened)

    def _on_context_menu(self, pos) -> None:
        self.annot_manager.show_context_menu(self.browser.mapToGlobal(pos))

    def _on_gui_action(self, event, payload):
        """Re-render when display settings change."""
        if event == GuiEvent.SOURCE_VIEWER_SETTINGS_CHANGED and payload.settings:
            self._display_settings = payload.settings
            if self.current_source_path:
                self._render()

    def _on_source_opened(self, event, payload):
        if event != SourceEvent.OPENED or payload.source_type != self._source_type:
            return
        service = getattr(self._ctx, 'source_viewer_settings_service', None)
        if service and service.get_settings().reading_mode == 'book' and not (payload.context or {}).get('source_anchor'):
            return
        self._pending_anchor = (payload.context or {}).get('source_anchor')
        self._load(payload.path)

    def _load(self, path: str) -> None:
        self.current_source_path = path
        self._title_label.setText(path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "")
        self.snapshot = {}
        self.raw_view.ranges = []
        self.browser.setPlainText("Loading extracted source text…")
        block_ids = [span['block_id'] for span in (self._pending_anchor or {}).get('spans') or [] if span.get('block_id')]
        self.pager.cursor, self.pager.next_cursor, self.pager.history = '', '', []
        self.loader.load(path, block_ids or None, text_page=True,
                         spans=(self._pending_anchor or {}).get('spans'))

    def _load_page(self):
        if self.current_source_path:
            self.loader.load(self.current_source_path, text_page=True, cursor=self.pager.cursor)

    def _snapshot_ready(self, snapshot):
        self.snapshot = snapshot
        self.pager.apply(snapshot)
        self._render()
        if self._pending_anchor:
            anchor, self._pending_anchor = self._pending_anchor, None
            if not self.raw_view.show_spans(anchor.get('spans') or []):
                self._title_label.setText("The saved location no longer matches these extracted blocks.")

    def _render(self):
        if self.snapshot:
            self.raw_view.render(self.snapshot['blocks'], self._display_settings,
                                 prepared=self.snapshot.get('rendered'), raw_offsets=self.snapshot.get('raw_offsets'))

    def _load_failed(self, error):
        self.snapshot = {}
        self.raw_view.ranges = []
        self._pending_anchor = None
        self.browser.setPlainText(error + "\nUse Intelligence → Processing to run source extraction, then reopen this source.")

    def _clear_source(self):
        self.current_source_path, self.snapshot, self._pending_anchor = None, {}, None
        self.raw_view.ranges = []
        self.browser.clear()

    def selected_source(self):
        selection = self.raw_view.selection()
        if not self.snapshot or not selection:
            return None
        return {**selection, **{key: self.snapshot[key] for key in
                ('source_id', 'source_path', 'source_hash', 'blocks_hash')}}

    def show_anchor(self, path, anchor):
        if path == self.current_source_path and self.snapshot and self.raw_view.show_spans(anchor.get('spans') or []):
            return True
        self._pending_anchor = anchor
        self._load(path)
        return True

    # ------------------------------------------------------------ zoom
    def zoom_in(self):
        self._display_settings.font_size_base = min(
            32, self._display_settings.font_size_base + 1
        )
        if self.current_source_path:
            self._render()

    def zoom_out(self):
        self._display_settings.font_size_base = max(8, self._display_settings.font_size_base - 1)
        if self.current_source_path:
            self._render()

    def zoom_reset(self):
        self._display_settings.font_size_base = SourceViewerDisplaySettings().font_size_base
        if self.current_source_path:
            self._render()

    # ------------------------------------------------------------ jump-to-source
    def toggle_reading_mode(self, checked: bool = None) -> None:
        """Reading mode not yet implemented for reflowable text; use inline mode in PDFViewer."""
        pass

    def jump_to_source(self, doc_name: str, quote: str) -> bool:
        """
        Fuzzy substring search over the rendered text — the reflowable-format
        analog of PDFViewer.jump_to_source's fuzzy full-text search.
        """
        if not quote:
            return False
        cursor = self.browser.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.browser.setTextCursor(cursor)
        found = self.browser.find(quote)
        if not found:
            words = quote.split()
            if len(words) > 6:
                cursor.movePosition(cursor.MoveOperation.Start)
                self.browser.setTextCursor(cursor)
                found = self.browser.find(" ".join(words[:6]))
        return bool(found)
