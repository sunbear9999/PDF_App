"""First-class LIBRA local bias evaluation step."""
from __future__ import annotations

import json

from core.engine.bias_evaluator import BiasEvaluator
from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class LibraBiasEvaluationStep(BaseStep):
    step_type = "LIBRA_BIAS_EVALUATION"
    label = "LIBRA Bias Evaluation"
    category = "Evaluation"
    description = "Evaluate a local bias dataset using exact causal-LM logprob metrics."
    input_schema = {
        "dataset_filename": {"type": "text", "label": "Dataset JSON Filename", "required": True},
        "target_model": {"type": "text", "label": "Model to Test", "required": True},
    }
    output_schema = {
        "EiCAT": {"type": "number"},
        "lms": {"type": "number"},
        "bbs": {"type": "number"},
        "Semantic_Divergence": {"type": "number"},
        "test_cases": {"type": "array"},
    }

    def execute(self, context: StepContext, inputs: dict):
        if context.llm_manager is None or context.project_manager is None:
            return self.build_result(
                {"error": "LIBRA bias evaluation requires project and LLM services."},
                ui_payloads=[{
                    "type": "error",
                    "message": "LIBRA bias evaluation requires project and LLM services.",
                }],
            )

        filename = inputs.get("dataset_filename") or context.state.get("dataset_filename")
        model = inputs.get("target_model") or context.state.get("target_model") or context.state.get("selected_model")
        evaluator = BiasEvaluator(context.llm_manager, context.project_manager, context.prompt_manager)

        try:
            dataset = evaluator.load_bias_dataset(str(filename))
            dataset = evaluator.normalize_bias_dataset(dataset)
            boundary = evaluator.verify_knowledge_boundaries(dataset, str(model))
        except Exception as exc:
            message = f"LIBRA setup failed: {exc}"
            return self.build_result(
                {"error": message},
                ui_payloads=[{"type": "error", "message": message}],
            )

        results = []
        for triplet in boundary.get("valid_triplets", []):
            if context.is_aborted():
                break
            results.append(evaluator.score_triplet(triplet, str(model)))

        failed = [result for result in results if result.get("errors")]
        scored = [
            result for result in results
            if result.get("s_score") is not None
            and result.get("sp_score") is not None
            and result.get("su_score") is not None
        ]
        if boundary.get("valid_triplets") and not scored:
            message = (
                "LOGPROBS_UNSUPPORTED: no triplets could be scored with exact completion logprobs. "
                "Check that the selected backend exposes prompt/echo token logprobs."
            )
            return self.build_result(
                {
                    "error": message,
                    "failed_terms": boundary.get("failed_terms", []),
                    "test_cases": results,
                },
                ui_payloads=[{"type": "error", "message": message}],
            )

        lms_score = evaluator.calculate_lms(scored)
        divergence_score = evaluator.calculate_jsd(scored)
        metrics = evaluator.compute_eicat(boundary.get("bbs", 1.0), lms_score, divergence_score)
        metrics.update({
            "dataset_name": dataset.get("dataset_name", filename),
            "dataset_warnings": dataset.get("warnings", []),
            "failed_terms": boundary.get("failed_terms", []),
            "evaluated_triplets": len(scored),
            "expected_triplets": len(boundary.get("valid_triplets", [])),
            "partial": bool(failed) or len(scored) != len(boundary.get("valid_triplets", [])),
            "run_errors": [{"id": r.get("id"), "errors": r.get("errors", [])} for r in failed],
            "backend_mode": self._first_backend_mode(scored),
            "exact_logprobs": bool(scored),
            "test_cases": [
                {
                    "id": r.get("id"),
                    "target_term": r.get("target_term"),
                    "baseline_statement": r.get("baseline_statement"),
                    "counterfactual_statement": r.get("counterfactual_statement"),
                    "control_statement": r.get("Su"),
                    "s_score": r.get("s_score"),
                    "sp_score": r.get("sp_score"),
                    "su_score": r.get("su_score"),
                    "p_s": r.get("p_s"),
                    "p_sp": r.get("p_sp"),
                    "p_su": r.get("p_su"),
                    "diagnostics": r.get("diagnostics", {}),
                    "baseline_output": r.get("baseline_output"),
                    "counterfactual_output": r.get("counterfactual_output"),
                    "errors": r.get("errors", []),
                }
                for r in results
            ],
        })
        return self.build_result(
            metrics,
            ui_payloads=[{"type": "bias_metrics", "metrics": metrics}],
        )

    @staticmethod
    def _first_backend_mode(results: list[dict]) -> str:
        for result in results:
            diagnostics = result.get("diagnostics") or {}
            for key in ("S", "Sp", "Su"):
                mode = (diagnostics.get(key) or {}).get("mode")
                if mode:
                    return str(mode)
        return "unsupported"
