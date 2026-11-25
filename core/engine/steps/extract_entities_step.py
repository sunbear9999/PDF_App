"""
core/engine/steps/extract_entities_step.py

Deterministic, no-LLM step: fetches cached person/organization entities for a
document via DocumentExtractionService (spaCy NER + regex fallback + dedup).
"""
from __future__ import annotations

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class ExtractEntitiesStep(BaseStep):
    step_type = "EXTRACT_ENTITIES"
    label = "Extract Entities"
    category = "Extraction"
    description = "Fetch cached person/organization entities extracted from a document."
    input_schema = {
        "doc_path": {"type": "string", "label": "Document Path"},
        "entity_type": {"type": "string", "label": "Entity type filter (optional)", "required": False},
    }
    output_schema = {
        "entities": {"type": "json", "label": "Extracted entities"},
    }

    def execute(self, context: StepContext, inputs: dict):
        doc_path = inputs.get("doc_path") or context.state.get("target_doc")
        svc = context.document_extraction_service
        if not doc_path or svc is None:
            return self.build_result([])

        entities = svc.get_entities(doc_path)
        entity_type = inputs.get("entity_type")
        if entity_type:
            entities = [e for e in entities if e.entity_type == entity_type]

        return self.build_result([
            {
                "entity_id": e.entity_id,
                "entity_type": e.entity_type,
                "canonical_text": e.canonical_text,
                "aliases": list(e.aliases or []),
                "properties": dict(e.properties or {}),
            }
            for e in entities
        ])
