from __future__ import annotations

from typing import List, Optional

from core.ontology.registry import (
    BaseOntologyEdgePlugin,
    RelationTrait,
    VisualStyleDefinition,
)

LAW_NODES_ALL = [
    "law.issue", "law.holding", "law.doctrine", "law.fact",
    "law.party", "law.authority", "law.statute", "law.reasoning", "law.policy",
]


class SupportsEdge(BaseOntologyEdgePlugin):
    type_key = "law.supports"
    display_name = "Supports"
    description = "Fact, reasoning, or authority supports a holding, doctrine, or issue."
    traits: List[RelationTrait] = [RelationTrait.EVIDENTIARY]
    valid_source_types: List[str] = ["law.fact", "law.reasoning", "law.authority", "law.policy"]
    valid_target_types: List[str] = ["law.holding", "law.doctrine", "law.issue", "law.reasoning"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#4caf50", "solid")


class ContradictsEdge(BaseOntologyEdgePlugin):
    type_key = "law.contradicts"
    display_name = "Contradicts"
    description = "Fact, argument, or authority contradicts a holding or claim."
    traits: List[RelationTrait] = [RelationTrait.EVIDENTIARY]
    valid_source_types: List[str] = ["*"]
    valid_target_types: List[str] = ["*"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#f44336", "solid")


class OverrulesEdge(BaseOntologyEdgePlugin):
    type_key = "law.overrules"
    display_name = "Overrules"
    description = "One authority or holding overrules another."
    traits: List[RelationTrait] = [RelationTrait.HIERARCHICAL]
    valid_source_types: List[str] = ["law.authority", "law.holding"]
    valid_target_types: List[str] = ["law.authority", "law.holding"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#b71c1c", "solid")


class DistinguishesEdge(BaseOntologyEdgePlugin):
    type_key = "law.distinguishes"
    display_name = "Distinguishes"
    description = "The court's holding or reasoning distinguishes a prior authority."
    traits: List[RelationTrait] = [RelationTrait.SEMANTIC]
    valid_source_types: List[str] = ["law.holding", "law.reasoning"]
    valid_target_types: List[str] = ["law.authority"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#ff9800", "dash")


class AppliesToEdge(BaseOntologyEdgePlugin):
    type_key = "law.applies_to"
    display_name = "Applies To"
    description = "A doctrine or statute applies to a fact pattern or issue."
    traits: List[RelationTrait] = [RelationTrait.CAUSAL]
    valid_source_types: List[str] = ["law.doctrine", "law.statute"]
    valid_target_types: List[str] = ["law.fact", "law.issue", "law.holding"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#2196f3", "solid")


class ResolvesEdge(BaseOntologyEdgePlugin):
    type_key = "law.resolves"
    display_name = "Resolves"
    description = "The court's holding resolves a legal issue."
    traits: List[RelationTrait] = [RelationTrait.CAUSAL]
    valid_source_types: List[str] = ["law.holding"]
    valid_target_types: List[str] = ["law.issue"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#9c27b0", "solid")


class ArguesEdge(BaseOntologyEdgePlugin):
    type_key = "law.argues"
    display_name = "Argues"
    description = "A party argues a legal position, doctrine, or interpretation."
    traits: List[RelationTrait] = [RelationTrait.SEMANTIC]
    valid_source_types: List[str] = ["law.party"]
    valid_target_types: List[str] = ["law.issue", "law.doctrine", "law.holding", "law.reasoning"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#03a9f4", "solid")


class RebutsEdge(BaseOntologyEdgePlugin):
    type_key = "law.rebuts"
    display_name = "Rebuts"
    description = "A reasoning step, argument, or party rebuts an opposing position."
    traits: List[RelationTrait] = [RelationTrait.SEMANTIC]
    valid_source_types: List[str] = ["law.reasoning", "law.party", "law.holding"]
    valid_target_types: List[str] = ["law.reasoning", "law.party", "law.holding", "law.doctrine"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#ff5722", "dash")


class CitesEdge(BaseOntologyEdgePlugin):
    type_key = "law.cites"
    display_name = "Cites"
    description = "A holding, reasoning step, or authority cites another authority or statute."
    traits: List[RelationTrait] = [RelationTrait.CITATIONAL]
    valid_source_types: List[str] = ["law.authority", "law.holding", "law.reasoning"]
    valid_target_types: List[str] = ["law.authority", "law.statute"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#ffc107", "dash")


class FollowsEdge(BaseOntologyEdgePlugin):
    type_key = "law.follows"
    display_name = "Follows"
    description = "The court's holding or reasoning follows and applies a prior authority."
    traits: List[RelationTrait] = [RelationTrait.HIERARCHICAL, RelationTrait.CAUSAL]
    valid_source_types: List[str] = ["law.holding", "law.reasoning"]
    valid_target_types: List[str] = ["law.authority"]
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#43a047", "solid")


ALL_EDGE_TYPES = [
    SupportsEdge,
    ContradictsEdge,
    OverrulesEdge,
    DistinguishesEdge,
    AppliesToEdge,
    ResolvesEdge,
    ArguesEdge,
    RebutsEdge,
    CitesEdge,
    FollowsEdge,
]
