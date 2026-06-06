# core/models/ontology_models.py
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional
from enum import Enum

class EntityType(str, Enum):
    QUESTION = "entity.question"
    CLAIM = "entity.claim"
    EVIDENCE = "entity.evidence"
    QUOTE = "entity.quote"
    SOURCE = "entity.source"
    FINDING = "entity.finding"
    CONCEPT = "entity.concept"
    TIMELINE_EVENT = "entity.timeline_event"
    COUNTERARGUMENT = "entity.counterargument"
    METHOD = "entity.method"
    DATA_TABLE = "entity.data_table"
    PERSON_ORG = "entity.person_org"
    TEXT = "entity.text"  # Fallback/basic node

class RelationType(str, Enum):
    SUPPORTS = "relation.supports"
    CONTRADICTS = "relation.contradicts"
    ANSWERS = "relation.answers"
    FOLLOW_UP = "relation.follow_up"
    DERIVED_FROM = "relation.derived_from"
    PART_OF = "relation.part_of"
    REFERENCES = "relation.references"
    CAUSES = "relation.causes"
    BEFORE_AFTER = "relation.before_after"
    CRITIQUES = "relation.critiques"
    SIMILAR_TO = "relation.similar_to"
    AUTHORED_BY = "relation.authored_by"
    BASIC = "relation.basic" # Fallback/untyped connection

class DictInterfaceMixin:
    """Provides dictionary-like .get() access for backwards compatibility."""
    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)
    
    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class EntityModel(DictInterfaceMixin):
    """The Global Node. Represents a Source, Quote, Person, Timeline Event, etc."""
    id: str
    entity_type: str
    origin_id: Optional[str] = None # e.g., PDF path or external URI
    properties: Dict[str, Any] = field(default_factory=dict) # custom variables (confidence, tags, etc.)
    state: Dict[str, Any] = field(default_factory=lambda: {"is_verified": True, "ai_generated": False})
    
    # Injected dynamically by the View Engine, NOT saved in the global entities table
    view_meta: Dict[str, Any] = field(default_factory=dict) 

@dataclass
class RelationModel(DictInterfaceMixin):
    """The Global Edge. Represents a semantic connection between two Entities."""
    id: str
    relation_type: str
    source_id: str
    target_id: str
    evidence_ids: List[str] = field(default_factory=list) # IDs of Quotes/Sources that justify this relation
    properties: Dict[str, Any] = field(default_factory=dict) # custom variables (weight, confidence, label)
    state: Dict[str, Any] = field(default_factory=lambda: {"is_verified": True})

@dataclass
class ViewModel(DictInterfaceMixin):
    """Replaces WorkspaceModel. Represents a specific presentation of the graph."""
    id: str
    view_type: str # e.g., "view.graph", "view.timeline", "view.debate"
    name: str
    properties: Dict[str, Any] = field(default_factory=dict)
    
@dataclass
class ViewEntityMetaModel(DictInterfaceMixin):
    """Maps a global entity to a specific view, holding presentation logic."""
    view_id: str
    entity_id: str
    x: float = 0.0
    y: float = 0.0
    color: Optional[str] = None
    is_collapsed: bool = False
    properties: Dict[str, Any] = field(default_factory=dict) # specific view overrides