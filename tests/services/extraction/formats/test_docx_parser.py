import pytest

docx = pytest.importorskip("docx")

from core.services.extraction.formats.docx_parser import DocxParser


def _make_docx(tmp_path):
    document = docx.Document()
    document.add_heading("Doc Title", level=1)
    document.add_paragraph("A regular paragraph.")
    document.add_heading("Sub Section", level=2)
    document.add_paragraph("Another paragraph.")
    path = str(tmp_path / "sample.docx")
    document.save(path)
    return path


def test_is_available():
    assert DocxParser.is_available() is True


def test_parses_headings_and_paragraphs(tmp_path):
    path = _make_docx(tmp_path)

    blocks = DocxParser().parse(path, "doc_1")

    assert [b.block_type for b in blocks] == ["heading", "paragraph", "heading", "paragraph"]
    assert blocks[0].exact_text == "Doc Title"
    assert blocks[0].heading_level == 1
    assert blocks[2].heading_level == 2
    assert all(b.page_num is None for b in blocks)


def test_parses_tables_as_table_blocks(tmp_path):
    document = docx.Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    table.cell(1, 0).text = "C"
    table.cell(1, 1).text = "D"
    path = str(tmp_path / "table.docx")
    document.save(path)

    blocks = DocxParser().parse(path, "doc_1")

    assert any(b.block_type == "table" for b in blocks)
    table_block = next(b for b in blocks if b.block_type == "table")
    assert "A | B" in table_block.exact_text


def test_corrupt_file_returns_empty_list(tmp_path):
    path = tmp_path / "not_a_docx.docx"
    path.write_text("not a real docx file")
    assert DocxParser().parse(str(path), "doc_1") == []
