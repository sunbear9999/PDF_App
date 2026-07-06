import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
import json
import fitz  # PyMuPDF
from core.backends.llamacpp_backend import LlamaCppProvider
from core.llm_manager import LocalLLMManager, OllamaProvider


class _VisionProvider:
    ai_enabled = True

    def __init__(self, supports=True):
        self.supports = supports
        self.last = None

    def available_models(self): return ["vision-model"]
    def supports_model_capability(self, model, capability): return self.supports and capability == "vision"
    def generate(self, prompt, system, model, **kwargs):
        self.last = {"prompt": prompt, "system": system, "model": model, **kwargs}
        return "ok"
    def embed(self, text): return []
    def embed_batch(self, texts): return [[] for _ in texts]
    def preload_model(self, model_name): pass
    def unload_all_models(self): pass
    def check_and_pull_embedding_model(self, progress_callback=None): pass

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

    @patch.object(LocalLLMManager, 'get_batch_embeddings')
    @patch.object(LocalLLMManager, 'get_embedding')
    def test_index_documents(self, mock_get_embedding, mock_get_batch_embeddings):
        """Test the extraction and chunking of PDF documents."""
        # Mock embedding to return a dummy vector
        mock_get_embedding.return_value = [0.1] * 768 
        mock_get_batch_embeddings.side_effect = lambda texts: [[0.1] * 768 for _ in texts]
        
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

    def test_query_forwards_normalized_images_to_vision_provider(self):
        provider = _VisionProvider()
        manager = LocalLLMManager(provider=provider)

        result = manager.query(
            "read chart", "vision-model", images=[{"data": b"png", "mime_type": "image/png"}],
            required_capabilities=["vision"],
        )

        self.assertEqual(result, "ok")
        self.assertEqual(provider.last["images"][0]["data"], "cG5n")
        self.assertEqual(provider.last["images"][0]["mime_type"], "image/png")

    def test_query_rejects_non_vision_provider(self):
        manager = LocalLLMManager(provider=_VisionProvider(supports=False))

        result = manager.query("read chart", "text-model", images=[{"data": b"png"}], required_capabilities=["vision"])

        self.assertIn("does not support required capability 'vision'", result)

    @patch.dict(os.environ, {"PAPYRUS_OLLAMA_KEEP_ALIVE": "0"}, clear=False)
    @patch('core.llm_manager.requests.post')
    def test_ollama_generation_unloads_previous_model_and_uses_safe_keep_alive(self, mock_post):
        provider = OllamaProvider.__new__(OllamaProvider)
        provider.api_base = "http://localhost:11434/api"
        provider._active_generation_model = "old-model"

        unload_response = MagicMock()
        stream_response = MagicMock()
        stream_response.status_code = 200
        stream_response.iter_lines.return_value = [
            json.dumps({"response": "ok"}).encode("utf-8"),
        ]
        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = stream_response
        mock_post.side_effect = [unload_response, stream_cm]

        result = provider.generate("hello", "", "new-model")

        self.assertEqual(result, "ok")
        self.assertEqual(mock_post.call_args_list[0].kwargs["json"], {
            "model": "old-model",
            "keep_alive": 0,
        })
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["model"], "new-model")
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["keep_alive"], "0")
        self.assertEqual(provider._active_generation_model, "")

    @patch.dict(os.environ, {}, clear=True)
    @patch('core.llm_manager.requests.post')
    def test_ollama_generation_keeps_new_model_loaded_by_default(self, mock_post):
        provider = OllamaProvider.__new__(OllamaProvider)
        provider.api_base = "http://localhost:11434/api"
        provider._active_generation_model = ""

        stream_response = MagicMock()
        stream_response.status_code = 200
        stream_response.iter_lines.return_value = [
            json.dumps({"response": "ok"}).encode("utf-8"),
        ]
        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = stream_response
        mock_post.return_value = stream_cm

        result = provider.generate("hello", "", "new-model")

        self.assertEqual(result, "ok")
        self.assertEqual(mock_post.call_args.kwargs["json"]["keep_alive"], "60m")
        self.assertEqual(provider._active_generation_model, "new-model")

    @patch('core.llm_manager.requests.post')
    def test_calculate_completion_pll_uses_ollama_native_prompt_logprobs(self, mock_post):
        provider = OllamaProvider.__new__(OllamaProvider)
        provider.api_base = "http://localhost:11434/api"

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "prompt_logprobs": [
                {"token": "The ", "logprob": None},
                {"token": "cat", "logprob": -0.2},
                {"token": " sat", "logprob": -0.3},
            ]
        }
        mock_post.return_value = response

        score = provider.calculate_completion_pll("The ", "cat sat", "llama3")

        self.assertAlmostEqual(score, -0.25)
        self.assertEqual(mock_post.call_count, 1)
        payloads = [call.kwargs["json"] for call in mock_post.call_args_list]
        self.assertEqual(payloads[0]["prompt"], "The cat sat")
        self.assertEqual(payloads[0]["options"]["num_predict"], 0)
        self.assertTrue(payloads[0]["logprobs"])

    @patch('core.llm_manager.requests.post')
    def test_calculate_completion_pll_uses_ollama_native_generated_logprobs(self, mock_post):
        provider = OllamaProvider.__new__(OllamaProvider)
        provider.api_base = "http://localhost:11434/api"

        no_prompt = MagicMock()
        no_prompt.status_code = 200
        no_prompt.json.return_value = {"response": ""}
        generated = MagicMock()
        generated.status_code = 200
        generated.json.return_value = {
            "response": "cat sat",
            "logprobs": [
                {"token": "cat", "logprob": -0.2},
                {"token": " sat", "logprob": -0.3},
            ],
        }
        mock_post.side_effect = [no_prompt, generated]

        result = provider.score_completion_logprobs("The ", "cat sat", "llama3")

        self.assertEqual(result.mode, "ollama_native_generate")
        self.assertAlmostEqual(result.score, -0.25)

    @patch('core.llm_manager.requests.post')
    def test_calculate_completion_pll_fails_when_native_and_openai_do_not_score(self, mock_post):
        provider = OllamaProvider.__new__(OllamaProvider)
        provider.api_base = "http://localhost:11434/api"

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"response": "cat"}
        mock_post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "LOGPROBS_UNSUPPORTED"):
            provider.calculate_completion_pll("The ", "zebra", "llama3")

    @patch('core.llm_manager.requests.post')
    def test_calculate_completion_pll_does_not_double_v1_suffix(self, mock_post):
        provider = OllamaProvider.__new__(OllamaProvider)
        provider.api_base = "http://localhost:11434/v1"

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{
                "logprobs": {
                    "tokens": ["The ", "cat"],
                    "token_logprobs": [None, -0.2],
                }
            }]
        }
        bad_native = MagicMock()
        bad_native.status_code = 200
        bad_native.json.return_value = {"response": ""}
        mock_post.side_effect = [bad_native, bad_native, response]

        provider.calculate_completion_pll("The ", "cat", "llama3")

        self.assertEqual(
            mock_post.call_args.args[0],
            "http://localhost:11434/v1/completions",
        )

    @patch('core.backends.llamacpp_backend.requests.post')
    def test_llamacpp_native_completion_probabilities_parse_logprobs(self, mock_post):
        provider = LlamaCppProvider.__new__(LlamaCppProvider)
        provider._base_url = "http://localhost:8080"

        response = MagicMock()
        response.json.return_value = {
            "completion_probabilities": [{
                "probs": [
                    {"tok_str": "cat", "prob": 0.5},
                    {"tok_str": "dog", "prob": 0.25},
                ]
            }]
        }
        mock_post.return_value = response

        distribution = provider._get_native_next_token_logprobs("The ", top_k=100)

        self.assertAlmostEqual(distribution["cat"], -0.6931471805599453)
        self.assertEqual(
            mock_post.call_args.args[0],
            "http://localhost:8080/completion",
        )

if __name__ == '__main__':
    unittest.main()
