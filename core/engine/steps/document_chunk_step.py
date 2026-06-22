"""
core/engine/steps/document_chunk_step.py

Migrated from MasterActionRunner._run_document_chunk().
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class DocumentChunkStep(BaseStep):
    step_type = "DOCUMENT_CHUNK"
    label = "Document Chunk"
    category = "Analysis"
    description = "Split a document into analysis chunks according to a contract."
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path"},
        "template": {"type": "object", "label": "Analysis Template"},
        "contract": {"type": "object", "label": "Analysis Contract"},
        "template_id": {"type": "string", "label": "Template ID"},
    }

    def execute(self, context: StepContext, inputs: dict):
        from core.engine.analysis_runtime import AnalysisRuntime
        runtime = AnalysisRuntime(
            context.project_manager,
            context.prompt_manager,
            context.ontology_registry,
        )

        template = inputs.get("template") or {}
        contract = inputs.get("contract") or context.state.get("analysis_contract") or {}
        doc_path = (
            inputs.get("doc_path")
            or context.state.get("analysis_doc_path")
            or context.state.get("target_doc")
        )
        template_id = (
            inputs.get("template_id")
            or context.state.get("analysis_template_id")
            or template.get("id")
            or "analysis"
        )

        chunks = runtime.chunk_document(doc_path, template_id, template, contract)
        if not chunks:
            raise ValueError("Could not parse document into analysis chunks.")

        return self.build_result(chunks)
