from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from core.plugins.plugin_step_protocol import CustomExecutionStep


@dataclass
class WorkflowNodeType:
    id: str
    label: str
    category: str
    step_type: str
    description: str = ""
    default_inputs: Dict[str, Any] = field(default_factory=dict)
    default_ui_format: str = "silent"
    default_output_key: str = "result"
    output_ports: List[str] = field(default_factory=lambda: ["result"])
    input_ports: List[str] = field(default_factory=list)
    plugin_id: Optional[str] = None
    input_schema: Dict[str, Any] = field(default_factory=dict)   # {key: {type, label, required}}
    output_schema: Dict[str, Any] = field(default_factory=dict)  # {key: {type, label}}
    # Registry-dispatch: when set, _dispatch_step instantiates step_cls instead of
    # calling a legacy handler function.  None = flow-control native or legacy handler.
    step_cls: Optional["Type[CustomExecutionStep]"] = None


@dataclass
class WorkflowNode:
    id: str
    type_id: str
    label: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0


@dataclass
class WorkflowEdge:
    id: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str


@dataclass
class WorkflowGraph:
    id: str
    name: str
    description: str = ""
    expected_inputs: List[Dict[str, Any]] = field(default_factory=list)
    mount_points: List[str] = field(default_factory=lambda: ["custom_tools_tab"])
    active_contexts: List[str] = field(default_factory=list)
    nodes: List[WorkflowNode] = field(default_factory=list)
    edges: List[WorkflowEdge] = field(default_factory=list)
