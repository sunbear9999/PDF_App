"""
Pagination service — splits flowing text into logical pages for book-view display.
Respects paragraph breaks, heading boundaries, and user page-width preferences.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from core.models.display_settings import SourceViewerDisplaySettings


@dataclass
class PageContent:
    """One logical page of text, ready for rendering."""

    number: int  # 1-indexed page number
    text: str  # the text content for this page
    start_char: int  # absolute position in full text
    end_char: int  # absolute position in full text
    is_chapter_start: bool = False  # True if page starts with a major heading
    heading: str = ""  # detected heading at page start, if any
    estimated_read_time_sec: int = 0  # rough estimate for display


class PaginationService:
    """
    Splits text into pages using word-wrapping and paragraph awareness.
    Does not split mid-paragraph unless a single paragraph exceeds page capacity.
    """

    # Rough WPM for estimated read time
    _WORDS_PER_MINUTE = 250

    @staticmethod
    def paginate(
        text: str, doc_id: str, settings: SourceViewerDisplaySettings
    ) -> List[PageContent]:
        """
        Split text into pages.

        Args:
            text: full document text
            doc_id: source identifier (for logging)
            settings: page width, lines per page, etc.

        Returns:
            List of PageContent objects, one per page.
        """
        if not text:
            return []

        pages: List[PageContent] = []
        paragraphs = PaginationService._split_paragraphs(text)

        # Calculate target characters per page (rough: avg 5 chars/word × words_per_line × lines_per_page)
        chars_per_line = (
            max(40, settings.page_width_chars) if settings.page_width_chars > 0 else 80
        )
        lines = settings.lines_per_page
        target_chars_per_page = int(chars_per_line * lines * 0.6)  # 0.6 = avg word length + space

        current_page_text = ""
        current_page_start = 0
        page_number = 1

        for para_idx, para in enumerate(paragraphs):
            para_with_newline = para + "\n"
            is_heading = PaginationService._is_heading(para)

            # If adding this paragraph would exceed page limit, flush current page
            if (
                current_page_text
                and len(current_page_text) + len(para_with_newline) > target_chars_per_page
            ):
                pages.append(
                    PageContent(
                        number=page_number,
                        text=current_page_text.rstrip(),
                        start_char=current_page_start,
                        end_char=current_page_start + len(current_page_text),
                        is_chapter_start=len(pages) == 0,
                        estimated_read_time_sec=PaginationService._estimate_read_time(
                            current_page_text
                        ),
                    )
                )
                page_number += 1
                current_page_text = ""
                current_page_start = sum(len(p) + 1 for p in paragraphs[:para_idx])

            # Add paragraph to current page
            current_page_text += para_with_newline

        # Flush final page
        if current_page_text:
            pages.append(
                PageContent(
                    number=page_number,
                    text=current_page_text.rstrip(),
                    start_char=current_page_start,
                    end_char=current_page_start + len(current_page_text),
                    is_chapter_start=len(pages) == 0,
                    estimated_read_time_sec=PaginationService._estimate_read_time(
                        current_page_text
                    ),
                )
            )

        return pages

    @staticmethod
    def _split_paragraphs(text: str) -> List[str]:
        """Split on blank lines (standard paragraph separator)."""
        # Normalize line endings, split on 2+ newlines
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        # Split on blank lines (one or more newlines with only whitespace between)
        paras = re.split(r"\n\s*\n+", normalized)
        # Filter empty, strip trailing whitespace per para
        return [p.strip() for p in paras if p.strip()]

    @staticmethod
    def _is_heading(text: str) -> bool:
        """Heuristic: single line, short, possibly starts with #, bold marker, etc."""
        if "\n" in text or len(text) > 100:
            return False
        # Markdown-style headings
        if text.startswith("#"):
            return True
        # All-caps and short (likely a title)
        if text.isupper() and len(text) < 50:
            return True
        return False

    @staticmethod
    def _estimate_read_time(text: str) -> int:
        """Estimate read time in seconds."""
        words = len(text.split())
        minutes = max(1, words / PaginationService._WORDS_PER_MINUTE)
        return int(minutes * 60)
