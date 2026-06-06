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

from core.services.embedding_service import EmbeddingService
from core.services.workspace_services import WorkspaceService, WorkspaceGraphService
from core.services.project_app_service import ProjectAppService
from core.services.document_app_service import DocumentAppService
from core.services.tts_app_service import TTSAppService
from core.services.ocr_app_service import OCRAppService
from core.services.dictionary_app_service import DictionaryAppService
from core.services.citation_app_service import CitationAppService
from core.services.notes_app_service import NotesAppService
from core.services.tag_app_service import TagAppService
from core.services.prompt_app_service import PromptAppService

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    return os.path.join(root_dir, relative_path)

class PapyrusCore:
    """The headless engine of the application. Plugins interact with this, not the GUI."""
    def __init__(self, user_data_dir: str):
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

    def _ensure_default_dictionary(self):
        """Silently provisions the default dictionary on first launch."""
        if not self.dictionary_manager.get_available_dictionaries():
            default_dict_path = get_resource_path(os.path.join("assets", "default_english.json"))
            if os.path.exists(default_dict_path):
                print("[System] First launch detected. Building default dictionary...")
                self.dictionary_manager.import_json(default_dict_path, "Default English")