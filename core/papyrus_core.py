# core/papyrus_core.py
import os
import sys
from core.events.event_bus import EventBus
from core.engine.process_manager import ProcessRegistry
from core.project_manager import ProjectManager
from core.llm_manager import LocalLLMManager
from core.prompt_manager import PromptManager
from core.dictionary_manager import DictionaryManager
from core.citation_manager import CitationManager
from core.engine.step_manager import StepManager
from core.engine.blueprint_manager import BlueprintManager

from core.engine.registries import build_default_blueprint_registry, build_default_workflow_node_type_registry
from core.services.workspace_registries import build_default_workspace_ai_tool_registry, build_default_workspace_node_type_registry
from core.ontology.registry import OntologyRegistry

from core.services.project.embedding_service import EmbeddingService
from core.services.project.project_app_service import ProjectAppService
from core.services.document.document_app_service import DocumentAppService
from core.services.document.document_ingestion_service import DocumentIngestionService
from core.services.document.ocr_app_service import OCRAppService
from core.services.content.notes_app_service import NotesAppService
from core.services.content.tag_app_service import TagAppService
from core.services.content.essay_app_service import EssayAppService
from core.services.reference.citation_app_service import CitationAppService
from core.services.reference.dictionary_app_service import DictionaryAppService
from core.services.knowledge.ontology_service import OntologyService
from core.services.knowledge.index_app_service import IndexAppService
from core.services.workspace.workspace_service import WorkspaceService
from core.services.workspace.workspace_graph_service import WorkspaceGraphService
from core.services.workspace.workspace_layout_service import WorkspaceLayoutService
from core.services.ai.workflow_runner_service import WorkflowRunnerService
from core.services.ai.research_agent_service import ResearchAgentService
from core.services.ai.prompt_app_service import PromptAppService
from core.services.ai.tts_app_service import TTSAppService
from core.plugins.plugin_loader import load_plugins
from core.plugins.extension_registry import PluginExtensionRegistry

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    return os.path.join(root_dir, relative_path)

class PapyrusCore:
    """The headless engine of the application. Plugins interact with this, not the GUI."""
    def __init__(self, user_data_dir: str):
        self.user_data_dir = user_data_dir
        self.bus = EventBus.get_instance()
        self.process_registry = ProcessRegistry()
        
        # 1. Base Managers
        self.project_manager = ProjectManager()
        self.prompt_manager = PromptManager()
        self.step_manager = StepManager()
        
        self.llm_manager = LocalLLMManager()
        if hasattr(self.llm_manager, "prompt_manager"):
            self.prompt_manager = self.llm_manager.prompt_manager
        else:
            self.llm_manager.prompt_manager = self.prompt_manager
        self.llm_manager.set_audit_logger(self.project_manager.log_ai_interaction_threadsafe)
        
        self.dictionary_manager = DictionaryManager(user_data_dir)
        self._ensure_default_dictionary()
        self.citation_manager = CitationManager(self.project_manager)

        # 2. Registries
        self.blueprint_registry = build_default_blueprint_registry()
        self.blueprint_manager = BlueprintManager(self.blueprint_registry)
        self.workflow_node_type_registry = build_default_workflow_node_type_registry()
        self.workspace_ai_tools_registry = build_default_workspace_ai_tool_registry()
        self.workspace_node_type_registry = build_default_workspace_node_type_registry()
        self.ontology_registry = OntologyRegistry()

        # 3. Headless App Services
        self.embedding_service = EmbeddingService(self.llm_manager, self.project_manager)
        self.project_app_service = ProjectAppService(self.project_manager, self.llm_manager)
        self.document_app_service = DocumentAppService(self.project_manager)
        self.tts_app_service = TTSAppService()
        self.ocr_app_service = OCRAppService(self.project_manager)
        self.dictionary_app_service = DictionaryAppService(self.dictionary_manager)
        self.citation_app_service = CitationAppService(self.project_manager, self.citation_manager)
        self.notes_app_service = NotesAppService(self.project_manager)
        self.tag_app_service = TagAppService(self.project_manager)
        self.prompt_app_service = PromptAppService(
            self.prompt_manager,
            blueprint_manager=self.blueprint_manager,
            blueprint_registry=self.blueprint_registry,
            step_manager=self.step_manager,
        )
        self.workspace_service = WorkspaceService(self.project_manager, self.bus)
        self.workspace_graph_service = WorkspaceGraphService(self.bus)
        self.workspace_layout_service = WorkspaceLayoutService(self.project_manager, self.llm_manager, self.bus)
        self.ontology_service = OntologyService(self.project_manager, self.bus, self.ontology_registry)

        self.essay_app_service = EssayAppService(self.project_manager)
        self.index_app_service = IndexAppService(self.llm_manager, self.project_manager)
        self.document_ingestion_service = DocumentIngestionService(self.llm_manager)

        # 4. Workflow Services (ui_router and model_provider injected later by GUI layer)
        self.workflow_runner_service = WorkflowRunnerService(
            llm_manager=self.llm_manager,
            prompt_manager=self.prompt_manager,
            step_manager=self.step_manager,
            process_registry=self.process_registry,
            project_manager=self.project_manager,
            ontology_registry=self.ontology_registry,
            blueprint_manager=self.blueprint_manager,
            event_bus=self.bus,
        )
        self.research_agent_service = ResearchAgentService(
            self.project_manager,
            self.prompt_manager,
            self.blueprint_registry,
            workflow_executor=self.workflow_runner_service.prepare_runner,
            runner_starter=self.workflow_runner_service.start_runner,
        )

        # 5. Plugin extension registry (GUI extension points for plugins)
        self.plugin_extension_registry = PluginExtensionRegistry()
        # plugin_dock_specs consumed by MainWindow
        self.plugin_dock_specs = []
        # Storage for plugin instances + their API objects (populated by loader)
        self._loaded_plugins = []
        self._custom_services = {}

        # Discover and register external plugins
        load_plugins(self)

    def register_plugin(self, plugin, api=None) -> None:
        """Register a plugin's contributions (blueprints, tools, ontology, dock, GUI)."""
        plugin.register_blueprints(self.blueprint_registry)
        plugin.register_workspace_tools(self.workspace_ai_tools_registry)
        plugin.register_ontology_types(self.ontology_registry)
        dock_spec = plugin.get_dock_spec()
        if dock_spec is not None:
            self.plugin_dock_specs.append(dock_spec)
        if hasattr(plugin, "register_gui_extensions"):
            plugin.register_gui_extensions(self.plugin_extension_registry)

    def _unload_plugins(self) -> None:
        """Call on_unload on all plugins and clean up API subscriptions."""
        for plugin, api in reversed(self._loaded_plugins):
            try:
                if hasattr(plugin, "on_unload") and callable(plugin.on_unload):
                    plugin.on_unload()
            except Exception as exc:
                print(f"[PluginLoader] Error unloading '{plugin.plugin_id}': {exc}")
            finally:
                api._cleanup()
        self._loaded_plugins.clear()

    def _ensure_default_dictionary(self):
        """Silently provisions the default dictionary on first launch."""
        if not self.dictionary_manager.get_available_dictionaries():
            default_dict_path = get_resource_path(os.path.join("assets", "default_english.json"))
            if os.path.exists(default_dict_path):
                print("[System] First launch detected. Building default dictionary...")
                self.dictionary_manager.import_json(default_dict_path, "Default English")
