# core/models/workspace_models.py
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class NodeModel:
    id: str
    quote: str
    note: str
    color: str
    is_custom: bool
    x: float
    y: float
    width: float
    height: float
    highlight_id: Optional[str] = None
    workspace_id: int = 1
    pdf_path: Optional[str] = None
    page_num: Optional[int] = None
    manual_font_size: Optional[int] = None
    node_origin: str = "human"
    is_verified: int = 0
    original_text: str = ""
    node_type_id: str = ""
    entity_type: str = ""
    source_id: Optional[str] = None
    entity_properties: Dict[str, Any] = field(default_factory=dict)
    entity_state: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Ensure original_text defaults to note if not provided
        if not self.original_text:
            self.original_text = self.note
        if not self.node_type_id:
            self.node_type_id = "workspace.node.quote" if (self.quote or self.highlight_id or self.pdf_path) and not self.is_custom else "workspace.node.text"
        if not self.entity_type:
            self.entity_type = "entity.quote" if self.node_type_id == "workspace.node.quote" else "entity.text"
        if not self.entity_properties:
            self.entity_properties = {
                "quote": self.quote,
                "exact_text": self.quote,
                "text": self.quote if self.entity_type in {"entity.quote", "entity.evidence"} else self.note,
                "note_text": self.note,
                "pdf_path": self.pdf_path,
                "page_num": self.page_num,
                "highlight_id": self.highlight_id,
                "source_id": self.source_id,
            }
        if not self.entity_state:
            self.entity_state = {
                "is_verified": bool(self.is_verified),
                "ai_generated": self.node_origin == "ai",
                "origin": self.node_origin,
            }

@dataclass
class EdgeModel:
    id: str
    source: str
    target: str
    label: str
    color: str
    weight: int
    relation_type: str = "relation.basic"
    evidence_ids: List[str] = field(default_factory=list)
    relation_properties: Dict[str, Any] = field(default_factory=dict)
    relation_state: Dict[str, Any] = field(default_factory=dict)

@dataclass
class WorkspaceModel:
    workspace_id: int
    nodes: List[NodeModel] = field(default_factory=list)      # Nodes to Add/Update
    edges: List[EdgeModel] = field(default_factory=list)      # Edges to Add/Update
    deleted_node_ids: List[str] = field(default_factory=list) # Nodes to Delete
    deleted_edge_ids: List[str] = field(default_factory=list) # Edges to Delete
