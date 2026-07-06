import pytest

pytest.importorskip("bs4")

from core.services.extraction.formats.html_parser import HtmlParser


def test_is_available():
    assert HtmlParser.is_available() is True


def test_parses_headings_and_paragraphs(tmp_path):
    path = tmp_path / "sample.html"
    path.write_text(
        "<html><body><h1>Title</h1><p>Para one.</p><p>Para two.</p></body></html>",
        encoding="utf-8",
    )

    blocks = HtmlParser().parse(str(path), "doc_1")

    assert blocks[0].block_type == "heading"
    assert blocks[0].exact_text == "Title"
    assert [b.exact_text for b in blocks[1:]] == ["Para one.", "Para two."]
    assert all(b.page_num is None for b in blocks)


def test_missing_file_returns_empty_list(tmp_path):
    assert HtmlParser().parse(str(tmp_path / "missing.html"), "doc_1") == []
