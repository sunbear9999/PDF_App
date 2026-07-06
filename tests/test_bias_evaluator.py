import os
import tempfile
import unittest
from unittest.mock import MagicMock

from core.engine.bias_evaluator import BiasEvaluator, _BBS_NEUTRAL_COMPLETION


class _PartialFailureLLM:
    def calculate_completion_pll(self, prefix, completion, model):
        if completion.startswith("bad"):
            raise TimeoutError("simulated timeout")
        return -0.1 * len(completion)


class _TemplateSmokeLLM:
    def calculate_completion_pll(self, prefix, completion, model):
        if prefix.startswith("The term "):
            if completion == _BBS_NEUTRAL_COMPLETION:
                return -10.0
            return -0.01
        return -0.001 * len(completion)


class TestBiasEvaluator(unittest.TestCase):
    def _evaluator(self, llm=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        project_manager = MagicMock()
        project_manager.templates_path = os.path.join(temp_dir.name, "templates")
        return BiasEvaluator(llm or MagicMock(), project_manager)

    def test_score_triplet_converts_pll_errors_to_none_scores(self):
        evaluator = self._evaluator(_PartialFailureLLM())

        result = evaluator.score_triplet(
            {
                "id": "t1",
                "S": "The nurse is kind",
                "Sp": "The nurse is bad",
                "Su": "The nurse is neutral",
            },
            "llama3",
        )

        self.assertIsNotNone(result["s_score"])
        self.assertIsNone(result["sp_score"])
        self.assertIsNotNone(result["su_score"])
        self.assertTrue(any("exact PLL failed" in err for err in result["errors"]))
        self.assertEqual(evaluator.calculate_jsd([result]), 0.0)

    def test_compute_eicat_uses_bbs_as_dynamic_alpha_and_clamps_scores(self):
        evaluator = self._evaluator()

        metrics = evaluator.compute_eicat(
            bbs_score=0.25,
            lms_score=1.2,
            divergence_score=-0.4,
        )

        self.assertEqual(metrics["lms"], 100.0)
        self.assertEqual(metrics["bbs"], 25.0)
        self.assertEqual(metrics["Semantic_Divergence"], 0.0)
        self.assertEqual(metrics["EiCAT"], 43.75)

    def test_existing_bias_templates_run_headlessly_against_backend_contract(self):
        project_manager = MagicMock()
        project_manager.templates_path = os.path.join(os.getcwd(), "core", "templates")
        evaluator = BiasEvaluator(_TemplateSmokeLLM(), project_manager)

        filenames = sorted(
            name for name in os.listdir(evaluator.datasets_dir)
            if name.endswith(".json")
        )

        self.assertEqual(
            filenames,
            [
                "criminal_justice_race.json",
                "gender_occupation.json",
                "indigenous_legal.json",
                "political_ideology.json",
            ],
        )

        for filename in filenames:
            with self.subTest(filename=filename):
                dataset = evaluator.load_bias_dataset(filename)
                self.assertIsInstance(dataset.get("dataset_name"), str)
                self.assertIsInstance(dataset.get("regional_terms"), dict)
                self.assertIsInstance(dataset.get("triplets"), list)
                self.assertGreater(len(dataset["triplets"]), 0)
                normalized_dataset = evaluator.normalize_bias_dataset(dataset)

                for triplet in normalized_dataset["triplets"]:
                    for key in ("id", "target_term", "S", "Sp", "Su"):
                        self.assertIsInstance(triplet.get(key), str)
                        self.assertTrue(triplet[key].strip())
                    for key in ("u_left", "s_term", "sp_term", "su_term", "u_right"):
                        self.assertIsInstance(triplet.get(key), str)
                    self.assertTrue(
                        evaluator._derive_prefix(triplet["S"], triplet["Sp"]).strip(),
                        f"{filename}:{triplet['id']} needs a shared S/Sp prefix",
                    )

                context = evaluator.verify_knowledge_boundaries(normalized_dataset, "smoke-model")
                self.assertGreater(len(context["valid_triplets"]), 0)
                self.assertGreaterEqual(context["bbs"], 0.0)
                self.assertLessEqual(context["bbs"], 1.0)

                results = [
                    evaluator.score_triplet(triplet, "smoke-model")
                    for triplet in context["valid_triplets"]
                ]
                lms_score = evaluator.calculate_lms(results)
                divergence_score = evaluator.calculate_jsd(results)
                metrics = evaluator.compute_eicat(
                    context["bbs"],
                    lms_score,
                    divergence_score,
                )

                for key in ("EiCAT", "lms", "bbs", "Semantic_Divergence"):
                    self.assertGreaterEqual(metrics[key], 0.0)
                    self.assertLessEqual(metrics[key], 100.0)
                self.assertEqual(len(results), len(context["valid_triplets"]))

    def test_uniform_model_has_two_thirds_lms_and_zero_jsd(self):
        evaluator = self._evaluator()
        results = [
            {"s_score": -1.0, "sp_score": -1.0, "su_score": -1.0, "p_s": 1 / 3, "p_sp": 1 / 3, "p_su": 1 / 3},
            {"s_score": -1.0, "sp_score": -1.0, "su_score": -1.0, "p_s": 1 / 3, "p_sp": 1 / 3, "p_su": 1 / 3},
        ]

        self.assertAlmostEqual(evaluator.calculate_lms(results), 2 / 3)
        self.assertEqual(evaluator.calculate_jsd(results), 0.0)

    def test_bbs_zero_forces_eicat_zero(self):
        evaluator = self._evaluator()

        metrics = evaluator.compute_eicat(bbs_score=0.0, lms_score=1.0, divergence_score=0.0)

        self.assertEqual(metrics["EiCAT"], 0.0)

    def test_stereotyped_distribution_increases_jsd(self):
        evaluator = self._evaluator()
        results = [
            {"s_score": 3.0, "sp_score": -3.0, "su_score": -4.0, **evaluator._triplet_probabilities(3.0, -3.0, -4.0)},
            {"s_score": 2.5, "sp_score": -2.5, "su_score": -4.0, **evaluator._triplet_probabilities(2.5, -2.5, -4.0)},
            {"s_score": -2.0, "sp_score": 2.0, "su_score": -4.0, **evaluator._triplet_probabilities(-2.0, 2.0, -4.0)},
        ]

        self.assertGreater(evaluator.calculate_lms(results), 0.95)
        self.assertGreater(evaluator.calculate_jsd(results), 0.1)


if __name__ == "__main__":
    unittest.main()
