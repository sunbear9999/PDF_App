# core/events/domains/ontology_events.py
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Dict, Any, List
from core.events.domains.base import BasePayload

class EntityIntent(Enum):
    ADD = auto()
    UPDATE_PROPERTIES = auto()
    UPDATE_STATE = auto()
    UPDATE_VIEW_META = auto()
    CHANGE_TYPE = auto()
    DELETE_FROM_VIEW = auto()
    PURGE_GLOBALLY = auto()

@dataclass
class EntityPayload(BasePayload):
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None
    origin_id: Optional[str] = None
    view_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

class RelationIntent(Enum):
    ADD = auto()
    UPDATE = auto()
    DELETE = auto()

@dataclass
class RelationPayload(BasePayload):
    relation_id: Optional[str] = None
    relation_type: Optional[str] = None
    source_id: Optional[str] = None
    target_id: Optional[str] = None
    view_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None