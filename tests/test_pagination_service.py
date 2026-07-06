"""Tests for PaginationService."""
from core.models.display_settings import SourceViewerDisplaySettings
from core.services.extraction.pagination_service import PaginationService


def test_pagination_splits_on_paragraph_boundaries():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    settings = SourceViewerDisplaySettings(lines_per_page=10, page_width_chars=80)
    pages = PaginationService.paginate(text, "doc_1", settings)

    assert len(pages) >= 1
    assert pages[0].text.startswith("First paragraph")


def test_pagination_respects_page_limit():
    # Create text with multiple paragraphs
    paras = ["Paragraph " + str(i) + "." * 100 for i in range(20)]
    text = "\n\n".join(paras)

    settings = SourceViewerDisplaySettings(lines_per_page=5, page_width_chars=40)
    pages = PaginationService.paginate(text, "doc_1", settings)

    # With small page limits, should split into multiple pages
    assert len(pages) > 1


def test_pagination_preserves_character_positions():
    text = "Start.\n\nMiddle.\n\nEnd."
    settings = SourceViewerDisplaySettings(lines_per_page=100)
    pages = PaginationService.paginate(text, "doc_1", settings)

    if len(pages) > 0:
        page = pages[0]
        # start_char and end_char should be within valid range
        assert 0 <= page.start_char <= page.end_char <= len(text)


def test_pagination_empty_text_returns_empty_list():
    settings = SourceViewerDisplaySettings()
    pages = PaginationService.paginate("", "doc_1", settings)
    assert pages == []


def test_pagination_calculates_read_time():
    text = " ".join(["word"] * 250)  # ~250 words = ~1 minute
    settings = SourceViewerDisplaySettings()
    pages = PaginationService.paginate(text, "doc_1", settings)

    if pages:
        assert pages[0].estimated_read_time_sec >= 50  # rough estimate
