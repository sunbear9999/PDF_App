import os
import sys
import tempfile
import unittest


class TestDictionariesGuard(unittest.TestCase):
    """Verify main._configure_qtwebengine_dictionaries only sets the
    QTWEBENGINE_DICTIONARIES_PATH env var when the dictionary folder
    actually exists. Without this guard, Qt logs a warning and the
    spellcheck silently fails on Macs where the folder isn't bundled."""

    def setUp(self):
        self._saved = os.environ.pop("QTWEBENGINE_DICTIONARIES_PATH", None)

    def tearDown(self):
        os.environ.pop("QTWEBENGINE_DICTIONARIES_PATH", None)
        if self._saved is not None:
            os.environ["QTWEBENGINE_DICTIONARIES_PATH"] = self._saved

    def test_unset_when_folder_missing(self):
        from main import _configure_qtwebengine_dictionaries
        with tempfile.TemporaryDirectory() as tmp:
            _configure_qtwebengine_dictionaries(tmp)
            self.assertNotIn("QTWEBENGINE_DICTIONARIES_PATH", os.environ)

    def test_set_when_folder_exists(self):
        from main import _configure_qtwebengine_dictionaries
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "qtwebengine_dictionaries"))
            _configure_qtwebengine_dictionaries(tmp)
            self.assertEqual(
                os.environ["QTWEBENGINE_DICTIONARIES_PATH"],
                os.path.join(tmp, "qtwebengine_dictionaries"),
            )


if __name__ == "__main__":
    unittest.main()
