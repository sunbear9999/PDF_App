import pytest

pytest.importorskip("ebooklib")
pytest.importorskip("bs4")

from ebooklib import epub

from core.services.extraction.formats.epub_parser import EpubParser


def _make_epub(tmp_path):
    book = epub.EpubBook()
    book.set_identifier("id123")
    book.set_title("Test Book")
    book.set_language("en")

    chapter = epub.EpubHtml(title="Chapter 1", file_name="chap1.xhtml", lang="en")
    chapter.content = "<h1>Chapter 1</h1><p>Once upon a time.</p><p>The end.</p>"
    book.add_item(chapter)
    book.toc = (epub.Link("chap1.xhtml", "Chapter 1", "chap1"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]

    path = str(tmp_path / "sample.epub")
    epub.write_epub(path, book)
    return path


def test_is_available():
    assert EpubParser.is_available() is True


def test_parses_headings_and_paragraphs_in_spine_order(tmp_path):
    path = _make_epub(tmp_path)

    blocks = EpubParser().parse(path, "doc_1")

    assert blocks[0].block_type == "heading"
    assert blocks[0].exact_text == "Chapter 1"
    assert blocks[0].heading_level == 1
    assert [b.exact_text for b in blocks[1:]] == ["Once upon a time.", "The end."]
    assert all(b.page_num is None for b in blocks)


def test_corrupt_file_returns_empty_list(tmp_path):
    path = tmp_path / "not_a_book.epub"
    path.write_text("not a real epub file")
    assert EpubParser().parse(str(path), "doc_1") == []
