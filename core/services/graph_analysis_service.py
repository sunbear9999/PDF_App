from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from core.models.ontology_model import EntityModel, RelationModel
from core.models.workspace_models import WorkspaceModel
from core.ontology.registry import OntologyRegistry, RelationTrait


@dataclass
class NodeGraphFacts:
    metrics: Dict[str, Any] = field(default_factory=dict)
    metric_labels: Dict[str, str] = field(default_factory=dict)
    child_ids: List[str] = field(default_factory=list)
    child_counts: Dict[str, int] = field(default_factory=dict)


class GraphAnalysisService:
    """Computes graph-derived facts without depending on GUI widgets."""

    def __init__(self, registry: OntologyRegistry | None = None):
        self.registry = registry or OntologyRegistry()

    def analyze_workspace(self, workspace: WorkspaceModel) -> Dict[str, NodeGraphFacts]:
        entities = {node.id: self._entity_from_node(node) for node in workspace.nodes}
        relations = [self._relation_from_edge(edge) for edge in workspace.edges]
        facts = {entity_id: NodeGraphFacts() for entity_id in entities}

        for entity_id, entity in entities.items():
            blueprint = self.registry.get_entity_blueprint(entity.entity_type)
            metrics = self.registry.compute_metrics(entity, relations, entities.get)
            facts[entity_id].metrics = metrics
            facts[entity_id].metric_labels = {
                metric.key: metric.label
                for metric in getattr(blueprint, "computed_metrics", [])
            }

        for relation in relations:
            relation_blueprint = self.registry.get_relation_blueprint(relation.relation_type)
            traits = {getattr(trait, "value", trait) for trait in relation_blueprint.traits}
            if not traits.intersection({RelationTrait.EVIDENTIARY.value, RelationTrait.HIERARCHICAL.value}):
                continue
            parent_id = relation.target_id
            child_id = relation.source_id
            if parent_id not in facts or child_id not in entities or parent_id == child_id:
                continue
            parent_facts = facts[parent_id]
            if child_id not in parent_facts.child_ids:
                parent_facts.child_ids.append(child_id)
            parent_facts.child_counts[relation.relation_type] = parent_facts.child_counts.get(relation.relation_type, 0) + 1

        return facts

    def _entity_from_node(self, node) -> EntityModel:
        properties = dict(getattr(node, "entity_properties", {}) or {})
        properties.setdefault("quote", getattr(node, "quote", "") or "")
        properties.setdefault("exact_text", getattr(node, "quote", "") or "")
        properties.setdefault("note_text", getattr(node, "note", "") or "")
        properties.setdefault("text", getattr(node, "note", "") or "")
        if getattr(node, "source_id", None):
            properties.setdefault("source_id", node.source_id)
        if getattr(node, "pdf_path", None):
            properties.setdefault("pdf_path", node.pdf_path)
        return EntityModel(
            id=node.id,
            entity_type=getattr(node, "entity_type", "") or "entity.text",
            origin_id=getattr(node, "highlight_id", None) or getattr(node, "pdf_path", None),
            properties=properties,
            state=dict(getattr(node, "entity_state", {}) or {}),
        )

    def _relation_from_edge(self, edge) -> RelationModel:
        return RelationModel(
            id=edge.id,
            source_id=edge.source,
            target_id=edge.target,
            relation_type=getattr(edge, "relation_type", "") or "relation.basic",
            evidence_ids=list(getattr(edge, "evidence_ids", []) or []),
            properties=dict(getattr(edge, "relation_properties", {}) or {}),
            state=dict(getattr(edge, "relation_state", {}) or {}),
        )
