from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional

from .base import BasePayload


class DataDockIntent(Enum):
    OPEN = auto()
    LOAD_SELECTION = auto()
    NEW_DATASET = auto()
    LOAD_DATASET = auto()
    SAVE_DATASET = auto()
    SAVE_DATASET_AS = auto()
    RENAME_DATASET = auto()
    DELETE_DATASET = auto()
    CLEAR_ACTIVE_GRID = auto()
    GENERATE_CHART = auto()
    CREATE_DATA_NODE = auto()
    CREATE_CHART_NODE = auto()
    GRID_UPDATED = auto()
    RENAME_HEADERS = auto()
    APPLY_CLEANER = auto()
    EXPORT_CHART = auto()
    JUMP_TO_PROVENANCE = auto()
    SELECT_PDF_REGION = auto()
    CANCEL_PDF_REGION_SELECTION = auto()


@dataclass
class DataDockPayload(BasePayload):
    dataset_id: Optional[str] = None
    chart_id: Optional[str] = None
    name: Optional[str] = None
    selection: Any = None
    dataset_state: Any = None
    chart_config: Any = None
    target_workspace_id: Optional[int] = None
    node_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    request_id: Optional[str] = None


class DataDockEvent(Enum):
    DATASET_LOADED = auto()
    DATASET_SAVED = auto()
    DATASET_DELETED = auto()
    DATASET_CHANGED = auto()
    LIBRARY_CHANGED = auto()
    CHART_CONFIGURED = auto()
    DATASET_RENAMED = auto()
    GRID_SELECTION_CHANGED = auto()
    CLEANER_APPLIED = auto()
    CHART_EXPORTED = auto()
    PROVENANCE_JUMP_REQUESTED = auto()
    CHART_ANALYSIS_STARTED = auto()
    CHART_ANALYSIS_COMPLETED = auto()
    CHART_ANALYSIS_FAILED = auto()
    RAW_CHART_NODE_CREATED = auto()


@dataclass
class DataDockEventPayload(BasePayload):
    dataset_id: Optional[str] = None
    chart_id: Optional[str] = None
    node_id: Optional[str] = None
    dataset_state: Any = None
    chart_config: Any = None
    library: Any = None
    changes: Dict[str, Any] = field(default_factory=dict)
