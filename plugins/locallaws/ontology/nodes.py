from __future__ import annotations

from typing import List, Optional

from core.ontology.registry import (
    BaseOntologyNodePlugin,
    FieldDefinition,
    VisualStyleDefinition,
)


class IssueNode(BaseOntologyNodePlugin):
    type_key = "law.issue"
    display_name = "Legal Issue"
    description = "A legal question presented for resolution by the court."
    requires_source = False
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#e67e22")
    fields: List[FieldDefinition] = [
        FieldDefinition("text", "Issue Text", "string", ""),
        FieldDefinition(
            "issue_type", "Issue Type", "string", "substantive",
            choices=["procedural", "substantive", "constitutional", "evidentiary"],
        ),
    ]
    default_properties = {"text": "", "issue_type": "substantive"}


class HoldingNode(BaseOntologyNodePlugin):
    type_key = "law.holding"
    display_name = "Holding"
    description = "The court's ruling or decision on a legal issue."
    requires_source = False
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#2980b9")
    fields: List[FieldDefinition] = [
        FieldDefinition("text", "Holding Text", "string", ""),
        FieldDefinition(
            "scope", "Scope", "string", "narrow",
            choices=["narrow", "broad", "per_curiam"],
        ),
        FieldDefinition("is_binding", "Is Binding Precedent", "bool", True),
    ]
    default_properties = {"text": "", "scope": "narrow", "is_binding": True}


class DoctrineNode(BaseOntologyNodePlugin):
    type_key = "law.doctrine"
    display_name = "Doctrine / Rule"
    description = "A legal rule, test, standard, or doctrine applied by the court."
    requires_source = False
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#1a237e")
    fields: List[FieldDefinition] = [
        FieldDefinition("text", "Doctrine Text", "string", ""),
        FieldDefinition(
            "doctrine_type", "Type", "string", "common_law",
            choices=["common_law", "statutory", "constitutional", "regulatory"],
        ),
        FieldDefinition("jurisdiction", "Jurisdiction", "string", ""),
        FieldDefinition("citation", "Source Citation", "string", ""),
    ]
    default_properties = {"text": "", "doctrine_type": "common_law", "jurisdiction": "", "citation": ""}


class KeyFactNode(BaseOntologyNodePlugin):
    type_key = "law.fact"
    display_name = "Key Fact"
    description = "A key factual finding — exact quote from the document required."
    requires_source = True
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#757575")
    fields: List[FieldDefinition] = [
        FieldDefinition("text", "Fact Text", "string", ""),
        FieldDefinition("disputed", "Disputed", "bool", False),
    ]
    default_properties = {"text": "", "disputed": False}


class PartyNode(BaseOntologyNodePlugin):
    type_key = "law.party"
    display_name = "Party"
    description = "A party to the case."
    requires_source = False
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#00897b")
    fields: List[FieldDefinition] = [
        FieldDefinition("name", "Party Name", "string", ""),
        FieldDefinition(
            "role", "Role", "string", "plaintiff",
            choices=["plaintiff", "defendant", "appellant", "appellee",
                     "petitioner", "respondent", "amicus"],
        ),
    ]
    default_properties = {"name": "", "role": "plaintiff"}


class AuthorityNode(BaseOntologyNodePlugin):
    type_key = "law.authority"
    display_name = "Authority / Precedent"
    description = "A cited case, statute, or secondary authority used in the analysis."
    requires_source = False
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#f9a825")
    fields: List[FieldDefinition] = [
        FieldDefinition("citation", "Citation", "string", ""),
        FieldDefinition(
            "authority_type", "Authority Type", "string", "mandatory",
            choices=["mandatory", "persuasive"],
        ),
        FieldDefinition("year", "Year", "string", ""),
        FieldDefinition("jurisdiction", "Jurisdiction", "string", ""),
    ]
    default_properties = {"citation": "", "authority_type": "mandatory", "year": "", "jurisdiction": ""}


class StatuteNode(BaseOntologyNodePlugin):
    type_key = "law.statute"
    display_name = "Statute / Regulation"
    description = "A statutory or regulatory provision applied in the case."
    requires_source = False
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#2e7d32")
    fields: List[FieldDefinition] = [
        FieldDefinition("citation", "Citation", "string", ""),
        FieldDefinition("text", "Provision Text", "string", ""),
        FieldDefinition("jurisdiction", "Jurisdiction", "string", ""),
    ]
    default_properties = {"citation": "", "text": "", "jurisdiction": ""}


class ReasoningNode(BaseOntologyNodePlugin):
    type_key = "law.reasoning"
    display_name = "Reasoning"
    description = "A step in the court's analytical reasoning chain."
    requires_source = False
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#7b1fa2")
    fields: List[FieldDefinition] = [
        FieldDefinition("text", "Reasoning Text", "string", ""),
        FieldDefinition(
            "reasoning_type", "Reasoning Type", "string", "analogical",
            choices=["policy", "textual", "historical", "structural", "analogical"],
        ),
    ]
    default_properties = {"text": "", "reasoning_type": "analogical"}


class PolicyNode(BaseOntologyNodePlugin):
    type_key = "law.policy"
    display_name = "Policy Consideration"
    description = "A policy rationale or consideration invoked in the court's reasoning."
    requires_source = False
    plugin_id: Optional[str] = None
    visual_style = VisualStyleDefinition("#bf360c")
    fields: List[FieldDefinition] = [
        FieldDefinition("text", "Policy Text", "string", ""),
        FieldDefinition(
            "policy_area", "Policy Area", "string", "institutional",
            choices=["economic", "social", "institutional", "constitutional", "efficiency"],
        ),
    ]
    default_properties = {"text": "", "policy_area": "institutional"}


ALL_NODE_TYPES = [
    IssueNode,
    HoldingNode,
    DoctrineNode,
    KeyFactNode,
    PartyNode,
    AuthorityNode,
    StatuteNode,
    ReasoningNode,
    PolicyNode,
]
