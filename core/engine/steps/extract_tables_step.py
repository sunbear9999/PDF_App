"""
core/engine/steps/extract_tables_step.py

Deterministic, no-LLM step: fetches cached extracted tables for a document
via DocumentExtractionService.
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class ExtractTablesStep(BaseStep):
    step_type = "EXTRACT_TABLES"
    label = "Extract Tables"
    category = "Extraction"
    description = "Fetch cached extracted tables for a document."
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path"},
    }
    output_schema = {
        "tables": {"type": "json", "label": "Extracted tables"},
    }

    def execute(self, context: StepContext, inputs: dict):
        doc_path = inputs.get("doc_path") or context.state.get("target_doc")
        svc = context.document_extraction_service
        if not doc_path or svc is None:
            return self.build_result([])

        tables = svc.get_tables(doc_path)
        return self.build_result([
            {
                "table_id": t.table_id,
                "page_num": t.page_num,
                "caption": t.caption,
                "headers": list(t.headers or []),
                "extraction_strategy": t.extraction_strategy,
                "confidence": t.confidence,
                "dataset_id": t.dataset_id,
            }
            for t in tables
        ])
