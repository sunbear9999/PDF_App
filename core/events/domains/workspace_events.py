from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from .base import BasePayload


class WorkspaceIntent(Enum):
    SYNC_TAGS_FROM_ANNOT = auto()
    UPDATE_HISTORY_BUTTONS = auto()
    EMBED_NODE_TEXT = auto()

    EDGE_TEXT_COMMITTED = auto()
    EDGE_EDIT_START = auto()
    EDGE_COLOR_REQUEST = auto()
    EDGE_WEIGHT_REQUEST = auto()
    EDGE_DELETE_REQUEST = auto()
    EDGE_DETAILS_REQUEST = auto()

    NODE_TEXT_COMMITTED = auto()
    NODE_EDIT_START = auto()
    NODE_VERIFY_TOGGLE = auto()
    TAG_FILTER_APPLY = auto()
    NODE_PRESSED = auto()
    NODE_JUMP_REQUEST = auto()
    NODE_CITATION_COPY = auto()
    NODE_CONNECT_START = auto()
    NODE_CHILDREN_TOGGLE = auto()
    NODE_COLOR_REQUEST = auto()
    NODE_FONT_REQUEST = auto()
    NODE_TAGS_MANAGE = auto()

    SELECTION_DELETE = auto()
    SELECTION_COLOR_REQUEST = auto()
    SELECTION_TAGS_MANAGE = auto()

    UNDO_CHECKPOINT_REQUESTED = auto()
    SAVE_UNDO_STATE = auto()
    SAVE_REDO_STATE = auto()
    UNDO_TRIGGERED = auto()
    REDO_TRIGGERED = auto()

    BOARD_ADD = auto()
    WORKSPACE_EXPORT = auto()
    FILTERS_RESET = auto()
    VIEW_RECENTER = auto()
    DECLUTTER_TRIGGERED = auto()
    CALCULATE_LAYOUT = auto()
    IMPORT_GRAPH = auto()


@dataclass
class WorkspacePayload(BasePayload):
    node_id: Optional[str] = None
    node_ids: Optional[List[str]] = None
    edge_id: Optional[str] = None
    text: Optional[str] = None
    tag_name: Optional[str] = None
    annot_id: Optional[str] = None
    model: Any = None
    can_undo: Optional[bool] = None
    can_redo: Optional[bool] = None
    target: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class WorkspaceEvent(Enum):
    LOADED = auto()
    CHANGED = auto()
    SAVED = auto()
    NODE_ADDED = auto()
    NODE_UPDATED = auto()
    NODE_DELETED = auto()
    EDGE_ADDED = auto()
    EDGE_UPDATED = auto()
    EDGE_DELETED = auto()
    SELECTION_CHANGED = auto()
    FILTER_CHANGED = auto()
    STATE_RESTORED = auto()
    RUN_AI_TOOL = auto()
    AI_GRAPH_GENERATED = auto()
    ACTIVE_MODEL_CHANGED = auto()
    LAYOUT_READY = auto()


@dataclass
class WorkspaceEventPayload(BasePayload):
    workspace_id: Optional[int] = None
    summary: Dict[str, Any] = field(default_factory=dict)
    model: Any = None
    node_model: Any = None
    edge_model: Any = None
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    changes: Dict[str, Any] = field(default_factory=dict)
    selected_ids: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)
    tool_id: Optional[str] = None
    result_text: Optional[str] = None
    model_name: Optional[str] = None
