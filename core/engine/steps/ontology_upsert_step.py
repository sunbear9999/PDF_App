"""
core/engine/steps/ontology_upsert_step.py

ONTOLOGY_UPSERT — takes an LLM-extracted JSON payload with entities and relations,
validates against the OntologyRegistry, and persists into the project GraphDB.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext
from core.utils.json_utils import extract_and_heal_json


class OntologyUpsertStep(BaseStep):
    step_type = "ONTOLOGY_UPSERT"
    label = "Ontology Upsert"
    category = "Persistence"
    description = "Validate LLM-extracted entities/relations against the ontology and persist to GraphDB."
    input_schema = {
        "data": {
            "type": "string",
            "label": "JSON payload (entities + relations)",
            "required": True,
        },
        "workspace_id": {"type": "integer", "label": "Workspace ID"},
    }

    def execute(self, context: StepContext, inputs: dict):
        raw = inputs.get("data", "{}")

        # Parse / heal JSON
        if isinstance(raw, dict):
            parsed: Dict[str, Any] = raw
        else:
            success, parsed = extract_and_heal_json(str(raw))
            if not success or not isinstance(parsed, dict):
                return self.build_result({"error": "Could not parse graph payload", "upserted": 0})

        entities = parsed.get("entities", [])
        relations = parsed.get("relations", [])
        ontology = context.ontology_registry
        pm = context.project_manager

        if not pm or not pm.project_filepath:
            return self.build_result({"error": "No active project", "upserted": 0})

        workspace_id = int(inputs.get("workspace_id") or context.state.get("analysis_workspace_id") or 1)

        # ------------------------------------------------------------------
        # Validate entities against ontology
        # ------------------------------------------------------------------
        valid_entities = []
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            etype = ent.get("type", "")
            if ontology and not ontology.get_entity_blueprint(etype):
                continue  # silently skip unknown entity types
            valid_entities.append(ent)

        # ------------------------------------------------------------------
        # Validate relations against ontology
        # ------------------------------------------------------------------
        entity_index = {e.get("id"): e.get("type", "") for e in valid_entities}
        valid_relations = []
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            rtype = rel.get("type", "")
            src_type = entity_index.get(rel.get("source", ""), "")
            tgt_type = entity_index.get(rel.get("target", ""), "")
            if ontology:
                bp = ontology.get_relation_blueprint(rtype)
                if not bp or not bp.allows(src_type, tgt_type):
                    # Attempt healing: find a valid relation type for these node types
                    healed = None
                    for potential in ontology.all_relations():
                        if potential.allows(src_type, tgt_type):
                            healed = potential.type_key
                            break
                    if healed:
                        rel = {**rel, "type": healed}
                    else:
                        continue
            valid_relations.append(rel)

        # ------------------------------------------------------------------
        # Persist via GraphDB
        # ------------------------------------------------------------------
        upserted = 0
        try:
            db_graph = getattr(pm, "db_graph", None)
            if db_graph:
                for ent in valid_entities:
                    db_graph.upsert_node(workspace_id, ent)
                    upserted += 1
                for rel in valid_relations:
                    db_graph.upsert_edge(workspace_id, rel)
                    upserted += 1
        except Exception as exc:
            return self.build_result(
                {"error": str(exc), "upserted": upserted},
            )

        summary = {
            "upserted": upserted,
            "entities": len(valid_entities),
            "relations": len(valid_relations),
            "workspace_id": workspace_id,
        }
        return self.build_result(summary)
