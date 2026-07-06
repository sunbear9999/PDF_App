"""
core/engine/steps/extract_citations_step.py

Deterministic, no-LLM step: fetches the cached CitationIndex (bibliography
entries, in-text citations, weighted matches) for a document via
DocumentExtractionService.
"""
from __future__ import annotations

import dataclasses

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class ExtractCitationsStep(BaseStep):
    step_type = "EXTRACT_CITATIONS"
    label = "Extract Citations"
    category = "Extraction"
    description = (
        "Fetch the cached citation index (bibliography entries, in-text citations, "
        "and weighted matches) for a document."
    )
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path"},
    }
    output_schema = {
        "citation_index": {"type": "json", "label": "Citation index"},
    }

    def execute(self, context: StepContext, inputs: dict):
        doc_path = inputs.get("doc_path") or context.state.get("target_doc")
        svc = context.document_extraction_service
        if not doc_path or svc is None:
            return self.build_result({})

        index = svc.get_citation_index(doc_path)
        if index is None:
            return self.build_result({})

        return self.build_result(dataclasses.asdict(index))
