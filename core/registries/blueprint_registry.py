from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.engine.action_model import AIActionBlueprint
from core.engine.default_blueprints import DefaultBlueprints


def _get_extract_claims():
    from core.engine.blueprints.extract_claims import get_extract_claims_blueprint
    return get_extract_claims_blueprint()


BlueprintFactory = Callable[..., AIActionBlueprint]


@dataclass
class BlueprintDefinition:
    id: str
    label: str
    description: str
    factory: BlueprintFactory
    mount_points: List[str] = field(default_factory=list)
    plugin_id: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    required_inputs: List[Dict[str, Any]] = field(default_factory=list)
    produced_outputs: List[str] = field(default_factory=list)
    side_effects: List[str] = field(default_factory=list)
    human_checkpoints: List[str] = field(default_factory=list)
    agent_visible: bool = True

    def create(self, *args, **kwargs) -> AIActionBlueprint:
        blueprint = self.factory(*args, **kwargs)
        blueprint.name = blueprint.name or self.label
        copied = copy.deepcopy(blueprint)
        setattr(copied, "_registry_id", self.id)
        setattr(copied, "_registry_label", self.label)
        setattr(copied, "_plugin_id", self.plugin_id)
        return copied

    def as_agent_tool(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "required_inputs": copy.deepcopy(self.required_inputs),
            "produced_outputs": list(self.produced_outputs),
            "side_effects": list(self.side_effects),
            "human_checkpoints": list(self.human_checkpoints),
            "mount_points": list(self.mount_points),
            "plugin_id": self.plugin_id,
        }


class BlueprintRegistry:
    def __init__(self):
        self._definitions: Dict[str, BlueprintDefinition] = {}

    def register(self, definition: BlueprintDefinition):
        self._definitions[definition.id] = definition

    def unregister(self, blueprint_id: str):
        self._definitions.pop(blueprint_id, None)

    def remove_by_plugin(self, plugin_id: str) -> None:
        self._definitions = {bid: d for bid, d in self._definitions.items()
                             if d.plugin_id != plugin_id}

    def get(self, blueprint_id: str) -> Optional[BlueprintDefinition]:
        return self._definitions.get(blueprint_id)

    def create(self, blueprint_id: str, *args, **kwargs) -> Optional[AIActionBlueprint]:
        definition = self.get(blueprint_id)
        return definition.create(*args, **kwargs) if definition else None

    def iter_mount(self, mount_point: str) -> Iterable[BlueprintDefinition]:
        for definition in self._definitions.values():
            if mount_point in definition.mount_points:
                yield definition

    def all(self) -> Iterable[BlueprintDefinition]:
        return self._definitions.values()

    def agent_tools(self) -> List[Dict[str, Any]]:
        return [d.as_agent_tool() for d in self._definitions.values() if d.agent_visible]


def build_default_blueprint_registry() -> BlueprintRegistry:
    registry = BlueprintRegistry()
    defaults = [
        BlueprintDefinition("Chat - Universal Agent", "Chat - Universal Agent", "Universal document chat workflow.", lambda pm=None, **_: DefaultBlueprints.get_universal_chat_blueprint(pm), ["chat_dock", "notes_dock", "data_dock"], capabilities=["answer_with_rag", "summarize_documents", "extract_inline_citations"], produced_outputs=["answer", "citation_cards"]),
        BlueprintDefinition("Search Terms", "Search Terms", "Generate reusable search terms.", lambda pm=None, model="{selected_model}", **_: DefaultBlueprints.get_search_terms_blueprint(pm, model=model), ["search_tab", "notes_dock", "data_dock"], capabilities=["generate_search_queries", "source_discovery"], required_inputs=[{"key": "goal", "type": "text", "label": "Research goal"}], produced_outputs=["search_terms"]),
        BlueprintDefinition("Organize Workspace", "Organize Workspace", "Organize selected workspace nodes.", DefaultBlueprints.get_workspace_organize_blueprint, ["workspace"], capabilities=["organize_workspace"], side_effects=["workspace_update"]),
        BlueprintDefinition("Group Selected Nodes", "Group Selected Nodes", "Create group nodes for selected evidence.", DefaultBlueprints.get_workspace_group_blueprint, ["workspace"], capabilities=["group_evidence"], side_effects=["workspace_update"]),
        BlueprintDefinition("Find Workspace Connections", "Find Workspace Connections", "Find useful links between selected nodes.", DefaultBlueprints.get_workspace_connections_blueprint, ["workspace"], capabilities=["connect_evidence"], side_effects=["workspace_update"]),
        BlueprintDefinition("Generate Workspace Outline", "Generate Workspace Outline", "Generate an outline from workspace nodes.", DefaultBlueprints.get_workspace_outline_blueprint, ["workspace"], capabilities=["outline_from_workspace"], produced_outputs=["outline"]),
        BlueprintDefinition("Identify Workspace Weakpoints", "Identify Workspace Weakpoints", "Identify weak arguments or missing support.", DefaultBlueprints.get_workspace_weakpoints_blueprint, ["workspace"], capabilities=["gap_analysis", "argument_review"], produced_outputs=["weakpoints"]),
        BlueprintDefinition("Fill Workspace Graph", "Fill Workspace Graph", "Suggest missing workspace ideas and links.", DefaultBlueprints.get_workspace_fill_blueprint, ["workspace"], capabilities=["suggest_missing_evidence", "expand_workspace"], side_effects=["workspace_update"]),
        BlueprintDefinition("Consolidate Nodes", "Consolidate Nodes", "Restructure selected workspace nodes.", DefaultBlueprints.get_workspace_consolidate_blueprint, ["workspace"], capabilities=["consolidate_evidence"], side_effects=["workspace_update"], human_checkpoints=["review_consolidation_plan"]),
        BlueprintDefinition("Brainstorming", "Brainstorming", "Strategy and brainstorming workflow.", lambda pm=None, prompt_key="Brainstorm System - Default", **_: DefaultBlueprints.get_brainstorm_blueprint(pm, prompt_key), ["brainstorm_dock", "notes_dock", "essay_dock"], capabilities=["brainstorm_topics", "refine_research_direction"], required_inputs=[{"key": "query", "type": "text", "label": "Prompt"}], produced_outputs=["ideas"]),
        BlueprintDefinition("Document Analysis", "Document Analysis", "Analyze document chunks with a template.", lambda pm=None, chunks=None, **_: DefaultBlueprints.get_analysis_blueprint(pm, chunks or []), ["analysis_tab", "data_dock"], capabilities=["analyze_documents"], required_inputs=[{"key": "chunks", "type": "list", "label": "Document chunks"}], produced_outputs=["document_analysis"]),
        BlueprintDefinition("Compare Outlines", "Compare Outlines", "Compare two document outlines.", DefaultBlueprints.get_compare_outlines_blueprint, ["analysis_tab", "essay_dock"], capabilities=["compare_sources", "compare_outlines"], produced_outputs=["comparison"]),
        BlueprintDefinition("Master Project Outline", "Master Project Outline", "Generate a master outline for a document/project.", lambda pm=None, doc_name="Project", **_: DefaultBlueprints.get_master_outline_blueprint(pm, doc_name), ["analysis_tab", "essay_dock", "notes_dock"], capabilities=["synthesize_outline", "project_outline"], produced_outputs=["outline"]),
        BlueprintDefinition("Blueprint Architect", "Blueprint Architect", "AI helper for building workflow blueprints.", DefaultBlueprints.get_blueprint_architect, ["custom_tools_tab"], capabilities=["build_workflow_blueprint"], produced_outputs=["blueprint_json"], agent_visible=False),
        BlueprintDefinition("Research Agent Planner", "Research Agent Planner", "Plan the next human-in-the-loop research agent action.", lambda pm=None, **_: DefaultBlueprints.get_research_agent_planner_blueprint(pm), ["research_agent"], capabilities=["plan_research_agent_next_action"], produced_outputs=["agent_plan"], agent_visible=False),
        BlueprintDefinition("Keyword Density Analyzer (Python)", "Keyword Density Analyzer (Python)", "Example workflow using RAG plus Python transform.", DefaultBlueprints.get_python_example_blueprint, ["custom_tools_tab", "data_dock"], capabilities=["analyze_keyword_density"], required_inputs=[{"key": "keyword", "type": "text", "label": "Target keyword"}], produced_outputs=["keyword_density"]),
        BlueprintDefinition("Blank Workflow", "Blank Workflow", "Starter workflow for custom tools.", lambda name="Blank Workflow", **_: DefaultBlueprints.get_blank_custom_tool(name), ["custom_tools_tab"], agent_visible=False),
        BlueprintDefinition(
            "Extract Claims", "Extract Claims",
            "Highlight text in the PDF viewer, extract structured claims with AI, review them, and save selected claims as workspace nodes.",
            lambda **_: _get_extract_claims(),
            ["source_viewer:context_menu:text_selection", "custom_tools_tab"],
            capabilities=["extract_claims", "workspace_write"],
            required_inputs=[{"key": "selected_text", "type": "text_area", "label": "Selected Text"}],
            produced_outputs=["claim nodes"],
            human_checkpoints=["select_claims"],
        ),
    ]
    for definition in defaults:
        registry.register(definition)
    return registry
