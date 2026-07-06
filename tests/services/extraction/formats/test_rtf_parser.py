import pytest

pytest.importorskip("striprtf")

from core.services.extraction.formats.rtf_parser import RtfParser


def test_is_available():
    assert RtfParser.is_available() is True


def test_parses_paragraphs_with_no_heading_level(tmp_path):
    path = tmp_path / "sample.rtf"
    path.write_text(
        r"{\rtf1\ansi First paragraph.\par Second paragraph.\par}",
        encoding="utf-8",
    )

    blocks = RtfParser().parse(str(path), "doc_1")

    assert [b.exact_text for b in blocks] == ["First paragraph.", "Second paragraph."]
    assert all(b.block_type == "paragraph" for b in blocks)
    assert all(b.heading_level is None for b in blocks)
    assert all(b.page_num is None for b in blocks)


def test_missing_file_returns_empty_list(tmp_path):
    assert RtfParser().parse(str(tmp_path / "missing.rtf"), "doc_1") == []
