"""Expose registered ontology categories to workflows on demand."""
from __future__ import annotations

import json

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext


class OntologyCatalogStep(BaseStep):
    step_type = "ONTOLOGY_CATALOG"
    label = "Ontology Catalog"
    category = "Context"
    description = "Load registered entity and relation categories for downstream tools."
    input_schema = {
        "entity_types": {"type": "array", "label": "Entity Type Filter"},
        "relation_types": {"type": "array", "label": "Relation Type Filter"},
        "include_descriptions": {"type": "boolean", "label": "Include Descriptions"},
    }
    output_schema = {
        "entity_types": {"type": "array"},
        "relation_types": {"type": "array"},
    }

    def execute(self, context: StepContext, inputs: dict):
        registry = context.ontology_registry
        if not registry:
            return self.build_result(json.dumps({"entity_types": [], "relation_types": []}))

        entity_filter = set(self._normalize(inputs.get("entity_types")))
        relation_filter = set(self._normalize(inputs.get("relation_types")))
        include_descriptions = inputs.get("include_descriptions", True)

        entities = []
        for item in registry.all_entities():
            if entity_filter and item.type_key not in entity_filter:
                continue
            entry = {
                "type": item.type_key,
                "label": item.display_name,
                "requires_source": bool(item.requires_source),
            }
            if include_descriptions:
                entry["description"] = item.description
            entities.append(entry)

        relations = []
        for item in registry.all_relations():
            if relation_filter and item.type_key not in relation_filter:
                continue
            entry = {
                "type": item.type_key,
                "label": item.display_name,
                "valid_source_types": list(item.valid_source_types),
                "valid_target_types": list(item.valid_target_types),
            }
            if include_descriptions:
                entry["description"] = item.description
            relations.append(entry)

        return self.build_result(json.dumps({
            "entity_types": entities,
            "relation_types": relations,
        }, ensure_ascii=False))

    @staticmethod
    def _normalize(value) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = [part.strip() for part in value.split(",")]
        if not isinstance(value, list):
            value = [value]
        return [str(item).strip() for item in value if str(item).strip()]
