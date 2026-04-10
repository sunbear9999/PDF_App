import unittest
from unittest.mock import MagicMock, patch

from core.pdf_utils import extract_filtered_blocks
from core.tts_engine import generate_audio
from core.text_utils import sanitize_extracted_text


class TestTTSTextHandling(unittest.TestCase):
    def test_sanitize_extracted_text_removes_annotation_artifacts_and_surrogates(self):
        raw_text = "Hello\ufffc highlighted\ufffd text\udc9d with bi\u200bdi base-\n ball"

        clean_text = sanitize_extracted_text(raw_text, collapse_whitespace=True)

        self.assertEqual(clean_text, "Hello highlighted text with bidi baseball")

    @patch("core.tts_engine.subprocess.run")
    @patch("core.tts_engine.os.path.exists", return_value=True)
    @patch("core.tts_engine.shutil.which", return_value="piper")
    def test_generate_audio_sends_clean_utf8_bytes_to_piper(self, mock_which, mock_exists, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")

        result = generate_audio("Text\udc9d with\ufffc artifact", "audio/out.wav")

        self.assertTrue(result)
        args, kwargs = mock_run.call_args
        self.assertIn("piper", args[0][0])
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["input"], b"Text with artifact\n")
        self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(kwargs["env"]["PYTHONUTF8"], "1")

    @patch("core.pdf_utils.fitz.open")
    def test_extract_filtered_blocks_sanitizes_highlight_anchor_text(self, mock_open):
        mock_page = MagicMock()
        mock_page.rect.height = 1000

        def _get_text(mode, sort=False):
            if mode == "words":
                return [
                    (0, 150, 50, 180, "Keep\ufffc", 0, 0, 0),
                    (55, 150, 120, 180, "this\udc9d", 0, 0, 1),
                    (125, 150, 170, 180, "text", 0, 0, 2),
                    (0, 10, 100, 20, "Header", 1, 0, 0),
                    (0, 930, 100, 980, "Footer", 2, 0, 0),
                ]
            return []

        mock_page.get_text.side_effect = _get_text

        mock_doc = MagicMock()
        mock_doc.__enter__.return_value = mock_doc
        mock_doc.__exit__.return_value = False
        mock_doc.__len__.return_value = 1
        mock_doc.load_page.return_value = mock_page
        mock_open.return_value = mock_doc

        text = extract_filtered_blocks("dummy.pdf", ignore_margins=True, start_page=1, end_page=1)

        self.assertEqual(text, "Keep this text")


if __name__ == "__main__":
    unittest.main()