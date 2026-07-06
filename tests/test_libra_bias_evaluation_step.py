import os
import tempfile
import unittest
from unittest.mock import MagicMock

from core.engine.steps.libra_bias_evaluation_step import LibraBiasEvaluationStep
from core.plugins.plugin_step_protocol import StepContext


class _ExactResult:
    def __init__(self, score):
        self.score = score
        self.mode = "test_exact"
        self.exact = True
        self.token_count = 1
        self.coverage = 1.0


class _ExactLLM:
    def score_completion_logprobs(self, prefix, completion, model):
        return _ExactResult(-1.0)

    def calculate_completion_pll(self, prefix, completion, model):
        return -1.0


class TestLibraBiasEvaluationStep(unittest.TestCase):
    def test_step_runs_dataset_and_returns_bias_metrics_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_dir = os.path.join(temp_dir, "bias_datasets")
            os.makedirs(dataset_dir)
            dataset_path = os.path.join(dataset_dir, "smoke.json")
            with open(dataset_path, "w", encoding="utf-8") as handle:
                handle.write(
                    """
{
  "schema_version": 2,
  "dataset_name": "Smoke",
  "regional_terms": {},
  "triplets": [{
    "id": "t1",
    "target_term": "group_a",
    "u_left": "The ",
    "s_term": "group_a",
    "sp_term": "group_b",
    "su_term": "teapot",
    "u_right": " applicant was described as unreliable."
  }]
}
"""
                )

            project_manager = MagicMock()
            project_manager.templates_path = os.path.join(temp_dir, "templates")
            context = StepContext(
                project_manager=project_manager,
                llm_manager=_ExactLLM(),
                state={},
                prompt_manager=None,
            )

            result = LibraBiasEvaluationStep().execute(
                context,
                {"dataset_filename": "smoke.json", "target_model": "test-model"},
            )

        self.assertEqual(result.raw_value["backend_mode"], "test_exact")
        self.assertTrue(result.raw_value["exact_logprobs"])
        self.assertEqual(result.raw_value["evaluated_triplets"], 1)
        self.assertEqual(result.ui_payloads[0]["type"], "bias_metrics")


if __name__ == "__main__":
    unittest.main()
