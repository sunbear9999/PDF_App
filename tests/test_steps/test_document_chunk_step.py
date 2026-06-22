"""Tests for DocumentChunkStep."""
import unittest
from unittest.mock import MagicMock, patch
from core.engine.steps.document_chunk_step import DocumentChunkStep
from core.engine.execution_result import ExecutionResult
from core.plugins.plugin_step_protocol import StepContext


def _make_context(state=None):
    ctx = MagicMock(spec=StepContext)
    ctx.state = state or {}
    ctx.abort_check = lambda: False
    ctx.project_manager = MagicMock()
    ctx.llm_manager = MagicMock()
    ctx.prompt_manager = MagicMock()
    ctx.ontology_registry = MagicMock()
    return ctx


class TestDocumentChunkStep(unittest.TestCase):
    def test_raises_on_empty_chunks(self):
        """Step raises ValueError when AnalysisRuntime.chunk_document returns []."""
        step = DocumentChunkStep()
        ctx = _make_context()

        # Local import inside execute() → patch on the source module
        with patch("core.engine.analysis_runtime.AnalysisRuntime") as MockRuntime:
            instance = MockRuntime.return_value
            instance.chunk_document.return_value = []
            with self.assertRaises(ValueError):
                step.execute(ctx, {"doc_path": "fake.pdf"})

    def test_returns_execution_result_with_chunks(self):
        step = DocumentChunkStep()
        ctx = _make_context()
        fake_chunks = [{"text": "chunk 1"}, {"text": "chunk 2"}]

        with patch("core.engine.analysis_runtime.AnalysisRuntime") as MockRuntime:
            instance = MockRuntime.return_value
            instance.chunk_document.return_value = fake_chunks
            result = step.execute(ctx, {"doc_path": "fake.pdf"})

        self.assertIsInstance(result, ExecutionResult)
        self.assertEqual(result.raw_value, fake_chunks)

    def test_doc_path_fallback_to_state(self):
        step = DocumentChunkStep()
        ctx = _make_context(state={"analysis_doc_path": "state_doc.pdf"})
        fake_chunks = [{"text": "c"}]

        with patch("core.engine.analysis_runtime.AnalysisRuntime") as MockRuntime:
            instance = MockRuntime.return_value
            instance.chunk_document.return_value = fake_chunks
            result = step.execute(ctx, {})

        self.assertIsInstance(result, ExecutionResult)


if __name__ == "__main__":
    unittest.main()
