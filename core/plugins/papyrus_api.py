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
    from core.citation_manager import CitationManager


class PluginDependencyError(RuntimeError):
    """Raised when a required service from another plugin is not available."""
    pass


class _PluginDBNamespace:
    """Namespaced database helpers exposed as ``api.db``."""

    def __init__(self, core: "PapyrusCore", plugin_id: str) -> None:
        self._core = core
        self._plugin_id = plugin_id

    def register_table(self, table_name: str, schema_dict: Dict[str, str]) -> None:
        """Register a plugin-owned SQLite table.

        The table is created (``CREATE TABLE IF NOT EXISTS``) the next time a
        project is opened. The ``plugin_`` prefix is added automatically.

        :param table_name: Logical name without the ``plugin_`` prefix.
        :param schema_dict: Column name → SQLite type, e.g.
                            ``{"ticker": "TEXT NOT NULL", "shares": "REAL DEFAULT 0"}``.
        """
        from core.plugins.plugin_db_registry import PluginTableRegistry
        PluginTableRegistry.get_instance().register_table(
            table_name, schema_dict, self._plugin_id
        )

    def execute(self, sql: str, params: tuple = ()) -> list:
        """Run SQL against the open project database.

        Returns a list of rows (each row a tuple). Commits automatically for
        DML statements. Raises RuntimeError if no project is open.

        Must be called from the main thread or with external locking.
        """
        conn = getattr(self._core.project_manager, "_conn", None)
        if conn is None:
            raise RuntimeError("No project is currently open.")
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.fetchall()


class _PluginTaskNamespace:
    """Background task helpers exposed as ``api.tasks``."""

    def __init__(self, core: "PapyrusCore", plugin_id: str) -> None:
        self._core = core
        self._plugin_id = plugin_id
        self._active_workers: List[Any] = []  # List[PluginWorker]

    def run_background(
        self,
        func: Callable,
        *args: Any,
        job_name: str = "",
        on_done: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ) -> Any:
        """Run *func* in a background QThread tracked by ProcessRegistry.

        :param func: Callable to execute; must be thread-safe.
        :param args: Positional arguments forwarded to *func*.
        :param job_name: Label shown in the process monitor (optional).
        :param on_done: Called with the return value on the main thread.
        :param on_error: Called with the error string on the main thread.
        :returns: The ``LLMJob`` handle (can be used to cancel).
        """
        from core.plugins.plugin_worker import PluginWorker

        name = job_name or f"{self._plugin_id}.task"
        worker = PluginWorker(func, args, job_name=name)

        if on_done:
            worker.finished.connect(on_done)
        if on_error:
            worker.error.connect(on_error)

        self._active_workers.append(worker)
        worker.finished.connect(
            lambda _r, w=worker: self._active_workers.remove(w) if w in self._active_workers else None
        )
        worker.error.connect(
            lambda _e, w=worker: self._active_workers.remove(w) if w in self._active_workers else None
        )

        job = self._core.process_registry.enqueue_runner(
            worker, name, job_type="Plugin", is_express=True
        )
        return job

    def kill_all(self) -> None:
        """Kill all background tasks from this plugin."""
        for worker in list(self._active_workers):
            job = getattr(worker, "job", None)
            if job and hasattr(job, "kill"):
                job.kill()
            elif worker.isRunning():
                worker.terminate()
        self._active_workers.clear()


class _PluginSourceNamespace:
    def __init__(self, core: "PapyrusCore") -> None:
        self._core = core

    def list(self, source_type: str = "") -> list:
        pm = self._core.project_manager
        return pm.list_sources(source_type or None) if hasattr(pm, "list_sources") else []

    def open(self, source_id: str = "", path: str = "", timestamp: float = 0) -> None:
        from core.events.domains.document_events import SourceIntent, SourcePayload
        self._core.bus.source_action_requested.emit(
            SourceIntent.OPEN,
            SourcePayload(source_id=source_id or None, path=path or None, timestamp=timestamp),
        )


class _PluginVideoNamespace:
    def __init__(self, core: "PapyrusCore") -> None:
        self._core = core

    def transcript(self, source_id: str) -> dict:
        pm = self._core.project_manager
        return pm.get_video_transcript(source_id) if hasattr(pm, "get_video_transcript") else {}

    def transcribe(self, source_id: str = "", path: str = "") -> None:
        from core.events.domains.document_events import SourceIntent, SourcePayload
        self._core.bus.source_action_requested.emit(
            SourceIntent.TRANSCRIBE,
            SourcePayload(source_id=source_id or None, path=path or None, source_type="video"),
        )


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
        # Tracks service IDs registered by this plugin so _cleanup() can remove them
        self._registered_service_ids: List[str] = []
        # Tracks workflow step types registered by this plugin for cleanup
        self._registered_step_types: List[str] = []
        # Lazy-initialised namespaces
        self._db_ns: Optional["_PluginDBNamespace"] = None
        self._tasks_ns: Optional["_PluginTaskNamespace"] = None
        self._sources_ns: Optional["_PluginSourceNamespace"] = None
        self._video_ns: Optional["_PluginVideoNamespace"] = None
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
    def data_dock(self):
        """Register Data Dock parsers, cleaners, grid actions, chart types, and palettes."""
        return self._core.data_provider_registry

    @property
    def help_registry(self):
        """Register help topics contributed by this plugin. Returns HelpRegistry."""
        return self._core.help_service.help_registry

    @property
    def tutorial_registry(self):
        """Register tutorials contributed by this plugin. Returns TutorialRegistry."""
        return self._core.help_service.tutorial_registry

    @property
    def ui_targets(self):
        """Register UI target widgets for tutorial spotlighting. Returns UITargetRegistry."""
        return self._core.help_service.ui_target_registry

    @property
    def citation_service(self) -> "CitationAppService":
        """Citation app service — use register_provider() to contribute citation entries."""
        return self._core.citation_app_service

    @property
    def citation_manager(self) -> "CitationManager":
        """Citation formatting and metadata helpers."""
        return self._core.citation_manager

    @property
    def llm_backends(self):
        """
        Register custom LLM backend specs.

        Usage::

            from core.registries.llm_backend_registry import BackendSpec
            api.llm_backends.register(BackendSpec(id="my_backend", ...))
        """
        return self._core.llm_backend_registry

    # ----------------------------------------------------------------
    # Section B2: Database and task namespaces
    # ----------------------------------------------------------------

    @property
    def db(self) -> "_PluginDBNamespace":
        """Plugin database helpers: register_table() and execute()."""
        if self._db_ns is None:
            self._db_ns = _PluginDBNamespace(self._core, self._plugin_id)
        return self._db_ns

    @property
    def tasks(self) -> "_PluginTaskNamespace":
        """Background task helpers: run_background() and kill_all()."""
        if self._tasks_ns is None:
            self._tasks_ns = _PluginTaskNamespace(self._core, self._plugin_id)
        return self._tasks_ns

    @property
    def sources(self) -> "_PluginSourceNamespace":
        """Source helpers for PDFs/videos stored in the active project."""
        if self._sources_ns is None:
            self._sources_ns = _PluginSourceNamespace(self._core)
        return self._sources_ns

    @property
    def video(self) -> "_PluginVideoNamespace":
        """Video transcript and transcription helpers."""
        if self._video_ns is None:
            self._video_ns = _PluginVideoNamespace(self._core)
        return self._video_ns

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
        self._registered_service_ids.append(service_id)

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

    def register_active_node(
        self,
        type_key: str,
        controller_cls: type,
        requires_internet: bool = False,
    ) -> None:
        """Register an ActiveController for *type_key*.

        The controller's ``tick()`` fires on a QTimer, ``on_data_updated()``
        fires when a node of this type changes, and ``get_custom_ui()`` can
        embed a QWidget inside the workspace node via QGraphicsProxyWidget.

        :param type_key: Entity type key (must already be registered in the
                         ontology registry).
        :param controller_cls: Subclass of ``ActiveController``.
        :param requires_internet: True if the controller makes network calls.
        """
        self._core.ontology_registry.register_active_node(
            type_key, controller_cls, requires_internet, self._plugin_id
        )

    def on_text_selected(self, callback: Callable[[str, dict], None]) -> None:
        """Register a callback invoked when the user selects text in the PDF viewer.

        :param callback: Receives ``(selected_text: str, context: dict)``.
                         ``context`` may include ``page_num``, ``rect``, ``doc_path``.
        """
        def _slot(event, payload):
            callback(
                getattr(payload, "text", ""),
                getattr(payload, "context", {}),
            )
        self.subscribe("document_text_selected", _slot)

    def on_editor_before_save(self, callback: Callable[[str, str], None]) -> None:
        """Register a callback invoked before an essay or note is saved.

        :param callback: Receives ``(filepath: str, content: str)``.
        """
        def _slot(event, payload):
            callback(
                getattr(payload, "path", ""),
                getattr(payload, "text", "") or getattr(payload, "content", ""),
            )
        self.subscribe("editor_before_save", _slot)

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

    def register_theme(self, name: str, theme_dict: dict) -> None:
        """
        Register a custom theme contributed by this plugin.

        The theme appears in the Theme Manager under its *name* and is
        automatically removed when the plugin is unloaded or hot-reloaded.
        It is NOT persisted to disk — the plugin must re-register it on every
        app startup.

        :param name: Display name for the theme (must be unique across all themes).
        :param theme_dict: Mapping of theme token keys (see gui/theme/tokens.py)
                           to hex colour strings.

        Example::

            api.register_theme("My Dark Blue", {
                "bg_main": "#0a0f1e",
                "bg_panel": "#111827",
                ...
            })
        """
        from core.plugins.extension_registry import ThemeSpec
        spec = ThemeSpec(name=name, theme_dict=dict(theme_dict), plugin_id=self._plugin_id)
        self.gui_extensions.add_theme(spec)
        # If the GUI is already running, register immediately via the ThemeManager
        # (lazy import keeps the core layer free of hard GUI dependencies).
        try:
            from gui.theme.theme import ThemeManager
            ThemeManager().register_plugin_theme(name, theme_dict, self._plugin_id)
        except Exception:
            pass  # GUI not available (headless / test mode) — no-op

    def register_shortcut(
        self,
        shortcut_id: str,
        label: str,
        callback: "Callable",
        default_key: str = "",
        description: str = "",
    ) -> None:
        """
        Register a keyboard shortcut contributed by this plugin.

        The shortcut appears in Settings → Shortcuts under the "Plugins" scope
        and is user-editable.  The callback is invoked when the key fires.

        :param shortcut_id: Unique ID within this plugin, e.g. ``"sync_zotero"``.
                            The registry stores it as ``plugin.<plugin_id>.<shortcut_id>``.
        :param label: Human-readable label shown in the shortcut editor.
        :param callback: Zero-argument callable to invoke when the key fires.
        :param default_key: Default key sequence string, e.g. ``"Ctrl+Shift+Z"``.
                            Pass ``""`` to leave unbound by default.
        :param description: One-line description shown in the shortcut editor.

        Example::

            api.register_shortcut(
                "sync_citations",
                "Sync Citations from Zotero",
                self._sync,
                default_key="Ctrl+Shift+Z",
                description="Pull latest citations from your Zotero library",
            )
        """
        from core.plugins.extension_registry import ShortcutSpec
        spec = ShortcutSpec(
            shortcut_id=f"{self._plugin_id}.{shortcut_id}",
            label=label,
            key_sequence=default_key,
            callback=callback,
            plugin_id=self._plugin_id,
        )
        self.gui_extensions.register_shortcut(spec)

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

    def register_workflow_step(self, step_cls: type) -> None:
        """Register a CustomExecutionStep subclass as a plugin-contributed workflow step.

        This registers the step as both a callable handler in MasterActionRunner
        and as a WorkflowNodeType in the workflow node type registry (so it
        appears in the workflow builder UI).

        :param step_cls: A subclass of ``CustomExecutionStep`` with ``step_type``,
                         ``input_schema``, ``output_schema``, and ``execute()`` defined.
        """
        from core.plugins.plugin_step_protocol import CustomExecutionStep
        from core.engine.master_runner import MasterActionRunner
        from core.engine.workflow_model import WorkflowNodeType

        if not (isinstance(step_cls, type) and issubclass(step_cls, CustomExecutionStep)):
            raise TypeError(f"{step_cls} must subclass CustomExecutionStep")
        if not step_cls.step_type:
            raise ValueError("CustomExecutionStep.step_type must be a non-empty string")

        step_cls.plugin_id = self._plugin_id

        node_type = WorkflowNodeType(
            id=f"plugin.{step_cls.step_type.lower()}",
            label=step_cls.label or step_cls.step_type,
            category=step_cls.category or "Plugin",
            step_type=step_cls.step_type,
            description=step_cls.description or "",
            plugin_id=self._plugin_id,
            input_schema=dict(step_cls.input_schema),
            output_schema=dict(step_cls.output_schema),
        )
        self._core.workflow_node_type_registry.register(node_type)
        MasterActionRunner.register_plugin_step_handler(step_cls.step_type, step_cls)
        self._registered_step_types.append(step_cls.step_type)

    def register_dock_action(self, spec: Any) -> None:
        """Register a DockActionSpec to be injected into dock context menus or toolbars.

        The spec's ``plugin_id`` is automatically set to this plugin's ID.
        ``spec.mounts`` lists the mount point string constants from
        ``core.engine.dock_mounts`` that the action should appear at.

        Example::

            from core.engine.dock_mounts import SOURCE_VIEWER_TEXT_SELECTION
            from core.plugins.extension_registry import DockActionSpec, DockActionContext

            def _handle(ctx: DockActionContext):
                api.workflow_runner.run_blueprint(my_bp, {"text": ctx.selection or ""})

            api.register_dock_action(DockActionSpec(
                action_id="myplugin.summarize",
                label="Summarize Selection",
                mounts=[SOURCE_VIEWER_TEXT_SELECTION],
                callback=_handle,
            ))
        """
        spec.plugin_id = self._plugin_id
        self.gui_extensions.add_dock_action(spec)

    def register_pack_contributor(self, contributor) -> None:
        """
        Register a :class:`PackContributor` so the Import/Export system can include
        this plugin's custom data in .ppack files.

        :param contributor: Object implementing the ``PackContributor`` protocol
                            (``contributor_id``, ``display_name``, ``get_exportable_items``,
                            ``export_items``, ``preview_items``, ``import_items``).

        Example::

            class MyContributor:
                contributor_id = "myplugin"
                display_name = "My Plugin Data"

                def get_exportable_items(self): ...
                def export_items(self, item_ids): ...
                def preview_items(self, data): ...
                def import_items(self, data, item_ids): ...

            api.register_pack_contributor(MyContributor())
        """
        reg = getattr(self._core, "pack_contributor_registry", None)
        if reg is not None:
            reg.register(contributor, self._plugin_id)

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
        """Disconnect all tracked subscriptions and remove registered services. Called on plugin unload."""
        for signal, slot in self._subscriptions:
            try:
                signal.disconnect(slot)
            except RuntimeError:
                pass
        self._subscriptions.clear()
        for service_id in self._registered_service_ids:
            self._core._custom_services.pop(service_id, None)
        self._registered_service_ids.clear()
        # Remove plugin table registrations (does NOT drop the actual SQLite table)
        from core.plugins.plugin_db_registry import PluginTableRegistry
        PluginTableRegistry.get_instance().remove_by_plugin(self._plugin_id)
        # Kill any running background tasks
        if self._tasks_ns is not None:
            self._tasks_ns.kill_all()
        # Unregister pack contributors
        reg = getattr(self._core, "pack_contributor_registry", None)
        if reg is not None:
            reg.remove_plugin(self._plugin_id)
        # Unregister plugin workflow step handlers
        from core.engine.master_runner import MasterActionRunner
        for st in self._registered_step_types:
            MasterActionRunner.unregister_plugin_step_handler(st)
        self._registered_step_types.clear()
        # Remove plugin help content
        hs = getattr(self._core, "help_service", None)
        if hs is not None:
            hs.help_registry.remove_plugin_topics(self._plugin_id)
            hs.tutorial_registry.remove_plugin_tutorials(self._plugin_id)
        data_registry = getattr(self._core, "data_provider_registry", None)
        if data_registry is not None and hasattr(data_registry, "remove_plugin"):
            data_registry.remove_plugin(self._plugin_id)
