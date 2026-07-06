import textwrap

from core.services.extraction.formats.plaintext_parser import PlainTextParser


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_is_available_is_always_true():
    assert PlainTextParser.is_available() is True


def test_parses_blank_line_separated_paragraphs_and_atx_headings(tmp_path):
    content = textwrap.dedent("""\
        # Title Heading

        First paragraph.

        ## Subheading

        Second paragraph.
    """)
    path = _write(tmp_path, "sample.txt", content)

    blocks = PlainTextParser().parse(path, "doc_1")

    assert [b.block_type for b in blocks] == ["heading", "paragraph", "heading", "paragraph"]
    assert blocks[0].exact_text == "Title Heading"
    assert blocks[0].heading_level == 1
    assert blocks[2].heading_level == 2
    assert all(b.page_num is None for b in blocks)


def test_falls_back_to_line_split_when_no_blank_lines(tmp_path):
    # Simulates LibreOffice/striprtf-style output: one newline per line, no
    # blank-line paragraph breaks at all.
    content = "First line.\nSecond line.\nThird line.\n"
    path = _write(tmp_path, "sample.txt", content)

    blocks = PlainTextParser().parse(path, "doc_1")

    assert [b.exact_text for b in blocks] == ["First line.", "Second line.", "Third line."]


def test_strips_utf8_bom(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_bytes("﻿Hello world.".encode("utf-8"))

    blocks = PlainTextParser().parse(str(path), "doc_1")

    assert blocks[0].exact_text == "Hello world."


def test_missing_file_returns_empty_list(tmp_path):
    blocks = PlainTextParser().parse(str(tmp_path / "does_not_exist.txt"), "doc_1")
    assert blocks == []
