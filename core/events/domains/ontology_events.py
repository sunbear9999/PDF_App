from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from core.models.ontology_model import (
    EntityIntent,
    EntityPayload,
    RelationIntent,
    RelationPayload,
    ViewIntent,
    ViewPayload,
)

class OntologyEvent(str, Enum):
    NODE_COLLAPSE_REQUESTED = "ontology.node.collapse_requested"
    NODE_EXPANDED = "ontology.node.expanded"
    NODE_COLLAPSED = "ontology.node.collapsed"
    BLUEPRINT_REQUESTED = "ontology.blueprint.requested"

@dataclass
class OntologyEventPayload:
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    entity_type: Optional[str] = None
    relation_type: Optional[str] = None
    blueprint_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

__all__ = [
    "OntologyEvent",
    "OntologyEventPayload",
    "EntityIntent",
    "EntityPayload",
    "RelationIntent",
    "RelationPayload",
    "ViewIntent",
    "ViewPayload",
]
