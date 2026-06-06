# core/models/workspace_models.py
from dataclasses import dataclass, field
from typing import List, Optional

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

    def __post_init__(self):
        # Ensure original_text defaults to note if not provided
        if not self.original_text:
            self.original_text = self.note
        if not self.node_type_id:
            self.node_type_id = "workspace.node.quote" if (self.quote or self.highlight_id or self.pdf_path) and not self.is_custom else "workspace.node.text"

@dataclass
class EdgeModel:
    id: str
    source: str
    target: str
    label: str
    color: str
    weight: int

@dataclass
class WorkspaceModel:
    workspace_id: int
    nodes: List[NodeModel] = field(default_factory=list)      # Nodes to Add/Update
    edges: List[EdgeModel] = field(default_factory=list)      # Edges to Add/Update
    deleted_node_ids: List[str] = field(default_factory=list) # Nodes to Delete
    deleted_edge_ids: List[str] = field(default_factory=list) # Edges to Delete
