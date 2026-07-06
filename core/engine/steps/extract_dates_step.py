"""
core/engine/steps/extract_dates_step.py

Deterministic, no-LLM step: fetches cached dates extracted from a document
via DocumentExtractionService (7-tier regex cascade with span dedup).
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class ExtractDatesStep(BaseStep):
    step_type = "EXTRACT_DATES"
    label = "Extract Dates"
    category = "Extraction"
    description = "Fetch cached dates extracted from a document."
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path"},
    }
    output_schema = {
        "dates": {"type": "json", "label": "Extracted dates"},
    }

    def execute(self, context: StepContext, inputs: dict):
        doc_path = inputs.get("doc_path") or context.state.get("target_doc")
        svc = context.document_extraction_service
        if not doc_path or svc is None:
            return self.build_result([])

        dates = svc.get_dates(doc_path)
        return self.build_result([
            {
                "date_id": d.date_id,
                "exact_text": d.exact_text,
                "normalized_date": d.normalized_date,
                "certainty": d.certainty,
                "context_text": d.context_text,
            }
            for d in dates
        ])
