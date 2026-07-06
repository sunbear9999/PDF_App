"""
core/engine/steps/extract_document_blocks_step.py

Deterministic, no-LLM step: fetches cached DocumentBlocks (layout-parsed,
semantically classified, section-tagged) for a document via
DocumentExtractionService. General-purpose primitive — any blueprint can
compose this ahead of an LLM step to get structured document content.
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class ExtractDocumentBlocksStep(BaseStep):
    step_type = "EXTRACT_DOCUMENT_BLOCKS"
    label = "Extract Document Blocks"
    category = "Extraction"
    description = (
        "Fetch cached, layout-parsed, semantically classified DocumentBlocks "
        "for a document (paragraphs, headings, captions, footnotes, etc.)."
    )
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path"},
    }
    output_schema = {
        "blocks": {"type": "json", "label": "Document blocks"},
    }

    def execute(self, context: StepContext, inputs: dict):
        doc_path = inputs.get("doc_path") or context.state.get("target_doc")
        svc = context.document_extraction_service
        if not doc_path or svc is None:
            return self.build_result([])

        blocks = svc.get_blocks(doc_path)
        return self.build_result([_block_dict(b) for b in blocks])


def _block_dict(b) -> dict:
    return {
        "block_id": b.block_id,
        "doc_id": b.doc_id,
        "page_num": b.page_num,
        "block_type": b.block_type,
        "exact_text": b.exact_text,
        "heading_level": b.heading_level,
        "section_path": list(b.section_path or []),
        "has_superscript": b.has_superscript,
    }
