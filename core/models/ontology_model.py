# core/models/ontology_model.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

class EntityType(str, Enum):
    TEXT = "entity.text"
    QUESTION = "entity.question"
    CLAIM = "entity.claim"
    REASONING = "entity.reasoning"
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

class RelationType(str, Enum):
    BASIC = "relation.basic"
    SUPPORTS = "relation.supports"
    CONTRADICTS = "relation.contradicts"
    REASONS = "relation.reasons"
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

class ViewType(str, Enum):
    GRAPH = "view.graph"
    EVIDENCE_MAP = "view.evidence_map"
    QUESTION_TREE = "view.question_tree"
    TIMELINE = "view.timeline"
    DEBATE = "view.debate"
    SOURCE = "view.source"
    ARGUMENT_OUTLINE = "view.argument_outline"
    CONCEPT_MAP = "view.concept_map"
    DATA = "view.data"
    CITATION_NETWORK = "view.citation_network"

class EntityIntent(Enum):
    ADD = auto()
    UPDATE_PROPERTIES = auto()
    UPDATE_STATE = auto()
    UPDATE_VIEW_META = auto()
    CHANGE_TYPE = auto()
    DELETE_FROM_VIEW = auto()
    PURGE_GLOBALLY = auto()
    VERIFY = auto()

class RelationIntent(Enum):
    ADD = auto()
    UPDATE_PROPERTIES = auto()
    UPDATE_STATE = auto()
    DELETE = auto()

class ViewIntent(Enum):
    ADD = auto()
    UPDATE = auto()
    DELETE = auto()
    SET_ACTIVE = auto()

class DictInterfaceMixin:
    """Provides dictionary-like .get() access for backwards compatibility."""
    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)
    
    def to_dict(self) -> dict:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

@dataclass
class EntityModel(DictInterfaceMixin):
    """The Global Node. Represents a Source, Quote, Person, Timeline Event, etc."""
    id: str
    entity_type: str = EntityType.TEXT.value
    origin_id: Optional[str] = None # e.g., PDF path or external URI
    properties: Dict[str, Any] = field(default_factory=dict) # custom variables (confidence, tags, etc.)
    state: Dict[str, Any] = field(default_factory=lambda: {"is_verified": True, "ai_generated": False})
    
    # Injected dynamically by the View Engine, NOT saved in the global entities table
    view_meta: Dict[str, Any] = field(default_factory=dict) 

@dataclass
class RelationModel(DictInterfaceMixin):
    """The Global Edge. Represents a semantic connection between two Entities."""
    id: str
    source_id: str
    target_id: str
    relation_type: str = RelationType.BASIC.value
    evidence_ids: List[str] = field(default_factory=list) # IDs of Quotes/Sources that justify this relation
    properties: Dict[str, Any] = field(default_factory=dict) # custom variables (weight, confidence, label)
    state: Dict[str, Any] = field(default_factory=lambda: {"is_verified": True})

@dataclass
class ViewModel(DictInterfaceMixin):
    """Replaces WorkspaceModel. Represents a specific presentation of the graph."""
    id: str
    name: str
    view_type: str = ViewType.GRAPH.value # e.g., "view.graph", "view.timeline", "view.debate"
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

@dataclass
class EntityPayload(DictInterfaceMixin):
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None
    origin_id: Optional[str] = None
    view_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

@dataclass
class RelationPayload(DictInterfaceMixin):
    relation_id: Optional[str] = None
    relation_type: Optional[str] = None
    source_id: Optional[str] = None
    target_id: Optional[str] = None
    view_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ViewPayload(DictInterfaceMixin):
    view_id: Optional[str] = None
    view_type: Optional[str] = None
    name: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
