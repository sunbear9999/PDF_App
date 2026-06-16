"""
gui/app_context.py

AppContext is the single typed facade between the GUI layer and PapyrusCore.
Plugins and docks should depend on AppContext, not MainWindow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from core.papyrus_core import PapyrusCore
    from core.events.event_bus import EventBus
    from core.project_manager import ProjectManager
    from core.llm_manager import LocalLLMManager
    from core.prompt_manager import PromptManager
    from core.dictionary_manager import DictionaryManager
    from core.citation_manager import CitationManager
    from core.engine.step_manager import StepManager
    from core.engine.blueprint_manager import BlueprintManager
    from core.engine.process_manager import ProcessRegistry
    from core.registries import (
        BlueprintRegistry,
        BlueprintNodeTypeRegistry,
        WorkspaceAIToolRegistry,
        WorkspaceNodeTypeRegistry,
        OntologyRegistry,
    )
    from core.services.workspace_services import WorkspaceService, WorkspaceGraphService
    from core.services.workflow_runner_service import WorkflowRunnerService
    from core.services.research_agent_service import ResearchAgentService


@dataclass
class AppContext:
    """
    Typed facade exposing all headless services to the GUI layer.

    Constructed once in MainWindow from a PapyrusCore instance.  Docks and
    tabs should accept AppContext rather than MainWindow so they remain
    testable and plugin-compatible.
    """

    bus: "EventBus"
    project_manager: "ProjectManager"
    llm_manager: "LocalLLMManager"
    prompt_manager: "PromptManager"
    step_manager: "StepManager"
    blueprint_registry: "BlueprintRegistry"
    blueprint_manager: "BlueprintManager"
    workflow_node_type_registry: "BlueprintNodeTypeRegistry"
    workspace_ai_tools_registry: "WorkspaceAIToolRegistry"
    workspace_node_type_registry: "WorkspaceNodeTypeRegistry"
    ontology_registry: "OntologyRegistry"
    dictionary_manager: "DictionaryManager"
    citation_manager: "CitationManager"
    process_registry: "ProcessRegistry"
    workspace_service: "WorkspaceService"
    workspace_graph_service: "WorkspaceGraphService"
    workflow_runner_service: "WorkflowRunnerService"
    research_agent_service: "ResearchAgentService"

    @classmethod
    def from_core(cls, core: "PapyrusCore") -> "AppContext":
        return cls(
            bus=core.bus,
            project_manager=core.project_manager,
            llm_manager=core.llm_manager,
            prompt_manager=core.prompt_manager,
            step_manager=core.step_manager,
            blueprint_registry=core.blueprint_registry,
            blueprint_manager=core.blueprint_manager,
            workflow_node_type_registry=core.workflow_node_type_registry,
            workspace_ai_tools_registry=core.workspace_ai_tools_registry,
            workspace_node_type_registry=core.workspace_node_type_registry,
            ontology_registry=core.ontology_registry,
            dictionary_manager=core.dictionary_manager,
            citation_manager=core.citation_manager,
            process_registry=core.process_registry,
            workspace_service=core.workspace_service,
            workspace_graph_service=core.workspace_graph_service,
            workflow_runner_service=core.workflow_runner_service,
            research_agent_service=core.research_agent_service,
        )
