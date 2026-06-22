"""
core/engine/steps/graph_validator_step.py

Migrated from MasterActionRunner._run_graph_validator().
"""
from __future__ import annotations

import json

from core.engine.steps.base_step import BaseStep
from core.plugins.plugin_step_protocol import StepContext
from core.utils.json_utils import extract_and_heal_json


class GraphValidatorStep(BaseStep):
    step_type = "GRAPH_VALIDATOR"
    label = "Graph Validator"
    category = "Transform"
    description = "Validate and heal LLM-generated graph tuples against the ontology registry."
    input_schema = {
        "tuple_data": {"type": "string", "label": "Raw Graph JSON", "required": True},
    }

    def execute(self, context: StepContext, inputs: dict):
        raw_data = inputs.get("tuple_data", "{}")
        if isinstance(raw_data, dict):
            parsed = raw_data
        else:
            try:
                parsed = json.loads(raw_data)
            except Exception:
                success, parsed = extract_and_heal_json(str(raw_data))
                if not success:
                    return self.build_result("{}")

        nodes_in = parsed.get("entities", parsed.get("nodes", []))
        edges_in = parsed.get("relations", parsed.get("edges", []))
        ontology_registry = context.ontology_registry

        valid_nodes = {}
        for n in nodes_in:
            if isinstance(n, list) and len(n) >= 3:
                n_id, n_type, n_text = str(n[0]), str(n[1]), str(n[2])
                custom_props = n[3] if len(n) > 3 and isinstance(n[3], dict) else {}
                node_data = {
                    "id": n_id,
                    "type": n_type,
                    "text": n_text,
                    "exact_text": n_text if "quote" in n_type else None,
                }
                node_data.update(custom_props)
                valid_nodes[n_id] = node_data
            elif isinstance(n, dict):
                n_id = str(n.get("id", "")).strip()
                if not n_id:
                    continue
                requested_type = str(n.get("type") or n.get("entity_type") or "entity.text")
                if ontology_registry and requested_type not in getattr(ontology_registry, "entities", {}):
                    requested_type = "entity.text"
                text = str(n.get("text") or n.get("label") or n.get("note") or "").strip()
                node_data = dict(n)
                node_data.update({"id": n_id, "type": requested_type, "text": text})
                valid_nodes[n_id] = node_data

        healed_edges = []
        for e in edges_in:
            if isinstance(e, list) and len(e) >= 4:
                e_id, e_type, e_src, e_tgt = str(e[0]), str(e[1]), str(e[2]), str(e[3])
                custom_props = e[4] if len(e) > 4 and isinstance(e[4], dict) else {}

                if e_src not in valid_nodes or e_tgt not in valid_nodes:
                    continue

                src_type = valid_nodes[e_src]["type"]
                tgt_type = valid_nodes[e_tgt]["type"]

                if ontology_registry:
                    rel_bp = ontology_registry.get_relation_blueprint(e_type)
                    if not rel_bp or not rel_bp.allows(src_type, tgt_type):
                        valid_fallback = None
                        for potential_rel in ontology_registry.all_relations():
                            if potential_rel.allows(src_type, tgt_type):
                                valid_fallback = potential_rel.type_key
                                break
                        if valid_fallback:
                            e_type = valid_fallback
                        else:
                            continue

                edge_data = {"id": e_id, "type": e_type, "source": e_src, "target": e_tgt}
                edge_data.update(custom_props)
                healed_edges.append(edge_data)
            elif isinstance(e, dict):
                e_id = str(e.get("id", ""))
                e_type = str(e.get("type") or e.get("relation_type") or "relation.basic")
                e_src = str(e.get("source", ""))
                e_tgt = str(e.get("target", ""))
                if e_src not in valid_nodes or e_tgt not in valid_nodes:
                    continue
                src_type = valid_nodes[e_src]["type"]
                tgt_type = valid_nodes[e_tgt]["type"]
                if ontology_registry:
                    exact_relation = getattr(ontology_registry, "relations", {}).get(e_type)
                    if not exact_relation or not exact_relation.allows(src_type, tgt_type):
                        exact_relation = next(
                            (candidate for candidate in ontology_registry.all_relations()
                             if candidate.allows(src_type, tgt_type)),
                            None,
                        )
                    if not exact_relation:
                        continue
                    e_type = exact_relation.type_key
                edge_data = dict(e)
                edge_data.update({
                    "id": e_id or f"r{len(healed_edges) + 1}",
                    "type": e_type,
                    "source": e_src,
                    "target": e_tgt,
                })
                healed_edges.append(edge_data)

        result = json.dumps({"entities": list(valid_nodes.values()), "relations": healed_edges})
        return self.build_result(result)
