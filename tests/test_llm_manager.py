import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
import json
import fitz  # PyMuPDF
from core.llm_manager import LocalLLMManager

class TestLocalLLMManager(unittest.TestCase):
    @patch('core.llm_manager.subprocess.Popen')
    @patch('core.llm_manager.requests.get')
    def setUp(self, mock_get, mock_popen):
        # Prevent the LLM Manager from actually trying to start Ollama during tests
        mock_get.return_value.status_code = 200
        self.llm_manager = LocalLLMManager()

    @patch('core.llm_manager.requests.get')
    def test_get_available_models(self, mock_get):
        """Test that the manager correctly fetches and filters models."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Should filter out the embedding model ('nomic-embed-text')
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3"},
                {"name": "qwen2.5:7b"},
                {"name": "nomic-embed-text"}
            ]
        }
        mock_get.return_value = mock_response

        models = self.llm_manager.get_available_models()
        self.assertIn("llama3", models)
        self.assertIn("qwen2.5:7b", models)
        self.assertNotIn("nomic-embed-text", models)

    @patch('core.llm_manager.requests.post')
    def test_get_embedding(self, mock_post):
        """Test vector embedding generation request."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
        mock_post.return_value = mock_response

        embedding = self.llm_manager.get_embedding("Test text")
        self.assertEqual(embedding, [0.1, 0.2, 0.3])
        mock_post.assert_called_once()
        
        # Verify the payload structure
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs['json']['prompt'], "Test text")
        self.assertEqual(kwargs['json']['model'], "nomic-embed-text")

    def test_set_project_database(self):
        """Test that ChromaDB initializes properly in a temporary directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = os.path.join(temp_dir, "test_project")
            self.llm_manager.set_project_database(project_path)
            
            self.assertIsNotNone(self.llm_manager.chroma_client)
            self.assertIsNotNone(self.llm_manager.collection)
            self.assertEqual(self.llm_manager.collection.name, "pdf_workspace")

    @patch.object(LocalLLMManager, 'get_embedding')
    def test_index_documents(self, mock_get_embedding):
        """Test the extraction and chunking of PDF documents."""
        # Mock embedding to return a dummy vector
        mock_get_embedding.return_value = [0.1] * 768 
        
        # Create a real but temporary PDF with fitz
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = os.path.join(temp_dir, "test_doc.pdf")
            doc = fitz.open()
            page = doc.new_page()
            
            # Add enough text to create a few chunks
            dummy_text = "Word " * 300 
            page.insert_text((50, 50), dummy_text)
            doc.save(pdf_path)
            doc.close()

            # Initialize DB
            self.llm_manager.set_project_database(os.path.join(temp_dir, "db"))
            
            # Perform indexing
            self.llm_manager.index_documents([pdf_path])
            
            # Check if collection was populated
            results = self.llm_manager.collection.get()
            self.assertTrue(len(results["ids"]) > 0)
            self.assertEqual(results["metadatas"][0]["doc_name"], "test_doc.pdf")

    @patch('core.llm_manager.requests.post')
    def test_query_no_rag_mode(self, mock_post):
        """Test standard querying without the database (RAG disabled)."""
        # Create a mock streaming response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_lines.return_value = [
            json.dumps({"response": "Hello "}).encode('utf-8'),
            json.dumps({"response": "World!"}).encode('utf-8')
        ]
        # Make the context manager return our mock
        mock_post.return_value.__enter__.return_value = mock_response

        # Test callback accumulation
        chunks = []
        def callback(chunk):
            chunks.append(chunk)

        result = self.llm_manager.query(
            question="Hi", 
            selected_model="llama3", 
            allowed_docs=[], 
            callback=callback, 
            rag_enabled=False, 
            use_agents=False
        )
        
        self.assertEqual("".join(chunks), "Hello World!")
        self.assertEqual(result, "Hello World!")

if __name__ == '__main__':
    unittest.main()