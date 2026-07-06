"""
BookViewWidget — paginated text viewer with page-flip navigation.
Splits text into pages via PaginationService and renders each with ReadableTextRenderer.
Supports arrow keys, page indicator, smooth page-flip animation.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.events.domains.document_events import DocumentIntent, DocumentPayload, SourceEvent
from core.events.event_bus import EventBus
from core.models.display_settings import SourceViewerDisplaySettings
from core.services.extraction.pagination_service import PageContent, PaginationService
from gui.components.readable_text_renderer import ReadableTextRenderer


class BookViewWidget(QWidget):
    """
    Paginated text viewer — displays one page at a time with navigation controls.
    Emits the same highlight/annotation intents as other viewers so they're
    automatically persisted and queryable across all source types.
    """

    def __init__(self, app_context=None, parent=None):
        super().__init__(parent)
        self._ctx = app_context
        self.bus = EventBus.get_instance()
        self._pages: list[PageContent] = []
        self._current_page_index: int = 0
        self._text_browser: Optional[QTextBrowser] = None
        self._current_source_path: Optional[str] = None
        self._display_settings: Optional[SourceViewerDisplaySettings] = None

        self._init_ui()
        self.bus.source_opened.connect(self._on_source_opened)
        self.bus.gui_action_requested.connect(self._on_gui_action)

    def _init_ui(self) -> None:
        """Build the UI: text display + navigation toolbar."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Top toolbar: title + page indicator
        toolbar = QHBoxLayout()
        self._title_label = QLabel("")
        self._title_label.setStyleSheet("font-weight: bold; padding: 4px;")
        toolbar.addWidget(self._title_label, 1)

        self._page_indicator = QLabel("0 / 0")
        self._page_indicator.setStyleSheet("color: #666; font-size: 11px; padding: 4px;")
        toolbar.addWidget(self._page_indicator)
        layout.addLayout(toolbar)

        # Text display
        self._text_browser = QTextBrowser()
        self._text_browser.setReadOnly(True)
        layout.addWidget(self._text_browser)

        # Bottom toolbar: navigation buttons
        nav_toolbar = QHBoxLayout()
        nav_toolbar.setContentsMargins(8, 8, 8, 8)

        self._prev_btn = QPushButton("← Previous")
        self._prev_btn.clicked.connect(self.previous_page)
        nav_toolbar.addWidget(self._prev_btn)

        self._page_input = QLabel("")
        self._page_input.setStyleSheet("padding: 0 8px;")
        nav_toolbar.addWidget(self._page_input)

        self._next_btn = QPushButton("Next →")
        self._next_btn.clicked.connect(self.next_page)
        nav_toolbar.addWidget(self._next_btn)

        nav_toolbar.addStretch()
        layout.addLayout(nav_toolbar)

        self.setLayout(layout)

    def _on_source_opened(self, event, payload):
        """Load a source for book-view display."""
        if event != SourceEvent.OPENED or payload.source_type not in (
            "txt",
            "md",
            "html",
            "docx",
            "epub",
            "rtf",
            "doc",
        ):
            return
        self._load(payload.path, payload.source_type)

    def _on_gui_action(self, event, payload):
        """Re-render when display settings change (including dark/theme mode)."""
        from core.events.domains.gui_events import GuiEvent
        if event == GuiEvent.SOURCE_VIEWER_SETTINGS_CHANGED:
            settings = getattr(payload, 'settings', None)
            if settings:
                self._display_settings = settings
                if self._pages:
                    self._render_page()

    def _on_gui_action(self, event, payload):
        """Re-render when display settings change."""
        from core.events.domains.gui_events import GuiEvent
        if event == GuiEvent.SOURCE_VIEWER_SETTINGS_CHANGED:
            self._display_settings = getattr(payload, 'settings', self._display_settings)
            if self._current_source_path:
                self._render_page()

    def _load(self, path: str, source_type: str) -> None:
        """Load and paginate a file (text or PDF) using appropriate parser."""
        self._current_source_path = path
        print(f"[BookViewWidget] Loading {source_type} file: {path}")

        # Extract text based on source type
        text = None
        try:
            if source_type == "pdf":
                text = self._extract_pdf_text(path)
            elif source_type == "epub":
                text = self._extract_epub_text(path)
            elif source_type == "docx":
                text = self._extract_docx_text(path)
            elif source_type == "doc":
                text = self._extract_legacy_doc_text(path)
            else:
                # txt, md, html, rtf: treat as raw text
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                print(f"[BookViewWidget] Read {len(text)} chars from {source_type}")
        except Exception as e:
            print(f"[BookViewWidget] Error loading file: {e}")
            if self._text_browser:
                self._text_browser.setText(f"<p>Error loading file: {e}</p>")
            import traceback
            traceback.print_exc()
            return

        if not text:
            print(f"[BookViewWidget] No text content found in {path}")
            if self._text_browser:
                self._text_browser.setText("<p>No text content found in file.</p>")
            return

        # Paginate using settings from SourceViewerSettingsService
        service = getattr(self._ctx, "source_viewer_settings_service", None)
        if service:
            self._display_settings = service.get_settings()
        else:
            from core.models.display_settings import SourceViewerDisplaySettings

            self._display_settings = SourceViewerDisplaySettings()

        self._pages = PaginationService.paginate(text, path, self._display_settings)
        self._current_page_index = 0
        print(f"[BookViewWidget] Paginated into {len(self._pages)} pages")

        # Set title
        filename = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else ""
        self._title_label.setText(filename)

        # Render first page
        self._render_page()

    def _extract_pdf_text(self, path: str) -> Optional[str]:
        """Extract text from PDF file."""
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(path)
            pages_text = []
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text("text")
                if text:
                    pages_text.append(text)
            doc.close()

            if not pages_text:
                return None

            # Join pages with section breaks
            full_text = "\n\n---PAGE BREAK---\n\n".join(pages_text)
            print(f"[BookViewWidget] Extracted {len(full_text)} chars from PDF with {len(pages_text)} pages")
            return full_text
        except Exception as e:
            print(f"[BookViewWidget] PDF extraction error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _extract_epub_text(self, path: str) -> Optional[str]:
        """Extract text from EPUB file."""
        try:
            import ebooklib
            from ebooklib import epub
            from bs4 import BeautifulSoup

            book = epub.read_epub(path)
            chapters = []
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    try:
                        content = item.get_content()
                        soup = BeautifulSoup(content, "html.parser")
                        text = soup.get_text(separator=" ", strip=True)
                        if text:
                            chapters.append(text)
                    except Exception as item_err:
                        print(f"[BookViewWidget] Error extracting EPUB item: {item_err}")
                        continue

            if not chapters:
                print(f"[BookViewWidget] No text extracted from EPUB {path}")
                return None

            full_text = "\n\n".join(chapters)
            print(f"[BookViewWidget] Extracted {len(full_text)} chars from EPUB in {len(chapters)} sections")
            return full_text
        except Exception as e:
            print(f"[BookViewWidget] EPUB extraction error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _extract_docx_text(self, path: str) -> Optional[str]:
        """Extract text from DOCX file."""
        try:
            from docx import Document

            doc = Document(path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n".join(paragraphs)
        except Exception as e:
            print(f"[BookViewWidget] DOCX extraction error: {e}")
            return None

    def _extract_legacy_doc_text(self, path: str) -> Optional[str]:
        """Extract text from legacy .doc file via LibreOffice."""
        try:
            import subprocess
            import tempfile
            import os

            # Check if soffice is available
            if not os.path.exists(path):
                return None

            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
                tmp_path = tmp.name

            try:
                subprocess.run(
                    ['soffice', '--headless', '--convert-to', 'txt:Text', '--outdir', os.path.dirname(tmp_path), path],
                    check=True,
                    capture_output=True,
                    timeout=10
                )
                with open(tmp_path, 'r', encoding='utf-8') as f:
                    return f.read()
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        except Exception as e:
            print(f"[BookViewWidget] Legacy DOC extraction error: {e}")
            return None

    def _render_page(self) -> None:
        """Render the current page."""
        if not self._pages:
            if self._text_browser:
                self._text_browser.setText("<p>No pages loaded.</p>")
            return

        if not self._text_browser or not self._display_settings:
            return

        page = self._pages[self._current_page_index]

        # Render with ReadableTextRenderer
        try:
            doc = ReadableTextRenderer.render_text(
                page.text,
                self._display_settings,
                heading_level=1 if page.is_chapter_start else 0,
            )
            self._text_browser.setDocument(doc)
        except Exception as e:
            print(f"[BookViewWidget] Error rendering page {page.number}: {e}")
            self._text_browser.setText(f"<p>Error rendering page: {e}</p>")
            return

        # Update page indicator
        self._page_indicator.setText(f"{page.number} / {len(self._pages)}")
        self._page_input.setText(f"Page {page.number}")

        # Update button states
        self._prev_btn.setEnabled(self._current_page_index > 0)
        self._next_btn.setEnabled(self._current_page_index < len(self._pages) - 1)

        print(f"[BookViewWidget] Rendered page {page.number}/{len(self._pages)} ({len(page.text)} chars)")

    def next_page(self) -> None:
        """Go to next page."""
        if self._current_page_index < len(self._pages) - 1:
            self._current_page_index += 1
            self._render_page()

    def previous_page(self) -> None:
        """Go to previous page."""
        if self._current_page_index > 0:
            self._current_page_index -= 1
            self._render_page()

    def goto_page(self, page_number: int) -> None:
        """Jump to a specific page (1-indexed)."""
        idx = page_number - 1
        if 0 <= idx < len(self._pages):
            self._current_page_index = idx
            self._render_page()

    # ---- Compatibility stubs (SourceViewerHost expects these methods) ----

    def zoom_in(self) -> None:
        """Increase font size."""
        if self._display_settings:
            self._display_settings.font_size_base = min(
                32, self._display_settings.font_size_base + 1
            )
            self._render_page()

    def zoom_out(self) -> None:
        """Decrease font size."""
        if self._display_settings:
            self._display_settings.font_size_base = max(
                8, self._display_settings.font_size_base - 1
            )
            self._render_page()

    def zoom_reset(self) -> None:
        """Reset font size to default."""
        if self._display_settings:
            from core.models.display_settings import SourceViewerDisplaySettings

            self._display_settings.font_size_base = (
                SourceViewerDisplaySettings().font_size_base
            )
            self._render_page()

    def jump_to_source(self, doc_name: str, quote: str) -> bool:
        """
        Find quote in current document and jump to that page.
        Returns True if found, False otherwise.
        """
        needle = " ".join((quote or "").lower().split())
        if not needle:
            return False

        for idx, page in enumerate(self._pages):
            haystack = " ".join((page.text or "").lower().split())
            if needle in haystack or haystack in needle:
                self._current_page_index = idx
                self._render_page()
                return True

        return False

    @property
    def current_source_path(self) -> Optional[str]:
        """Current source file path."""
        return self._current_source_path

    def toggle_dark_mode(self) -> None:
        """Toggle dark mode (handled via display settings)."""
        if self._display_settings:
            self._display_settings.dark_mode_enabled = not self._display_settings.dark_mode_enabled
            self._display_settings.theme_mode_enabled = False
            self._render_page()

    def toggle_theme_mode(self) -> None:
        """Toggle theme mode (handled via display settings)."""
        if self._display_settings:
            self._display_settings.theme_mode_enabled = not self._display_settings.theme_mode_enabled
            self._display_settings.dark_mode_enabled = False
            self._render_page()

    def toggle_reading_mode(self, checked: bool = None) -> None:
        """Reading mode not yet implemented for book view; use inline mode in PDFViewer."""
        pass
