"""
gui/app_context.py

AppContext is the single typed facade between the GUI layer and PapyrusCore.
Plugins and docks should depend on AppContext, not MainWindow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

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
    from core.plugins.extension_registry import PluginExtensionRegistry
    from gui.registry.action_spec import ActionRegistry
    from gui.registry.context_menu_registry import ContextMenuRegistry
    
    from core.services.gui_bridge.ai_bootstrap_service import AIBootstrapService
    from core.services.ai.prompt_app_service import PromptAppService


@dataclass
class AppContext:
    """
    Typed facade exposing all headless services to the GUI layer.

    Constructed once in MainWindow from a PapyrusCore instance.  Docks and
    tabs should accept AppContext rather than MainWindow so they remain
    testable and plugin-compatible.

    GUI-only fields (ui_router, theme_manager, viewer) are set by MainWindow
    after construction and are None until then.
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
    workspace_annotation_service: Any = None
    workspace_ai_service: Any = None
    data_dock_service: Any = None
    data_provider_registry: Any = None
    media_asset_service: Any = None
    pdf_data_selection_service: Any = None
    pdf_grid_extraction_service: Any = None
    video_transcription_service: Any = None

    # Plugin extension specs collected at startup
    plugin_extension_registry: Optional["PluginExtensionRegistry"] = field(default=None)

    # GUI extensibility registries — created by MainWindow and available to all docks/plugins
    action_registry: Optional["ActionRegistry"] = field(default=None)
    context_menu_registry: Optional["ContextMenuRegistry"] = field(default=None)

    # Wired in by MainWindow after GUI is constructed
    ui_router: Optional[Any] = field(default=None)
    theme_manager: Optional[Any] = field(default=None)
    viewer: Optional[Any] = field(default=None)
    dialog_manager: Optional[Any] = field(default=None)
    # Central keybinding registry – set by MainWindow after KeybindingRegistry is created
    keybinding_registry: Optional[Any] = field(default=None)

    # Pack import/export service
    pack_service: Optional[Any] = field(default=None)

    # Help & Tutorial subsystem
    help_service: Optional[Any] = field(default=None)

    # AI backend registry and setup service
    llm_backend_registry: Optional[Any] = field(default=None)
    ai_setup_service: Optional[Any] = field(default=None)

    # Prompt management service — fully wired with blueprint_registry and step_manager
    prompt_app_service: Optional["PromptAppService"] = field(default=None)

    # Raw core reference — used by AI setup tab to access user_data_dir and registry
    core: Optional[Any] = field(default=None)

    # Dock spawning — set by MainWindow after DockManager is created
    dock_manager: Optional[Any] = field(default=None)

    # GUI bridge services — wired by MainWindow; need viewer + dock_manager + project_manager
    workspace_annotation_service: Optional[Any] = field(default=None)
    workspace_ai_service: Optional[Any] = field(default=None)

    # Deterministic extraction service
    deterministic_extractor_service: Optional[Any] = field(default=None)

    # Source evaluation service
    source_evaluation_service: Optional[Any] = field(default=None)

    # Currently selected AI model — updated by UnifiedResearchDock when model combo changes
    active_model: str = field(default="")

    # ------------------------------------------------------------------ helpers

    def get_active_ai_model(self) -> str:
        """Return the currently selected AI model, falling back to the first available."""
        if self.active_model:
            return self.active_model
        # Prefer the persisted default from AISetupService
        if self.ai_setup_service:
            try:
                default = self.ai_setup_service.get_default_chat_model()
                if default:
                    return default
            except Exception:
                pass
        try:
            models = self.llm_manager.get_available_models() or []
            return models[0] if models else ""
        except Exception:
            return ""

    def build_rag_context_payload(self) -> dict:
        """
        Package the standard RAG context variables that workflow blueprints expect.
        Consolidates the metadata assembly that was duplicated across BaseTab, ChatTab,
        and AnalysisTab.
        """
        if not self.project_manager:
            return {}

        from gui.utils.document_helpers import prune_doc_names

        def _metadata_json(key: str, default):
            raw = self.project_manager.get_metadata(key, json.dumps(default))
            if isinstance(raw, list):
                return raw
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else default
            except Exception:
                return default

        payload: dict = {
            "project_manifest": self.project_manager.get_metadata("project_manifest", "{}"),
            "active_rag_docs": prune_doc_names(self.project_manager, _metadata_json("active_rag_docs", [])),
            "active_rag_tags": _metadata_json("active_rag_tags", []),
            "active_rag_tag_logic": self.project_manager.get_metadata("active_rag_tag_logic", "OR"),
        }

        try:
            from core.api.workspace_ai import WorkspaceAIApi
            ws_data = self.project_manager.get_workspace_data()
            api = WorkspaceAIApi(self.project_manager)
            payload["workspace_data"] = api.build_ai_context(ws_data)
        except Exception:
            payload["workspace_data"] = "{}"

        try:
            settings = json.loads(self.project_manager.get_metadata("global_ai_settings", "{}"))
            payload["_global_ai_settings"] = settings
        except Exception:
            pass

        return payload

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
            data_dock_service=getattr(core, "data_dock_service", None),
            data_provider_registry=getattr(core, "data_provider_registry", None),
            media_asset_service=getattr(core, "media_asset_service", None),
            pdf_data_selection_service=getattr(core, "pdf_data_selection_service", None),
            pdf_grid_extraction_service=getattr(core, "pdf_grid_extraction_service", None),
            video_transcription_service=getattr(core, "video_transcription_service", None),
            plugin_extension_registry=getattr(core, "plugin_extension_registry", None),
            pack_service=getattr(core, "pack_service", None),
            help_service=getattr(core, "help_service", None),
            deterministic_extractor_service=getattr(core, "deterministic_extractor_service", None),
            source_evaluation_service=getattr(core, "source_evaluation_service", None),
            llm_backend_registry=getattr(core, "llm_backend_registry", None),
            ai_setup_service=getattr(core, "ai_setup_service", None),
            prompt_app_service=getattr(core, "prompt_app_service", None),
            core=core,
        )
