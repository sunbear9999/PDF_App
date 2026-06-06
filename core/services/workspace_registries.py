# core/services/workspace_registries.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional
from core.engine.action_model import AIActionBlueprint
from core.engine.default_blueprints import DefaultBlueprints
from core.events.event_bus import EventBus
from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload

@dataclass
class WorkspaceActionDefinition:
    id: str
    label: str
    intent_name: WorkspaceIntent
    payload_factory: Callable[..., WorkspacePayload] = field(default_factory=lambda: (lambda *args, **kwargs: WorkspacePayload()))
    contexts: List[str] = field(default_factory=list)
    shortcut: Optional[str] = None
    enabled_predicate: Optional[Callable] = None

    def execute(self, *args, **kwargs):
        bus = EventBus.get_instance()
        payload = self.payload_factory(*args, **kwargs)
        if isinstance(payload, dict):
            payload = WorkspacePayload(**payload)
        bus.workspace_action_requested.emit(self.intent_name, payload)

    def is_enabled(self, context=None) -> bool:
        if not self.enabled_predicate:
            return True
        try:
            return bool(self.enabled_predicate(context))
        except Exception:
            return False

class WorkspaceActionRegistry:
    def __init__(self):
        self._actions: Dict[str, WorkspaceActionDefinition] = {}

    def register(self, definition: WorkspaceActionDefinition):
        self._actions[definition.id] = definition

    def get(self, action_id: str) -> Optional[WorkspaceActionDefinition]:
        return self._actions.get(action_id)

    def iter_context(self, context: str) -> Iterable[WorkspaceActionDefinition]:
        for action in self._actions.values():
            if context in action.contexts:
                yield action

@dataclass
class WorkspaceNodeTypeDefinition:
    id: str
    label: str
    description: str
    base_type_id: Optional[str] = None
    action_ids: List[str] = field(default_factory=list)
    display_factory: Optional[Callable] = None
    plugin_id: Optional[str] = None

    def inherits_from(self, type_id: str) -> bool:
        return self.id == type_id or self.base_type_id == type_id

    def build_display_text(self, node, expanded: bool = False) -> str:
        if self.display_factory:
            return self.display_factory(node, expanded)
        return getattr(node, "note", "") or getattr(node, "quote", "") or ""

class WorkspaceNodeTypeRegistry:
    def __init__(self):
        self._types: Dict[str, WorkspaceNodeTypeDefinition] = {}

    def register(self, definition: WorkspaceNodeTypeDefinition):
        self._types[definition.id] = definition

    def get(self, type_id: str) -> Optional[WorkspaceNodeTypeDefinition]:
        return self._types.get(type_id)

    def all(self) -> Iterable[WorkspaceNodeTypeDefinition]:
        return self._types.values()

    def resolve(self, node) -> WorkspaceNodeTypeDefinition:
        type_id = getattr(node, "node_type_id", None) or infer_workspace_node_type_id(node)
        return self.get(type_id) or self.get("workspace.node.text") or WorkspaceNodeTypeDefinition(
            id="workspace.node.text",
            label="Text Node",
            description="Basic workspace text node.",
            action_ids=["node.edit", "node.color", "node.font_size", "node.connect"],
        )

def infer_workspace_node_type_id(node) -> str:
    is_custom = bool(getattr(node, "is_custom", False))
    has_source = bool(getattr(node, "pdf_path", None) or getattr(node, "highlight_id", None) or getattr(node, "quote", ""))
    return "workspace.node.quote" if has_source and not is_custom else "workspace.node.text"

def build_text_node_display(node, expanded: bool = False) -> str:
    return getattr(node, "note", "") or ""

def build_quote_node_display(node, expanded: bool = False) -> str:
    note = getattr(node, "note", "") or ""
    quote = getattr(node, "quote", "") or ""
    if note and quote and quote != note:
        return f'{note}\n\n"{quote}"'
    if note:
        return note
    return f'"{quote}"' if quote else ""

def build_default_workspace_node_type_registry() -> WorkspaceNodeTypeRegistry:
    registry = WorkspaceNodeTypeRegistry()
    base_actions = ["node.edit", "node.color", "node.font_size", "node.connect"]
    registry.register(WorkspaceNodeTypeDefinition(
        id="workspace.node.text",
        label="Text Node",
        description="A basic workspace idea or note node.",
        action_ids=base_actions,
        display_factory=build_text_node_display,
    ))
    registry.register(WorkspaceNodeTypeDefinition(
        id="workspace.node.quote",
        label="Quote Node",
        description="A source-backed quote/evidence node with PDF navigation.",
        base_type_id="workspace.node.text",
        action_ids=[*base_actions, "node.jump", "node.copy_citation"],
        display_factory=build_quote_node_display,
    ))
    return registry

@dataclass
class WorkspaceAIToolDefinition:
    id: str
    label: str
    description: str
    blueprint_key: str
    fallback_factory: Callable[..., AIActionBlueprint]
    requires_selection: bool = True
    mount_points: List[str] = field(default_factory=lambda: ["workspace_toolbar", "workspace_context_menu"])
    workspace_filters: Optional[List[str]] = None
    review_before_apply: bool = True

    def resolve_filters(self, blueprint: AIActionBlueprint) -> List[str]:
        if self.workspace_filters is not None:
            return list(self.workspace_filters)
        if blueprint.steps:
            return list(getattr(blueprint.steps[0], "permissions", []) or ["all"])
        return ["all"]

class WorkspaceAIToolRegistry:
    def __init__(self):
        self._tools: Dict[str, WorkspaceAIToolDefinition] = {}

    def register(self, definition: WorkspaceAIToolDefinition):
        self._tools[definition.id] = definition

    def get(self, tool_id: str) -> Optional[WorkspaceAIToolDefinition]:
        return self._tools.get(tool_id)

    def iter_mount(self, mount_point: str) -> Iterable[WorkspaceAIToolDefinition]:
        for tool in self._tools.values():
            if mount_point in tool.mount_points:
                yield tool

def build_default_workspace_ai_tool_registry() -> WorkspaceAIToolRegistry:
    registry = WorkspaceAIToolRegistry()
    defaults = [
        WorkspaceAIToolDefinition(
            id="workspace.ai.organize",
            label="✨ Organize Selected Nodes",
            description="Reposition selected workspace nodes.",
            blueprint_key="Organize Workspace",
            fallback_factory=DefaultBlueprints.get_workspace_organize_blueprint,
            requires_selection=True,
            workspace_filters=["text", "layout", "edges", "color"],
        ),
        WorkspaceAIToolDefinition(
            id="workspace.ai.group",
            label="🗂️ Group Selected Nodes",
            description="Create group nodes and connect selected notes to them.",
            blueprint_key="Group Selected Nodes",
            fallback_factory=DefaultBlueprints.get_workspace_group_blueprint,
            requires_selection=True,
            workspace_filters=["text", "layout", "edges", "color"],
        ),
        WorkspaceAIToolDefinition(
            id="workspace.ai.connections",
            label="🔗 Find New Connections",
            description="Find useful links between selected nodes.",
            blueprint_key="Find Workspace Connections",
            fallback_factory=DefaultBlueprints.get_workspace_connections_blueprint,
            requires_selection=True,
            workspace_filters=["text", "layout", "edges", "doc_meta"],
        ),
        WorkspaceAIToolDefinition(
            id="workspace.ai.outline",
            label="📝 Generate Outline",
            description="Generate an outline from selected nodes.",
            blueprint_key="Generate Workspace Outline",
            fallback_factory=DefaultBlueprints.get_workspace_outline_blueprint,
            requires_selection=True,
            workspace_filters=["text", "edges", "doc_meta"],
        ),
        WorkspaceAIToolDefinition(
            id="workspace.ai.weakpoints",
            label="🔍 Identify Weakpoints",
            description="Find weak spots in selected workspace arguments.",
            blueprint_key="Identify Workspace Weakpoints",
            fallback_factory=DefaultBlueprints.get_workspace_weakpoints_blueprint,
            requires_selection=True,
            workspace_filters=["text", "edges", "doc_meta"],
        ),
        WorkspaceAIToolDefinition(
            id="workspace.ai.fill_graph",
            label="🕸️ Fill Out Graph",
            description="Add missing graph ideas and links.",
            blueprint_key="Fill Workspace Graph",
            fallback_factory=DefaultBlueprints.get_workspace_fill_blueprint,
            requires_selection=True,
            workspace_filters=["all"],
        ),
        WorkspaceAIToolDefinition(
            id="workspace.ai.consolidate",
            label="🏗️ Consolidate Notes",
            description="Consolidate selected workspace nodes.",
            blueprint_key="Consolidate Nodes",
            fallback_factory=DefaultBlueprints.get_workspace_consolidate_blueprint,
            requires_selection=True,
            workspace_filters=["all"],
        ),
    ]
    for definition in defaults:
        registry.register(definition)
    return registry

def build_default_workspace_action_registry() -> WorkspaceActionRegistry:
    registry = WorkspaceActionRegistry()
    
    actions_setup = [
        ("workspace.add_board", "➕ Board", WorkspaceIntent.BOARD_ADD, lambda: WorkspacePayload(), ["toolbar"], None),
        ("workspace.export", "📸 Export", WorkspaceIntent.WORKSPACE_EXPORT, lambda: WorkspacePayload(), ["toolbar", "canvas"], None),
        ("workspace.reset_filters", "⚠️ Clear", WorkspaceIntent.FILTERS_RESET, lambda: WorkspacePayload(), ["toolbar"], "Ctrl+W"),
        ("workspace.recenter", "🎯 Center", WorkspaceIntent.VIEW_RECENTER, lambda: WorkspacePayload(), ["toolbar"], None),
        ("workspace.undo", "↩️", WorkspaceIntent.UNDO_TRIGGERED, lambda: WorkspacePayload(), ["toolbar"], "Ctrl+Z"),
        ("workspace.redo", "↪️", WorkspaceIntent.REDO_TRIGGERED, lambda: WorkspacePayload(), ["toolbar"], "Ctrl+Y"),
        ("workspace.declutter", "🧹 Declutter", WorkspaceIntent.DECLUTTER_TRIGGERED, lambda node=None: WorkspacePayload(target=node.node_id if node else None), ["toolbar", "canvas", "selection", "node"], "Ctrl+D"),
        
        ("node.edit", "✏️ Edit Note Text", WorkspaceIntent.NODE_EDIT_START, lambda node: WorkspacePayload(node_id=node.node_id), ["node"], None),
        ("node.color", "🎨 Change Color", WorkspaceIntent.NODE_COLOR_REQUEST, lambda node: WorkspacePayload(node_ids=[node.node_id]), ["node"], None),
        ("node.font_size", "📏 Change Font Size", WorkspaceIntent.NODE_FONT_REQUEST, lambda node: WorkspacePayload(node_id=node.node_id), ["node"], None),
        ("node.verify", "⚠️ Verify AI", WorkspaceIntent.NODE_VERIFY_TOGGLE, lambda node: WorkspacePayload(node_id=node.node_id), ["node"], None),
        ("node.jump", "📄 Jump to PDF", WorkspaceIntent.NODE_JUMP_REQUEST, lambda node: WorkspacePayload(node_id=node.node_id), ["node"], None),
        ("node.connect", "🔗 Connect", WorkspaceIntent.NODE_CONNECT_START, lambda node: WorkspacePayload(node_id=node.node_id), ["node"], None),
        ("node.manage_tags", "🏷️ Manage Tags", WorkspaceIntent.NODE_TAGS_MANAGE, lambda node: WorkspacePayload(node_ids=[node.node_id]), ["node"], None),
        ("node.copy_citation", "📋 Copy In-Text Citation", WorkspaceIntent.NODE_CITATION_COPY, lambda node: WorkspacePayload(node_id=node.node_id), ["node"], None),
        
        ("edge.edit", "✏️ Edit Connection Text", WorkspaceIntent.EDGE_EDIT_START, lambda edge: WorkspacePayload(edge_id=edge.edge_id), ["edge"], None),
        ("edge.color", "🎨 Change Line Color", WorkspaceIntent.EDGE_COLOR_REQUEST, lambda edge: WorkspacePayload(edge_id=edge.edge_id), ["edge"], None),
        ("edge.weight", "📏 Change Line Weight", WorkspaceIntent.EDGE_WEIGHT_REQUEST, lambda edge: WorkspacePayload(edge_id=edge.edge_id), ["edge"], None),
        ("edge.delete", "🗑️ Delete Connection", WorkspaceIntent.EDGE_DELETE_REQUEST, lambda edge: WorkspacePayload(edge_id=edge.edge_id), ["edge"], None),
        
        ("selection.delete", "🗑️ Remove Selected", WorkspaceIntent.SELECTION_DELETE, lambda: WorkspacePayload(), ["selection"], None),
        ("selection.color", "🎨 Change Selected Color", WorkspaceIntent.SELECTION_COLOR_REQUEST, lambda: WorkspacePayload(), ["selection"], None),
        ("selection.manage_tags", "🏷️ Manage Selected Tags", WorkspaceIntent.SELECTION_TAGS_MANAGE, lambda: WorkspacePayload(), ["selection"], None),
    ]
    
    for action_id, label, intent, p_factory, contexts, shortcut in actions_setup:
        registry.register(WorkspaceActionDefinition(action_id, label, intent, p_factory, contexts, shortcut))
        
    return registry
