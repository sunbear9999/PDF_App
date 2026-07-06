from unittest.mock import patch

from core.services.extraction.formats.image_ocr_parser import ImageOcrParser


def _fake_ocr_data():
    # Mirrors pytesseract.image_to_data(output_type=Output.DICT) shape:
    # two words on line (0,0,0), one word on a second line (0,0,1).
    return {
        "text": ["Hello", "world", "Second", ""],
        "conf": ["95", "92", "88", "-1"],
        "block_num": [0, 0, 0, 0],
        "par_num": [0, 0, 0, 0],
        "line_num": [0, 0, 1, 1],
        "left": [10, 60, 10, 0],
        "top": [10, 10, 30, 0],
        "width": [40, 45, 50, 0],
        "height": [15, 15, 15, 0],
    }


def test_is_available_checks_pytesseract_and_tesseract_binary():
    with patch("shutil.which", return_value=None):
        assert ImageOcrParser.is_available() is False
    with patch("shutil.which", return_value="/usr/bin/tesseract"):
        assert ImageOcrParser.is_available() is True


def test_parses_word_level_ocr_into_line_grouped_blocks_with_bbox():
    with patch("core.ocr_engine.run_ocr_on_image", return_value=_fake_ocr_data()):
        blocks = ImageOcrParser().parse("/tmp/fake.png", "doc_1")

    assert len(blocks) == 2
    assert blocks[0].exact_text == "Hello world"
    assert blocks[0].bbox == [10.0, 10.0, 105.0, 25.0]
    assert blocks[0].page_num is None
    assert blocks[0].block_type == "paragraph"
    assert blocks[1].exact_text == "Second"


def test_ocr_error_returns_empty_list():
    with patch("core.ocr_engine.run_ocr_on_image", return_value={"error": "tesseract not found"}):
        assert ImageOcrParser().parse("/tmp/fake.png", "doc_1") == []


def test_missing_pytesseract_returns_empty_list():
    with patch.dict("sys.modules", {"core.ocr_engine": None}):
        assert ImageOcrParser().parse("/tmp/fake.png", "doc_1") == []
