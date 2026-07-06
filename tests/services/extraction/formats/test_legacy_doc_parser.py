from unittest.mock import patch

from core.services.extraction.formats.legacy_doc_parser import LegacyDocParser


def test_is_available_false_and_reason_populated_without_libreoffice():
    with patch("shutil.which", return_value=None):
        assert LegacyDocParser.is_available() is False
    assert LegacyDocParser.unavailable_reason
    assert "LibreOffice" in LegacyDocParser.unavailable_reason


def test_is_available_true_when_soffice_on_path():
    with patch("shutil.which", side_effect=lambda name: "/usr/bin/soffice" if name == "soffice" else None):
        assert LegacyDocParser.is_available() is True


def test_parse_returns_empty_list_without_libreoffice(tmp_path):
    path = tmp_path / "sample.doc"
    path.write_bytes(b"not a real doc file")
    with patch("shutil.which", return_value=None):
        assert LegacyDocParser().parse(str(path), "doc_1") == []
