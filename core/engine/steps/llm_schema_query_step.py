"""
core/engine/steps/llm_schema_query_step.py

LLM_SCHEMA_QUERY — like LLM_QUERY but always forces json_mode=True and injects
a JSON Schema built from the active OntologyRegistry's entity/relation definitions.
Ensures highly structured, ontology-aware LLM responses.
"""
from __future__ import annotations

import json

from core.engine.steps.llm_query_step import LLMQueryStep
from core.plugins.plugin_step_protocol import StepContext


class LLMSchemaQueryStep(LLMQueryStep):
    step_type = "LLM_SCHEMA_QUERY"
    label = "LLM Schema Query"
    category = "AI"
    description = "LLM query with automatically enforced ontology-aware JSON schema."
    input_schema = {
        "query": {"type": "string", "label": "Query / User Message", "required": True},
        "entity_types": {
            "type": "array",
            "label": "Entity types to include in schema (empty = all)",
        },
        "relation_types": {
            "type": "array",
            "label": "Relation types to include in schema (empty = all)",
        },
    }

    def execute(self, context: StepContext, inputs: dict):
        # Inject ontology schema into the step proxy's output_schema so LLMQueryStep
        # treats it as a structured JSON mode request.
        schema = self._build_schema(context, inputs)

        step = getattr(context, "_step_proxy", None)
        if step is not None:
            # Temporarily attach schema; LLMQueryStep reads step.output_schema
            original = getattr(step, "output_schema", None)
            step.output_schema = schema
            result = super().execute(context, inputs)
            step.output_schema = original
        else:
            # No step proxy: inject directly via inputs
            enriched = dict(inputs)
            enriched["_output_schema"] = schema
            result = super().execute(context, enriched)

        return result

    def _build_schema(self, context: StepContext, inputs: dict) -> dict:
        """Build a JSON Schema from the active OntologyRegistry."""
        ontology = context.ontology_registry
        entity_filter = set(inputs.get("entity_types") or [])
        relation_filter = set(inputs.get("relation_types") or [])

        entity_types = []
        if ontology:
            for bp in (ontology.entities or {}).values():
                if entity_filter and bp.type_key not in entity_filter:
                    continue
                entity_types.append(bp.type_key)

        relation_types = []
        if ontology:
            for bp in ontology.all_relations():
                if relation_filter and bp.type_key not in relation_filter:
                    continue
                relation_types.append(bp.type_key)

        return {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "description": "Tuple: [id, type, text, optional_props]",
                        "prefixItems": [
                            {"type": "string"},
                            {"type": "string", "enum": entity_types or ["entity"]},
                            {"type": "string"},
                        ],
                    },
                },
                "relations": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "description": "Tuple: [id, type, source_id, target_id, optional_props]",
                        "prefixItems": [
                            {"type": "string"},
                            {"type": "string", "enum": relation_types or ["related_to"]},
                            {"type": "string"},
                            {"type": "string"},
                        ],
                    },
                },
            },
            "required": ["entities", "relations"],
        }
