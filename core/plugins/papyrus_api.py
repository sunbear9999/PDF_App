"""
core/plugins/papyrus_api.py

The controlled surface that plugins receive instead of raw PapyrusCore.
Plugins access services, registries, and events through this facade.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from core.papyrus_core import PapyrusCore
    from core.events.event_bus import EventBus
    from core.project_manager import ProjectManager
    from core.llm_manager import LocalLLMManager
    from core.plugins.plugin_config import PluginConfig
    from core.registries import (
        BlueprintRegistry,
        WorkspaceAIToolRegistry,
        OntologyRegistry,
        BlueprintNodeTypeRegistry,
        WorkspaceNodeTypeRegistry,
    )
    from core.plugins.extension_registry import PluginExtensionRegistry
    from core.services.ai.workflow_runner_service import WorkflowRunnerService
    from core.services.ai.research_agent_service import ResearchAgentService
    from core.services.ai.prompt_app_service import PromptAppService
    from core.services.reference.citation_app_service import CitationAppService


class PluginDependencyError(RuntimeError):
    """Raised when a required service from another plugin is not available."""
    pass


class PapyrusAPI:
    """
    Typed facade exposing the backend to plugins.

    Plugins receive one instance of this per plugin, constructed by the loader.
    It gives access to services, registries, and events without exposing
    private internals of PapyrusCore.

    Usage in a plugin::

        def on_load(self, api: PapyrusAPI) -> None:
            api.subscribe("project_loaded", self._on_project_loaded)
            api.register_service("myplugin.service", MyService())
            api.blueprints.register(...)
    """

    def __init__(self, core: "PapyrusCore", plugin_id: str = "") -> None:
        self._core = core
        self._plugin_id = plugin_id
        # Tracks (signal, slot) pairs so we can disconnect on unload
        self._subscriptions: List[Tuple[Any, Callable]] = []
        # Per-plugin persistent config (lazy import to avoid circular at module level)
        from core.plugins.plugin_config import PluginConfig
        self._config = PluginConfig(plugin_id or "_unknown", core.user_data_dir)

    # ----------------------------------------------------------------
    # Section A: Typed service access
    # ----------------------------------------------------------------

    @property
    def event_bus(self) -> "EventBus":
        """The application event bus."""
        return self._core.bus

    @property
    def project_manager(self) -> "ProjectManager":
        """Project state, PDF list, and database access."""
        return self._core.project_manager

    @property
    def llm(self) -> "LocalLLMManager":
        """Local LLM manager (Ollama / ChromaDB embeddings)."""
        return self._core.llm_manager

    @property
    def workflow_runner(self) -> "WorkflowRunnerService":
        """Run AI blueprints programmatically."""
        return self._core.workflow_runner_service

    @property
    def research_agent(self) -> "ResearchAgentService":
        """Hook into the autonomous research loop."""
        return self._core.research_agent_service

    @property
    def prompts(self) -> "PromptAppService":
        """Read and write the prompt catalog."""
        return self._core.prompt_app_service

    # ----------------------------------------------------------------
    # Section B: Registry access
    # ----------------------------------------------------------------

    @property
    def blueprints(self) -> "BlueprintRegistry":
        """Register custom AI action blueprints."""
        return self._core.blueprint_registry

    @property
    def workspace_tools(self) -> "WorkspaceAIToolRegistry":
        """Register workspace context-menu AI tools."""
        return self._core.workspace_ai_tools_registry

    @property
    def ontology(self) -> "OntologyRegistry":
        """Register custom entity and relation types."""
        return self._core.ontology_registry

    @property
    def workspace_node_types(self) -> "WorkspaceNodeTypeRegistry":
        """Register custom workspace node visual types."""
        return self._core.workspace_node_type_registry

    @property
    def workflow_node_types(self) -> "BlueprintNodeTypeRegistry":
        """Register custom workflow step types."""
        return self._core.workflow_node_type_registry

    @property
    def gui_extensions(self) -> "PluginExtensionRegistry":
        """Register toolbar buttons, dock specs, AI output renderers, research tabs."""
        return self._core.plugin_extension_registry

    @property
    def citation_service(self) -> "CitationAppService":
        """Citation app service — use register_provider() to contribute citation entries."""
        return self._core.citation_app_service

    # ----------------------------------------------------------------
    # Section C: Event subscription helpers
    # ----------------------------------------------------------------

    def subscribe(self, signal_name: str, slot: Callable) -> None:
        """
        Subscribe to any EventBus signal by name.

        The connection is tracked and automatically disconnected when the
        plugin is unloaded.

        Example::

            api.subscribe("document_added", self._on_document_added)
            api.subscribe("entity_changed", self._on_entity)
        """
        signal = getattr(self._core.bus, signal_name, None)
        if signal is None:
            raise AttributeError(
                f"EventBus has no signal '{signal_name}'. "
                f"Check core/events/event_bus.py for available signal names."
            )
        signal.connect(slot)
        self._subscriptions.append((signal, slot))

    def emit(self, signal_name: str, *args) -> None:
        """
        Emit any EventBus signal by name.

        Use intent/payload signals where a service handler exists. Use this
        for signals without a corresponding handler.
        """
        signal = getattr(self._core.bus, signal_name, None)
        if signal is None:
            raise AttributeError(f"EventBus has no signal '{signal_name}'.")
        signal.emit(*args)

    # ----------------------------------------------------------------
    # Section D: Custom service registration (plugin-to-plugin)
    # ----------------------------------------------------------------

    def register_service(self, service_id: str, instance: Any) -> None:
        """
        Register a service so other plugins can discover it.

        service_id should be namespaced to avoid collisions,
        e.g. ``"my_plugin.translation_service"``.

        Raises ValueError if the id is already taken.
        """
        if not hasattr(self._core, "_custom_services"):
            self._core._custom_services: Dict[str, Any] = {}
        if service_id in self._core._custom_services:
            raise ValueError(
                f"A service with id '{service_id}' is already registered. "
                f"Use a unique, namespaced id."
            )
        self._core._custom_services[service_id] = instance

    def get_service(self, service_id: str) -> Optional[Any]:
        """
        Retrieve a service registered by another plugin.

        Returns None if not found. Use this for optional plugin dependencies.
        """
        return getattr(self._core, "_custom_services", {}).get(service_id)

    def require_service(self, service_id: str, expected_type: Optional[type] = None) -> Any:
        """
        Retrieve a service, raising PluginDependencyError if not found.

        Use this when your plugin cannot function without another plugin's
        service. Call it from on_load() after all dependency plugins have
        had their on_load() called (guaranteed by topological load order).
        """
        service = self.get_service(service_id)
        if service is None:
            raise PluginDependencyError(
                f"Required service '{service_id}' is not registered. "
                f"Declare the providing plugin in your 'dependencies' list."
            )
        if expected_type is not None and not isinstance(service, expected_type):
            raise TypeError(
                f"Service '{service_id}' has type {type(service).__name__}, "
                f"expected {expected_type.__name__}."
            )
        return service

    # ----------------------------------------------------------------
    # Section E: Project lifecycle helpers
    # ----------------------------------------------------------------

    def on_project_open(self, callback: Callable[[str], None]) -> None:
        """
        Register a callback invoked each time a project is opened.

        The callback receives the project file path as a string.
        """
        from core.events.domains.project_events import ProjectEvent

        def _slot(event, payload):
            if event == ProjectEvent.LOADED:
                path = getattr(self._core.project_manager, "project_filepath", "") or ""
                callback(path)

        self.subscribe("project_loaded", _slot)

    def on_project_close(self, callback: Callable[[], None]) -> None:
        """
        Register a callback invoked just before a project closes.
        """
        def _slot(event, payload):
            callback()

        self.subscribe("project_clearing_started", _slot)

    # ----------------------------------------------------------------
    # Section G: Per-plugin persistent configuration
    # ----------------------------------------------------------------

    @property
    def config(self) -> "PluginConfig":
        """
        Persistent per-plugin key-value store.

        Values are saved to ~/.papyrus/plugin_configs/<plugin_id>.json.

        Example::

            api.config.set("api_key", "abc123")
            key = api.config.get("api_key", "")
        """
        return self._config

    # ----------------------------------------------------------------
    # Section H: UI helpers
    # ----------------------------------------------------------------

    def notify(self, message: str, level: str = "info", duration: int = 3000) -> None:
        """
        Emit a user-visible toast notification.

        :param message: Text to display.
        :param level: One of "info", "success", "warning", "error".
        :param duration: Display duration in milliseconds.
        """
        bus = self._core.bus
        signal = getattr(bus, "plugin_notification_requested", None)
        if signal is not None:
            signal.emit(message, level, duration)

    def reload_plugin(self, plugin_id: str) -> bool:
        """
        Hot-reload a plugin by ID. Dev mode only.

        Unloads the plugin, invalidates its module cache, re-imports, and
        re-runs the full lifecycle. The calling plugin cannot reload itself.

        Returns True on success, False if the plugin was not found or failed.
        """
        from core.plugins.plugin_loader import reload_plugin as _reload
        return _reload(self._core, plugin_id)

    # ----------------------------------------------------------------
    # Section F: Cleanup (called by plugin loader on unload)
    # ----------------------------------------------------------------

    def _cleanup(self) -> None:
        """Disconnect all tracked subscriptions. Called on plugin unload."""
        for signal, slot in self._subscriptions:
            try:
                signal.disconnect(slot)
            except RuntimeError:
                pass
        self._subscriptions.clear()
