import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QPointF, QRectF
import fitz

# Note: In a headless environment, PyQt widget creation can fail. 
# We mock PyQt6 components heavily here to test pure logic.
import sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv) if not QApplication.instance() else QApplication.instance()

from gui.components.annotation_manager import AnnotationManager

class TestAnnotationManager(unittest.TestCase):
    def setUp(self):
        # Mock the PDF viewer widget
        self.mock_viewer = MagicMock()
        self.mock_viewer.base_zoom = 1.0
        self.mock_viewer.page_items = []
        self.mock_viewer.scene = MagicMock()
        
        # Mock a document and a page
        self.mock_doc = MagicMock()
        self.mock_page = MagicMock()
        self.mock_doc.load_page.return_value = self.mock_page
        self.mock_viewer.doc = self.mock_doc
        
        self.annot_manager = AnnotationManager(self.mock_viewer)

    def test_get_word_at_pos(self):
        """Test if the manager correctly identifies a clicked word based on coordinates."""
        # word format from fitz: (x0, y0, x1, y1, "word", block_no, line_no, word_no)
        self.annot_manager.page_words = [
            (10.0, 10.0, 50.0, 20.0, "Hello", 0, 0, 0),
            (60.0, 10.0, 100.0, 20.0, "World", 0, 0, 1)
        ]
        
        # Simulate click inside the first word bounding box
        mock_pos = QPointF(30.0, 15.0) 
        idx = self.annot_manager._get_word_at_pos(mock_pos, zoom=1.0)
        self.assertEqual(idx, 0)
        
        # Simulate click inside the second word bounding box
        mock_pos2 = QPointF(80.0, 15.0)
        idx2 = self.annot_manager._get_word_at_pos(mock_pos2, zoom=1.0)
        self.assertEqual(idx2, 1)

    def test_clear_selection(self):
        """Test clearing temporary highlight objects."""
        mock_highlight = MagicMock()
        self.annot_manager.temp_highlights.append(mock_highlight)
        self.annot_manager.selected_words = [("Test",)]
        self.annot_manager.start_word_idx = 1
        
        self.annot_manager.clear_selection()
        
        self.assertEqual(len(self.annot_manager.temp_highlights), 0)
        self.assertEqual(len(self.annot_manager.selected_words), 0)
        self.assertIsNone(self.annot_manager.start_word_idx)
        self.mock_viewer.scene.removeItem.assert_called_once_with(mock_highlight)

    @patch('gui.components.annotation_manager.RewordDialog')
    def test_reword_selection(self, mock_reword_dialog):
        """Test that rewording correctly instantiates the dialog without RAG routing."""
        self.annot_manager.selected_words = [
            (0, 0, 0, 0, "This", 0, 0, 0),
            (0, 0, 0, 0, "is", 0, 0, 1),
            (0, 0, 0, 0, "test", 0, 0, 2)
        ]
        
        # Mock window and tabs
        mock_main_window = MagicMock()
        mock_llm_dock = MagicMock()
        mock_llm_dock.model_combo.currentText.return_value = "llama3"
        mock_main_window.tabs = {"LLM Chat": mock_llm_dock}
        self.mock_viewer.window.return_value = mock_main_window

        self.annot_manager.reword_selection()
        
        # Ensure RewordDialog was created with correct extracted text
        mock_reword_dialog.assert_called_once()
        args, kwargs = mock_reword_dialog.call_args
        self.assertEqual(args[0], "This is test") # The extracted string
        
        # Ensure it clears selection afterwards
        self.assertEqual(len(self.annot_manager.selected_words), 0)

if __name__ == '__main__':
    unittest.main()