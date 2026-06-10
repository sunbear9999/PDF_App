# core/ontology/registry.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.models.ontology_model import EntityType, RelationType, ViewType

class RelationTrait(str, Enum):
    HIERARCHICAL = "trait.hierarchical"
    CAUSAL = "trait.causal"
    SEMANTIC = "trait.semantic"
    EVIDENTIARY = "trait.evidentiary"
    TEMPORAL = "trait.temporal"
    CITATIONAL = "trait.citational"
    AUTHORSHIP = "trait.authorship"

@dataclass(frozen=True)
class FieldDefinition:
    key: str
    label: str
    value_type: str = "string"
    default: Any = None
    editable: bool = True
    choices: List[Any] = field(default_factory=list)
    minimum: Optional[float] = None
    maximum: Optional[float] = None

@dataclass(frozen=True)
class RenderBlockDefinition:
    id: str
    block_type: str
    source: str
    label: str = ""
    visible_when: Optional[Callable[[Any], bool]] = None

@dataclass(frozen=True)
class ActionDefinition:
    id: str
    label: str
    intent: str
    contexts: List[str] = field(default_factory=list)
    icon: str = ""
    tooltip: str = ""

@dataclass(frozen=True)
class ComputedMetricDefinition:
    key: str
    label: str
    compute: Callable[[Any, Iterable[Any], Callable[[str], Optional[Any]]], Any]


# --- Lifecycle ECS Blueprint ---
@dataclass
class EntityBlueprint:
    type_key: str
    display_name: str
    description: str
    default_properties: Dict[str, Any] = field(default_factory=dict)
    default_state: Dict[str, Any] = field(default_factory=lambda: {"is_verified": True, "ai_generated": False})
    fields: List[FieldDefinition] = field(default_factory=list)
    render_blocks: List[RenderBlockDefinition] = field(default_factory=list)
    action_ids: List[str] = field(default_factory=lambda: ["entity.edit", "entity.color", "entity.resize", "entity.change_type", "entity.connect"])
    computed_metrics: List[ComputedMetricDefinition] = field(default_factory=list)
    extraction_hints: Dict[str, Any] = field(default_factory=dict)
    requires_source: bool = False
    plugin_id: Optional[str] = None
    
    # NEW: Automated Triggers
    on_created_intents: List[str] = field(default_factory=list)
    on_updated_intents: List[str] = field(default_factory=list)

    def build_default_properties(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        props = dict(self.default_properties)
        for field_def in self.fields:
            if field_def.key not in props and field_def.default is not None:
                props[field_def.key] = field_def.default
        if overrides:
            props.update(overrides)
        return props

    def build_default_state(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = dict(self.default_state)
        if overrides:
            state.update(overrides)
        return state


@dataclass
class RelationBlueprint:
    type_key: str
    display_name: str
    description: str = ""
    traits: List[RelationTrait] = field(default_factory=list)
    valid_source_types: List[str] = field(default_factory=lambda: ["*"])
    valid_target_types: List[str] = field(default_factory=lambda: ["*"])
    default_properties: Dict[str, Any] = field(default_factory=dict)
    default_state: Dict[str, Any] = field(default_factory=lambda: {"is_verified": True})
    fields: List[FieldDefinition] = field(default_factory=list)
    computed_effects: List[ComputedMetricDefinition] = field(default_factory=list)
    plugin_id: Optional[str] = None

    def allows(self, source_type: str, target_type: str) -> bool:
        return _matches_type(source_type, self.valid_source_types) and _matches_type(target_type, self.valid_target_types)

    def build_default_properties(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        props = dict(self.default_properties)
        for field_def in self.fields:
            if field_def.key not in props and field_def.default is not None:
                props[field_def.key] = field_def.default
        if overrides:
            props.update(overrides)
        return props

    def build_default_state(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = dict(self.default_state)
        if overrides:
            state.update(overrides)
        return state

@dataclass
class ViewBlueprint:
    type_key: str
    display_name: str
    description: str = ""
    layout_policy: str = "freeform"
    relation_traits: List[RelationTrait] = field(default_factory=list)
    query_policy: Dict[str, Any] = field(default_factory=dict)
    plugin_id: Optional[str] = None

def _matches_type(type_key: str, allowed: Iterable[str]) -> bool:
    allowed_set = set(allowed or [])
    return "*" in allowed_set or type_key in allowed_set

# --- Module Level Metric Functions (Fixes NameError) ---
def _claim_support_count(entity, relations, get_entity):
    return len(_claim_evidence_relations(entity, relations, get_entity, {RelationType.SUPPORTS.value}))

def _claim_contradiction_count(entity, relations, get_entity):
    return len(_claim_evidence_relations(entity, relations, get_entity, {RelationType.CONTRADICTS.value}))

def _claim_unique_source_count(entity, relations, get_entity):
    source_ids = set()
    for rel in _claim_evidence_relations(entity, relations, get_entity, {RelationType.SUPPORTS.value, RelationType.CONTRADICTS.value}):
        evidence = get_entity(rel.source_id)
        if evidence and (sid := evidence.properties.get("source_id") or evidence.properties.get("pdf_path") or evidence.origin_id):
            source_ids.add(sid)
    return len(source_ids)

def _claim_confidence(entity, relations, get_entity):
    support_scores, contradiction_scores, source_ids = [], [], set()
    for rel in _claim_evidence_relations(entity, relations, get_entity, {RelationType.SUPPORTS.value, RelationType.CONTRADICTS.value}):
        evidence = get_entity(rel.source_id)
        rel_conf = float(rel.properties.get("confidence", rel.properties.get("strength", 1.0)) or 0.0)
        ev_strength = float(evidence.properties.get("strength", 1.0) or 0.0) if evidence else 1.0
        score = max(0.0, min(1.0, rel_conf * ev_strength))
        
        if rel.relation_type == RelationType.SUPPORTS.value: support_scores.append(score)
        else: contradiction_scores.append(score)
            
        if evidence and (sid := evidence.properties.get("source_id") or evidence.properties.get("pdf_path") or evidence.origin_id):
            source_ids.add(sid)

    if not support_scores and not contradiction_scores: return float(entity.properties.get("confidence", 0.0) or 0.0)
    support_strength = 1.0
    for score in support_scores:
        support_strength *= (1.0 - score)
    support_strength = 1.0 - support_strength
    contradiction_strength = 1.0
    for score in contradiction_scores:
        contradiction_strength *= (1.0 - score)
    contradiction_strength = 1.0 - contradiction_strength
    source_bonus = min(0.2, 0.045 * max(0, len(source_ids) - 1))
    volume_bonus = min(0.15, 0.025 * max(0, len(support_scores) - 1))
    contradiction_penalty = contradiction_strength * (0.55 + min(0.25, 0.05 * max(0, len(contradiction_scores) - 1)))
    return round(max(0.0, min(1.0, support_strength + source_bonus + volume_bonus - contradiction_penalty)), 3)

def _claim_evidence_relations(entity, relations, get_entity, relation_types):
    """Return direct evidence plus evidence that supports/contradicts reasoning for a claim."""
    direct = [rel for rel in relations if rel.target_id == entity.id and rel.relation_type in relation_types]
    reasoning_link_types = set(relation_types)
    if RelationType.SUPPORTS.value in relation_types:
        reasoning_link_types.add(RelationType.REASONS.value)
    reasoning_ids = {
        rel.source_id
        for rel in relations
        if rel.target_id == entity.id and rel.relation_type in reasoning_link_types
        if (reason := get_entity(rel.source_id)) and reason.entity_type == EntityType.REASONING.value
    }
    inherited = [
        rel for rel in relations
        if rel.target_id in reasoning_ids and rel.relation_type in relation_types
    ]
    return direct + inherited

def _source_cited_count(entity, relations, get_entity):
    return sum(1 for rel in relations if rel.source_id == entity.id and rel.relation_type == RelationType.REFERENCES.value)

def _source_in_text_count(entity, relations, get_entity):
    return sum(1 for rel in relations if rel.target_id == entity.id and rel.relation_type == "relation.attributed_to")

def _source_most_cited(entity, relations, get_entity):
    citations = {}
    for rel in relations:
        if rel.source_id == entity.id and rel.relation_type == RelationType.REFERENCES.value:
            citations[rel.target_id] = citations.get(rel.target_id, 0) + 1
    if not citations: return "None"
    top_target = max(citations, key=citations.get)
    target_entity = get_entity(top_target)
    return target_entity.properties.get("title", top_target) if target_entity else top_target


class OntologyRegistry:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.entities, cls._instance.relations, cls._instance.views, cls._instance.actions = {}, {}, {}, {}
            cls._instance._register_core_ontology()
        return cls._instance

    def register_entity(self, blueprint: EntityBlueprint): self.entities[blueprint.type_key] = blueprint
    def register_relation(self, blueprint: RelationBlueprint): self.relations[blueprint.type_key] = blueprint
    def register_view(self, blueprint: ViewBlueprint): self.views[blueprint.type_key] = blueprint
    def register_action(self, action: ActionDefinition): self.actions[action.id] = action

    def get_entity_blueprint(self, type_key: str) -> EntityBlueprint: return self.entities.get(type_key) or self.entities[EntityType.TEXT.value]
    def get_relation_blueprint(self, type_key: str) -> RelationBlueprint: return self.relations.get(type_key) or self.relations[RelationType.BASIC.value]
    def get_view_blueprint(self, type_key: str) -> ViewBlueprint: return self.views.get(type_key) or self.views[ViewType.GRAPH.value]
    def get_action_definition(self, action_id: str) -> Optional[ActionDefinition]: return self.actions.get(action_id)

    def all_entities(self) -> Iterable[EntityBlueprint]: return self.entities.values()
    def all_relations(self) -> Iterable[RelationBlueprint]: return self.relations.values()
    def all_views(self) -> Iterable[ViewBlueprint]: return self.views.values()
    def all_actions(self) -> Iterable[ActionDefinition]: return self.actions.values()

    def validate_relation(self, relation_type: str, source_type: str, target_type: str) -> bool:
        return self.get_relation_blueprint(relation_type).allows(source_type, target_type)

    def compute_metrics(self, entity, relations, get_entity: Callable[[str], Optional[Any]]) -> Dict[str, Any]:
        blueprint = self.get_entity_blueprint(entity.entity_type)
        return {metric.key: metric.compute(entity, relations, get_entity) for metric in blueprint.computed_metrics}
    
    def _register_core_ontology(self):
        for action in [
            ActionDefinition("entity.edit", "Edit", "workspace.node.edit", ["node"], "✎", "Edit note"),
            ActionDefinition("entity.color", "Color", "workspace.node.color", ["node"], "●", "Change color"),
            ActionDefinition("entity.resize", "Size", "workspace.node.resize", ["node"], "↕", "Change text size"),
            ActionDefinition("entity.change_type", "Type", "workspace.node.change_type", ["node"], "T", "Change node type"),
            ActionDefinition("entity.connect", "Connect", "workspace.node.connect", ["node"], "⛓", "Connect to another node"),
            ActionDefinition("entity.toggle_children", "Children", "workspace.node.toggle_children", ["node"], "▾", "Collapse or expand child nodes"),
            ActionDefinition("entity.jump_source", "Source", "workspace.node.jump_source", ["node"], "↗", "Jump to source"),
            ActionDefinition("entity.copy_citation", "Cite", "workspace.node.copy_citation", ["node"], "C", "Copy citation"),
            ActionDefinition("entity.verify", "Verify", "workspace.node.verify", ["node"], "!", "Verify entity"),
        ]:
            self.register_action(action)

        common_blocks = [
            RenderBlockDefinition("header", "header", "properties.title", "Title"),
            RenderBlockDefinition("body", "text", "properties.text", "Text"),
            RenderBlockDefinition("verify", "verify_badge", "state.is_verified", "Verify"),
        ]
        common_actions = ["entity.edit", "entity.color", "entity.resize", "entity.change_type", "entity.connect"]

        def entity(type_key, name, description, properties=None, fields=None, actions=None, metrics=None, hints=None, requires_source=False, on_created=None, render_blocks=None):
            self.register_entity(EntityBlueprint(
                type_key=type_key, display_name=name, description=description,
                default_properties=properties or {}, fields=fields or [],
                render_blocks=list(render_blocks or common_blocks), action_ids=actions or list(common_actions),
                computed_metrics=metrics or [], extraction_hints=hints or {},
                requires_source=requires_source, on_created_intents=on_created or []
            ))

        entity(EntityType.TEXT.value, "Text", "A basic text node.", {"text": "", "title": ""})
        entity(EntityType.QUOTE.value, "Quote", "An exact quotation.", {"exact_text": "", "page": None, "source_id": ""}, hints={"extractors": ["date", "person_org"]}, requires_source=True)
        
        entity(EntityType.EVIDENCE.value, "Evidence", "Supports or contradicts a claim.", {"strength": 1.0}, 
            fields=[FieldDefinition("strength", "Strength", "float", 1.0, minimum=0.0, maximum=1.0)], requires_source=True)
            
        claim_blocks = [
            RenderBlockDefinition("body", "text", "properties.text", "Text"),
            RenderBlockDefinition("supporting", "metric_badge", "metrics.supporting_evidence_count", "For"),
            RenderBlockDefinition("contradicting", "metric_badge", "metrics.contradicting_evidence_count", "Against"),
            RenderBlockDefinition("sources", "metric_badge", "metrics.unique_source_count", "Sources"),
            RenderBlockDefinition("confidence", "metric_badge", "metrics.computed_confidence", "Conf"),
        ]

        entity(EntityType.CLAIM.value, "Claim", "An assertion.", {"confidence": 0.0, "claim_type": "factual"}, 
            fields=[
                FieldDefinition("confidence", "Confidence", "float", 0.0, minimum=0.0, maximum=1.0),
                FieldDefinition("claim_type", "Claim Type", "string", "factual")
            ], metrics=[
                ComputedMetricDefinition("supporting_evidence_count", "Supporting", _claim_support_count),
                ComputedMetricDefinition("contradicting_evidence_count", "Contradicting", _claim_contradiction_count),
                ComputedMetricDefinition("unique_source_count", "Unique Sources", _claim_unique_source_count),
                ComputedMetricDefinition("computed_confidence", "Calculated Score", _claim_confidence)
            ], actions=list(common_actions) + ["entity.toggle_children"], render_blocks=claim_blocks)

        reasoning_blocks = [
            RenderBlockDefinition("body", "text", "properties.text", "Reasoning"),
            RenderBlockDefinition("role", "field_badge", "properties.reasoning_role", "Role"),
            RenderBlockDefinition("confidence", "field_badge", "properties.confidence", "Conf"),
            RenderBlockDefinition("verify", "verify_badge", "state.is_verified", "Verify"),
        ]
        entity(
            EntityType.REASONING.value,
            "Reasoning",
            "An inferential bridge between evidence and a claim.",
            {"text": "", "reasoning_role": "premise", "confidence": 0.75},
            fields=[
                FieldDefinition("reasoning_role", "Reasoning Role", "string", "premise", choices=["premise", "warrant", "assumption", "interpretation", "limitation"]),
                FieldDefinition("confidence", "Confidence", "float", 0.75, minimum=0.0, maximum=1.0),
            ],
            actions=list(common_actions) + ["entity.toggle_children"],
            render_blocks=reasoning_blocks,
        )
            
        entity(EntityType.QUESTION.value, "Question", "Open research question.", {"status": "open"}, 
            fields=[FieldDefinition("status", "Status", "string", "open", choices=["open", "answered"])])
            
        entity(EntityType.FINDING.value, "Finding", "Synthesized knowledge.", {"confidence": 0.0})
        entity(EntityType.CONCEPT.value, "Concept", "Topical grouping.", {"category": ""})
        entity(EntityType.COUNTERARGUMENT.value, "Counterargument", "An objection or opposing argument.", {"target_claim_id": "", "severity": "medium"})
        
        entity(EntityType.TIMELINE_EVENT.value, "Timeline Event", "An event with a date.", {"date": "", "certainty": "unknown"}, 
            fields=[
                FieldDefinition("date", "Date / Year", "string", ""),
                FieldDefinition("normalized_date", "Normalized Date", "string", ""),
                FieldDefinition("certainty", "Certainty", "string", "unknown", choices=["exact", "approximate"])
            ], hints={"extractors": ["date"]}, requires_source=True)
            
        entity(EntityType.PERSON_ORG.value, "Person/Org", "Entity recognition.", {"role": ""}, 
            fields=[
                FieldDefinition("role", "Role / Type", "string", "", choices=["", "person", "org", "author"]),
                FieldDefinition("context", "Context", "string", ""),
            ], hints={"extractors": ["ner"]}, requires_source=True)

        # The Source automatically triggers background workflows when it is created
        entity(EntityType.SOURCE.value, "Source", "A referenced document.", {"title": "", "authors": "", "year": ""}, 
            fields=[
                FieldDefinition("title", "Title", "string", ""),
                FieldDefinition("authors", "Authors", "string", ""),
                FieldDefinition("year", "Year", "string", ""),
                FieldDefinition("doi", "DOI", "string", ""),
                FieldDefinition("bibliography_entry", "Bibliography Entry", "string", ""),
            ], metrics=[
                ComputedMetricDefinition("cited_sources_count", "Bibliography Size", _source_cited_count),
                ComputedMetricDefinition("in_text_citation_count", "In-Text Citations", _source_in_text_count),
                ComputedMetricDefinition("most_cited_source", "Heavily Relied On", _source_most_cited),
            ], hints={"extractors": ["citation", "duplicate_source"]}, 
            on_created=["document.intent.extract_citations", "document.intent.extract_timeline"],
            requires_source=True)
            
        entity(EntityType.METHOD.value, "Method", "Research methodology.", {"method_type": ""}, requires_source=True)
        entity(EntityType.DATA_TABLE.value, "Data/Table", "Structured data.", {"units": ""}, requires_source=True)

        def relation(type_key, name, traits=None, sources=None, targets=None, props=None, fields=None):
            self.register_relation(RelationBlueprint(
                type_key=type_key, display_name=name, traits=traits or [],
                valid_source_types=sources or ["*"], valid_target_types=targets or ["*"],
                default_properties=props or {}, fields=fields or [],
            ))

        confidence_fields = [
            FieldDefinition("strength", "Strength", "float", 1.0, minimum=0.0, maximum=1.0),
            FieldDefinition("confidence", "Confidence", "float", 1.0, minimum=0.0, maximum=1.0),
        ]
        context_field = [FieldDefinition("context", "Context", "string", "")]
        citation_fields = [
            FieldDefinition("citation_context", "Citation Context", "string", ""),
            FieldDefinition("confidence", "Confidence", "float", 1.0, minimum=0.0, maximum=1.0),
        ]
        relation(RelationType.BASIC.value, "Connection", props={"label": "", "color": "#888"})
        evidence_sources = [EntityType.QUOTE.value, EntityType.EVIDENCE.value, EntityType.CLAIM.value, EntityType.FINDING.value, EntityType.REASONING.value]
        argumentative_targets = [EntityType.CLAIM.value, EntityType.FINDING.value, EntityType.REASONING.value]
        relation(RelationType.SUPPORTS.value, "Supports", [RelationTrait.EVIDENTIARY], evidence_sources, argumentative_targets, {"strength": 1.0, "confidence": 1.0}, confidence_fields)
        relation(RelationType.CONTRADICTS.value, "Contradicts", [RelationTrait.EVIDENTIARY], evidence_sources, argumentative_targets, {"strength": 1.0, "confidence": 1.0}, confidence_fields)
        relation(RelationType.REASONS.value, "Reasons For", [RelationTrait.HIERARCHICAL, RelationTrait.EVIDENTIARY], [EntityType.REASONING.value], [EntityType.CLAIM.value, EntityType.FINDING.value, EntityType.REASONING.value], {"confidence": 0.75, "reasoning_note": ""}, [FieldDefinition("confidence", "Confidence", "float", 0.75, minimum=0.0, maximum=1.0), FieldDefinition("reasoning_note", "Reasoning Note", "string", "")])
        relation(RelationType.ANSWERS.value, "Answers", [RelationTrait.HIERARCHICAL], [EntityType.CLAIM.value, EntityType.FINDING.value, EntityType.QUOTE.value, EntityType.EVIDENCE.value], [EntityType.QUESTION.value], {"completeness": 1.0}, [FieldDefinition("completeness", "Completeness", "float", 1.0, minimum=0.0, maximum=1.0)])
        relation(RelationType.FOLLOW_UP.value, "Follow-up", [RelationTrait.HIERARCHICAL], [EntityType.QUESTION.value], [EntityType.QUESTION.value], {"priority": "normal", "dependency": ""}, [FieldDefinition("priority", "Priority", "string", "normal", choices=["low", "normal", "high"]), FieldDefinition("dependency", "Dependency", "string", "")])
        relation(RelationType.DERIVED_FROM.value, "Derived From", [RelationTrait.HIERARCHICAL, RelationTrait.EVIDENTIARY], [EntityType.CLAIM.value, EntityType.FINDING.value, EntityType.REASONING.value, EntityType.COUNTERARGUMENT.value, EntityType.TIMELINE_EVENT.value, EntityType.METHOD.value, EntityType.DATA_TABLE.value], [EntityType.QUOTE.value, EntityType.EVIDENCE.value, EntityType.SOURCE.value, EntityType.DATA_TABLE.value], {"reasoning_note": ""}, [FieldDefinition("reasoning_note", "Reasoning Note", "string", "")])
        relation(RelationType.PART_OF.value, "Part Of", [RelationTrait.HIERARCHICAL], ["*"], ["*"], {"weight": 1.0})
        relation(RelationType.REFERENCES.value, "References", [RelationTrait.CITATIONAL], [EntityType.QUOTE.value, EntityType.EVIDENCE.value, EntityType.CLAIM.value, EntityType.FINDING.value, EntityType.SOURCE.value, EntityType.PERSON_ORG.value], [EntityType.SOURCE.value, EntityType.QUOTE.value, EntityType.EVIDENCE.value, EntityType.PERSON_ORG.value], {"citation_context": ""}, citation_fields)
        relation(RelationType.CAUSES.value, "Causes", [RelationTrait.CAUSAL], [EntityType.TIMELINE_EVENT.value, EntityType.CLAIM.value, EntityType.FINDING.value], [EntityType.TIMELINE_EVENT.value, EntityType.CLAIM.value, EntityType.FINDING.value], {"certainty": "unknown"}, [FieldDefinition("certainty", "Certainty", "string", "unknown", choices=["unknown", "weak", "moderate", "strong"])])
        relation(RelationType.BEFORE_AFTER.value, "Before/After", [RelationTrait.TEMPORAL], [EntityType.TIMELINE_EVENT.value], [EntityType.TIMELINE_EVENT.value], {"date_certainty": "unknown", "order": "before"}, [FieldDefinition("order", "Order", "string", "before", choices=["before", "after"]), FieldDefinition("date_certainty", "Date Certainty", "string", "unknown", choices=["unknown", "approximate", "exact"])])
        relation(RelationType.CRITIQUES.value, "Critiques", [RelationTrait.EVIDENTIARY], [EntityType.QUOTE.value, EntityType.EVIDENCE.value, EntityType.SOURCE.value, EntityType.CLAIM.value, EntityType.COUNTERARGUMENT.value], [EntityType.CLAIM.value, EntityType.FINDING.value, EntityType.METHOD.value, EntityType.SOURCE.value], {"severity": "medium"}, [FieldDefinition("severity", "Severity", "string", "medium", choices=["low", "medium", "high"]), FieldDefinition("confidence", "Confidence", "float", 1.0, minimum=0.0, maximum=1.0)])
        relation(RelationType.SIMILAR_TO.value, "Similar To", [RelationTrait.SEMANTIC], ["*"], ["*"], {"similarity_score": 0.0}, [FieldDefinition("similarity_score", "Similarity", "float", 0.0, minimum=0.0, maximum=1.0)])
        relation(RelationType.AUTHORED_BY.value, "Authored By", [RelationTrait.AUTHORSHIP], [EntityType.SOURCE.value, EntityType.QUOTE.value, EntityType.EVIDENCE.value], [EntityType.PERSON_ORG.value], {"role": "author"}, [FieldDefinition("role", "Role", "string", "author", choices=["author", "editor", "translator", "contributor"])])
        relation(
            "relation.attributed_to",
            "Attributed To",
            [RelationTrait.CITATIONAL],
            [EntityType.QUOTE.value, EntityType.EVIDENCE.value, EntityType.TEXT.value, EntityType.SOURCE.value],
            [EntityType.SOURCE.value],
            {"context": ""},
            context_field,
        )
        self.register_view(ViewBlueprint(ViewType.GRAPH.value, "Graph View"))
        self.register_view(ViewBlueprint(ViewType.EVIDENCE_MAP.value, "Evidence Map", relation_traits=[RelationTrait.EVIDENTIARY]))
        self.register_view(ViewBlueprint(ViewType.QUESTION_TREE.value, "Question Tree", relation_traits=[RelationTrait.HIERARCHICAL]))
        self.register_view(ViewBlueprint(ViewType.DEBATE.value, "Debate View", relation_traits=[RelationTrait.EVIDENTIARY, RelationTrait.SEMANTIC]))
        self.register_view(ViewBlueprint(ViewType.SOURCE.value, "Source View", relation_traits=[RelationTrait.HIERARCHICAL, RelationTrait.CITATIONAL]))
        self.register_view(ViewBlueprint(ViewType.ARGUMENT_OUTLINE.value, "Argument Outline", relation_traits=[RelationTrait.HIERARCHICAL, RelationTrait.EVIDENTIARY]))
        self.register_view(ViewBlueprint(ViewType.CONCEPT_MAP.value, "Concept Map", relation_traits=[RelationTrait.SEMANTIC]))
        self.register_view(ViewBlueprint(ViewType.DATA.value, "Data View"))
        self.register_view(ViewBlueprint(ViewType.CITATION_NETWORK.value, "Citation Network", relation_traits=[RelationTrait.CITATIONAL]))
        self.register_view(ViewBlueprint(ViewType.TIMELINE.value, "Timeline View", relation_traits=[RelationTrait.TEMPORAL]))
