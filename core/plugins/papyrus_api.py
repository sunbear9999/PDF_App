"""
core/plugins/papyrus_api.py

The controlled surface that plugins receive instead of raw PapyrusCore.
Plugins access services, registries, and events through this facade.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
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
        conn = getattr(self._core.project_manager, "_conn", None)
        if conn is not None:
            PluginTableRegistry.get_instance().ensure_tables(conn, self._plugin_id)

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
        on_progress: Optional[Callable] = None,
        on_partial: Optional[Callable] = None,
        pass_cancel_check: bool = False,
        pass_task_context: bool = False,
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
        worker = PluginWorker(
            func,
            args,
            job_name=name,
            pass_cancel_check=pass_cancel_check,
            pass_task_context=pass_task_context,
        )

        if on_done:
            worker.finished.connect(on_done)
        if on_error:
            worker.error.connect(on_error)
        if on_progress:
            worker.progress.connect(on_progress)
        if on_partial:
            worker.partial.connect(on_partial)
        worker.progress.connect(
            lambda current, total, message, w=worker:
            self._core.process_registry.update_job_progress(
                w.job.id, current=current, total=total, message=message
            ) if w.job else None
        )

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
        # Connected last so the plugin's on_done/on_error above fire first.
        # PluginWorker never calls complete_job itself (unlike MasterActionRunner),
        # so without this the job stays in ProcessRegistry.express_jobs forever,
        # pinning the dead QThread and its connected closures.
        registry = self._core.process_registry
        worker.finished.connect(lambda _r, j=job: registry.complete_job_threadsafe(j.id))
        worker.error.connect(lambda _e, j=job: registry.complete_job_threadsafe(j.id))
        return job

    def kill_all(self) -> None:
        """Kill all background tasks from this plugin."""
        workers = list(self._active_workers)
        for worker in workers:
            job = getattr(worker, "job", None)
            if job and hasattr(job, "kill"):
                job.kill()
            elif worker.isRunning():
                worker.requestInterruption()
        for worker in workers:
            if worker.isRunning():
                worker.wait(750)
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

    def jump(self, source_type: str, locator: dict) -> None:
        """
        Trigger "jump to source" navigation for a source_type without
        needing to reach into plugin_extension_registry directly. Looks up
        the registered CitationSourceHandlerSpec (built-in or plugin) and
        invokes its callback with an AppContext-like object if the GUI layer
        has registered one, else no-ops.
        """
        registry = self._core.plugin_extension_registry
        handler = registry.get_citation_source_handler(source_type) if registry else None
        if handler is None:
            return
        # Built-in handlers (see MainWindow._register_builtin_locate_handlers)
        # close over their own viewer reference and ignore this argument;
        # plugin handlers that need GUI context should do the same.
        handler.callback(locator, None)

    def import_file(self, request, task_context=None):
        """Atomically import a local file into project-owned source storage."""
        from core.models.source_import_models import SourceImportRequest

        if isinstance(request, dict):
            request = SourceImportRequest(**request)
        if not isinstance(request, SourceImportRequest):
            raise TypeError("request must be SourceImportRequest or a matching dict")
        service = getattr(self._core, "source_import_service", None)
        if service is None:
            raise RuntimeError("Source import service is unavailable.")
        return service.import_source(
            request,
            cancel_check=getattr(task_context, "cancel_check", None),
            progress=getattr(task_context, "progress_callback", None),
        )


class _PluginNetworkNamespace:
    """Plugin-scoped access to the central outbound network gate."""

    def __init__(self, core, plugin_id: str, allowed_hosts) -> None:
        self._service = core.network_access_service
        self._plugin_id = plugin_id
        self._allowed_hosts = tuple(allowed_hosts)

    @property
    def is_allowed(self) -> bool:
        return self._service.is_granted(self._plugin_id)

    @property
    def allowed_hosts(self) -> Tuple[str, ...]:
        return self._allowed_hosts

    def get_json(self, url: str, *, params=None, max_bytes=8 * 1024 * 1024,
                 timeout=20.0, task_context=None, allow_unlisted_host=False):
        response = self._service.request(
            self._plugin_id,
            url,
            allowed_hosts=self._allowed_hosts,
            allow_unlisted_host=allow_unlisted_host,
            params=params,
            max_bytes=max_bytes,
            timeout=timeout,
            cancel_check=getattr(task_context, "cancel_check", None),
        )
        try:
            return response.json()
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("Archive returned invalid JSON.") from exc

    def get_bytes(self, url: str, *, params=None, max_bytes=8 * 1024 * 1024,
                  timeout=20.0, task_context=None, allow_unlisted_host=False):
        return self._service.request(
            self._plugin_id,
            url,
            allowed_hosts=self._allowed_hosts,
            allow_unlisted_host=allow_unlisted_host,
            params=params,
            max_bytes=max_bytes,
            timeout=timeout,
            cancel_check=getattr(task_context, "cancel_check", None),
        )

    def download(self, url: str, destination: str, *, max_bytes=250 * 1024 * 1024,
                 timeout=20.0, task_context=None, allow_unlisted_host=False):
        return self._service.download(
            self._plugin_id,
            url,
            destination,
            allowed_hosts=self._allowed_hosts,
            allow_unlisted_host=allow_unlisted_host,
            max_bytes=max_bytes,
            timeout=timeout,
            cancel_check=getattr(task_context, "cancel_check", None),
            progress=getattr(task_context, "progress_callback", None),
        )


class _PluginStorageNamespace:
    """Safe plugin-private cache and temporary paths outside project files."""

    def __init__(self, core, plugin_id: str) -> None:
        self._root = Path(core.user_data_dir) / "plugin_cache" / plugin_id
        self._root.mkdir(parents=True, exist_ok=True)

    def cache_dir(self, *parts: str) -> str:
        candidate = self._root.joinpath(*[self._safe_part(p) for p in parts]).resolve()
        if candidate != self._root.resolve() and self._root.resolve() not in candidate.parents:
            raise ValueError("Unsafe plugin cache path.")
        candidate.mkdir(parents=True, exist_ok=True)
        return str(candidate)

    def temp_path(self, suffix: str = "") -> str:
        handle = tempfile.NamedTemporaryFile(
            dir=self.cache_dir("tmp"), suffix=suffix, delete=False
        )
        path = handle.name
        handle.close()
        return path

    @staticmethod
    def _safe_part(value: str) -> str:
        part = os.path.basename(str(value or "")).strip()
        if not part or part in {".", ".."}:
            raise ValueError("Invalid plugin cache path component.")
        return part


class _PluginSourceFormatNamespace:
    """
    Source format registration, exposed as ``api.source_formats``.

    A plugin adds an entirely new ingestible source type with three calls:
      1. ``api.source_formats.register(SourceFormatSpec(...))`` — dispatch
         (extensions/mime types, category, parser/loader id, availability).
      2. ``api.gui_extensions.add_source_viewer(SourceViewerSpec(...))`` —
         the viewer widget factory shown in SourceViewerHost.
      3. ``api.gui_extensions.add_citation_source_handler(CitationSourceHandlerSpec(...))``
         — jump-to-source navigation for that source_type.

    See core/registries/source_format_registry.py for SourceFormatSpec.
    """

    def __init__(self, core: "PapyrusCore") -> None:
        self._core = core

    def register(self, spec) -> None:
        self._core.source_format_registry.register(spec)

    def available(self) -> List[str]:
        return [s.source_type for s in self._core.source_format_registry.all()]

    def get(self, source_type: str):
        return self._core.source_format_registry.get(source_type)


class _PluginTagNamespace:
    """Project tag helpers exposed as ``api.tags``.

    All methods proxy to the project's TagDB via ProjectManager.
    They are safe to call from the main thread at any point when a project is open.
    """

    def __init__(self, core: "PapyrusCore") -> None:
        self._core = core

    @property
    def _pm(self):
        return self._core.project_manager

    def ensure_tag(self, name: str, color: str = "#607d8b") -> Optional[int]:
        """Return the tag_id for *name*, creating it (with *color*) if absent."""
        for t in (self._pm.get_all_tags() or []):
            if t["name"].lower() == name.lower():
                return t["id"]
        return self._pm.create_tag(name, color)

    def create(self, name: str, color: str = "#607d8b") -> Optional[int]:
        return self._pm.create_tag(name, color)

    def delete(self, tag_id: int) -> None:
        self._pm.delete_tag(tag_id)

    def rename(self, tag_id: int, new_name: str) -> None:
        self._pm.rename_tag(tag_id, new_name)

    def update_color(self, tag_id: int, color: str) -> None:
        self._pm.update_tag_color(tag_id, color)

    def list_all(self) -> List[Dict[str, Any]]:
        return self._pm.get_all_tags() or []

    def assign_to_doc(self, doc_path: str, tag_id: int) -> None:
        self._pm.assign_tag_to_doc(doc_path, tag_id)

    def remove_from_doc(self, doc_path: str, tag_id: int) -> None:
        self._pm.remove_tag_from_doc(doc_path, tag_id)

    def get_for_doc(self, doc_path: str) -> List[Dict[str, Any]]:
        return self._pm.get_tags_for_doc(doc_path) or []

    def get_docs_for_tag(self, tag_id: int) -> List[Dict[str, Any]]:
        return self._pm.get_docs_for_tag(tag_id) or []

    def sync_tags(self, tag_dicts: List[Dict[str, Any]]) -> Dict[str, int]:
        """Ensure all tags in *tag_dicts* exist in the project; return name→tag_id map."""
        mapping: Dict[str, int] = {}
        for t in tag_dicts:
            tid = self.ensure_tag(t["name"], t.get("color", "#607d8b"))
            if tid is not None:
                mapping[t["name"]] = tid
        return mapping


class _PluginWidgetNamespace:
    """Widget registry helpers exposed as ``api.widgets``.

    Plugins call ``api.widgets.register(AIWidgetSpec(...))`` to contribute a
    full widget type: LLM prompt, payload parser, and optional GUI renderer.

    Example::

        from core.registries.ai_widget_registry import AIWidgetDefinition
        from core.plugins.extension_registry import AIWidgetSpec

        def on_load(self, api):
            api.widgets.register(AIWidgetSpec(
                definition=AIWidgetDefinition(
                    widget_id="my_timeline",
                    label="Timeline",
                    prompt_key="My Timeline Format",
                    default_prompt="Output a JSON array of {date, event, description}...",
                    payload_parser=_parse_timeline,
                    plugin_id=self.plugin_id,
                ),
                renderer_factory=lambda payload, parent: TimelineWidget(payload, parent),
            ))
    """

    def __init__(self, core: "PapyrusCore", plugin_id: str) -> None:
        self._core = core
        self._plugin_id = plugin_id
        self._registered_widget_ids: List[str] = []

    def register(self, spec: "Any") -> None:  # spec: AIWidgetSpec
        """Register a widget type end-to-end (prompt + parser + optional renderer).

        *spec* must be an ``AIWidgetSpec`` from
        ``core.plugins.extension_registry``.  The backend definition is
        registered in ``AIWidgetRegistry``; the ``renderer_factory`` (if any)
        is registered in ``AIOutputWidgetFactory`` so the GUI can create the
        widget from its payload type.

        Also seeds the widget's ``default_prompt`` into ``PromptManager`` if
        the key does not yet exist there.
        """
        from core.registries.ai_widget_registry import AIWidgetRegistry

        defn = spec.definition
        # Ensure plugin_id is set on the definition for hot-reload cleanup.
        if not getattr(defn, "plugin_id", None):
            defn.plugin_id = self._plugin_id

        # Seed default prompt so restore-to-default works.
        if defn.prompt_key and defn.default_prompt:
            pm = getattr(self._core, "prompt_manager", None)
            if pm is not None and defn.prompt_key not in pm.DEFAULT_PROMPTS:
                pm.DEFAULT_PROMPTS[defn.prompt_key] = defn.default_prompt

        AIWidgetRegistry.get_instance().register(defn)
        self._registered_widget_ids.append(defn.widget_id)

        # Also register the old AIOutputRendererSpec pathway so that the
        # AIOutputWidgetFactory (GUI) can create the widget when a payload
        # of this type arrives.  The renderer_factory receives the payload
        # dict and a parent widget.
        if spec.renderer_factory is not None:
            from core.plugins.extension_registry import AIOutputRendererSpec
            self._core.plugin_extension_registry.add_ai_renderer(
                AIOutputRendererSpec(
                    payload_type=defn.widget_id,
                    factory=spec.renderer_factory,
                    plugin_id=self._plugin_id,
                )
            )

    def _cleanup(self) -> None:
        """Remove all widget types registered by this plugin (called on unload)."""
        from core.registries.ai_widget_registry import AIWidgetRegistry
        AIWidgetRegistry.get_instance().remove_by_plugin(self._plugin_id)
        self._registered_widget_ids.clear()


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


class _PluginMemoryNamespace:
    """Register memory-holding resources with the central memory manager."""

    def __init__(self, core: "PapyrusCore", plugin_id: str) -> None:
        self._core = core
        self._plugin_id = plugin_id

    def register_consumer(self, consumer_id: str, label: str, usage, release, *,
                          kind: str = "cache", busy=None, priority: int = 30) -> str:
        """``usage()`` returns bytes held; ``release(level)`` frees them ("trim" or "unload")."""
        from core.models.memory_models import MemoryConsumer
        full_id = f"plugin:{self._plugin_id}:{consumer_id}"
        self._core.memory_manager_service.register_consumer(MemoryConsumer(
            id=full_id, label=label, kind=kind, usage=usage, release=release,
            busy=busy or (lambda: False), priority=priority, plugin_id=self._plugin_id,
        ))
        return full_id

    def snapshot(self):
        return self._core.memory_manager_service.snapshot()

    def reserve(self, size_bytes: int, purpose: str, *, per_worker: int = 0, max_workers: int = 1):
        """Call from a background thread before heavy work; returns a Grant."""
        return self._core.memory_manager_service.reserve(
            size_bytes, f"{self._plugin_id}: {purpose}", per_worker=per_worker, max_workers=max_workers)


class _PluginIntelligenceNamespace:
    """Controlled access to source intelligence and artifact retrieval."""

    def __init__(
        self,
        core: "PapyrusCore",
        plugin_id: str,
        registered_provider_ids: List[str],
        registered_producer_ids: List[str],
    ) -> None:
        self._core = core
        self._plugin_id = plugin_id
        self._registered_provider_ids = registered_provider_ids
        self._registered_producer_ids = registered_producer_ids
        self._registered_types = set()
        self._registered_type_topics = {}

    def page_links(self, source_id, page_indices):
        return self._core.document_link_service.page_links(source_id, page_indices)

    def register_link_provider(self, provider_id, provider):
        self._core.document_link_service.register_provider(
            self._plugin_id + ":" + provider_id, provider, self._plugin_id)

    def get_source_status(self, source_id: str) -> dict:
        svc = getattr(self._core, "source_intelligence_service", None)
        return svc.get_source_status(source_id) if svc else {}

    def run_stage(self, source_id: str, stage: str):
        svc = getattr(self._core, "source_intelligence_service", None)
        return svc.run_stage(source_id, stage) if svc else None

    def run_missing(self, source_id: str):
        svc = getattr(self._core, "source_intelligence_service", None)
        return svc.run_missing(source_id) if svc else []

    def rerun_stale(self, source_id: str):
        svc = getattr(self._core, "source_intelligence_service", None)
        return svc.rerun_stale(source_id) if svc else []

    def delete_derived(self, source_id: str, stage: str = "") -> int:
        svc = getattr(self._core, "source_intelligence_service", None)
        return svc.delete_derived(source_id, stage or None) if svc else 0

    def archive_derived(self, source_id: str, stage: str = "") -> int:
        svc = getattr(self._core, "source_intelligence_service", None)
        return svc.archive_derived(source_id, stage or None) if svc else 0

    def get_artifacts(self, source_id: str = "", filters: Optional[dict] = None) -> list:
        svc = getattr(self._core, "source_intelligence_service", None)
        return svc.get_artifacts(source_id or None, filters or {}) if svc else []

    def get_all_artifacts(self, source_id: str, filters: Optional[dict] = None) -> list:
        """Every matching artifact of a source, uncapped (producers must see the whole source)."""
        service = getattr(self._core, "artifact_service", None)
        return service.get_all_artifacts(source_id, filters or {}) if service else []

    def retire_artifacts(self, artifact_ids: list) -> None:
        """Mark this plugin's outdated artifacts superseded (kept in history, hidden from views)."""
        service = getattr(self._core, "artifact_service", None)
        if service and artifact_ids:
            service.db.mark_artifact_ids_status(sorted(artifact_ids), "superseded")

    def get_related_artifacts(self, artifact_id: str) -> dict:
        svc = getattr(self._core, "source_intelligence_service", None)
        return svc.get_related_artifacts(artifact_id) if svc else {}

    def plan_processing(self, source_id: str) -> dict:
        """Per-stage plan for a source: selected/why, status, blocking reason
        and the method/model that would run (same plan the scheduler runs)."""
        service = getattr(self._core, "processing_plan_service", None)
        plan = service.plan_source(source_id) if service else None
        return plan.to_dict() if plan else {}

    def preview_processing(self, selection: Optional[dict], category: str = "document") -> dict:
        service = getattr(self._core, "processing_plan_service", None)
        return service.preview(selection, category).to_dict() if service else {}

    def resolve_navigation(self, artifact_id: str) -> dict:
        """Resolve an artifact to a typed source anchor.

        Returns ``SourceNavigationTarget.to_dict()``: source ID/path/type,
        ``precision`` (exact, repaired, coarse, missing_source, unresolved),
        ``granularity`` and per-span page/block/char range/rects or time range.
        May parse a page; call from a background task, not the Qt thread.
        """
        resolver = getattr(self._core, "source_anchor_resolver", None)
        return resolver.resolve(artifact_id).to_dict() if resolver else {}

    def build_citation_map(self, source_id: str) -> dict:
        """Project canonical bibliography matches into a workspace-ready graph."""
        svc = getattr(self._core, "source_intelligence_service", None)
        return svc.build_citation_map(source_id) if svc else {
            "entities": [], "relations": []
        }

    def query_artifacts(self, query: dict) -> dict:
        service = getattr(self._core, "artifact_service", None)
        return service.query_artifacts(query) if service else {"items": [], "next_cursor": ""}

    def iter_artifacts(self, query: dict):
        service = getattr(self._core, "artifact_service", None)
        return service.iter_artifacts(query) if service else iter(())

    def query_entities(self, query: dict) -> dict:
        service = getattr(self._core, "artifact_service", None)
        return service.query_entities(query) if service else {"items": [], "next_cursor": ""}

    def iter_entities(self, query: dict):
        service = getattr(self._core, "artifact_service", None)
        return service.iter_entities(query) if service else iter(())

    def revise_artifact(self, artifact_id: str, **changes) -> dict:
        service = getattr(self._core, "artifact_service", None)
        return service.revise_artifact(artifact_id, **changes) if service else {}

    def revise_artifact_fields(
        self, artifact_id: str, changes: dict, *, provenance_confirmed: bool = False
    ) -> dict:
        service = getattr(self._core, "artifact_service", None)
        return service.revise_artifact_fields(
            artifact_id, changes, provenance_confirmed=provenance_confirmed,
            created_by=f"plugin:{self._plugin_id}",
        ) if service else {}

    def assign_citation(self, citation_id: str, entry_id: Optional[str]) -> dict:
        service = getattr(self._core, "artifact_service", None)
        return service.assign_citation(
            citation_id, entry_id, created_by=f"plugin:{self._plugin_id}"
        ) if service else {}

    def citation_assignments(self, source_id: str = "") -> dict:
        service = getattr(self._core, "artifact_service", None)
        return service.citation_assignments(source_id or None) if service else {}

    def get_reprocessing_proposals(self, artifact_id: str) -> list:
        service = getattr(self._core, "artifact_service", None)
        return service.get_reprocessing_proposals(artifact_id) if service else []

    def set_artifact_review_state(self, artifact_id: str, review_state: str) -> dict:
        service = getattr(self._core, "artifact_service", None)
        return (
            service.set_review_state(
                artifact_id, review_state, created_by=f"plugin:{self._plugin_id}"
            ) or {}
            if service else {}
        )

    def get_artifact_revisions(self, artifact_id: str) -> list:
        service = getattr(self._core, "artifact_service", None)
        return service.get_revisions(artifact_id) if service else []

    def upsert_artifacts(self, artifacts: list) -> list:
        """Batch idempotent grounded artifacts into the project store."""
        from core.models.intelligence_models import ArtifactRecord

        db = getattr(getattr(self._core, "project_manager", None), "db_intelligence", None)
        if not db or not hasattr(db, "upsert_artifacts"):
            raise RuntimeError("Batch artifact storage is not available.")
        records = []
        for value in artifacts or []:
            if isinstance(value, ArtifactRecord):
                records.append(value)
                continue
            payload = dict(value or {})
            payload.setdefault("created_by", f"plugin:{self._plugin_id}")
            records.append(ArtifactRecord(**payload))
        return db.upsert_artifacts(records)

    def knowledge_signature(
        self, source_ids: Optional[list] = None,
        artifact_families: Optional[list] = None,
    ) -> str:
        """Return the scoped semantic revision used by analysis caches."""
        db = getattr(getattr(self._core, "project_manager", None), "db_intelligence", None)
        if not db or not hasattr(db, "knowledge_signature"):
            return "0"
        return db.knowledge_signature(
            source_ids=source_ids or [],
            artifact_families=artifact_families or [],
        )

    @staticmethod
    def normalize_graph_contract(contract: Optional[dict] = None) -> dict:
        """Validate and fill the versioned grounded-graph contract."""
        from core.registries.analysis_mode_registry import normalize_graph_contract

        return normalize_graph_contract(contract or {})

    def build_retrieval_plan(self, query: str, **kwargs) -> dict:
        from dataclasses import asdict
        svc = getattr(self._core, "source_intelligence_service", None)
        plan = svc.build_retrieval_plan(query, **kwargs) if svc else None
        return asdict(plan) if plan else {"query": query}

    def retrieve(self, plan_or_query, **kwargs) -> list:
        from dataclasses import asdict, is_dataclass
        from core.models.intelligence_models import RetrievalPlan, SourceScope
        service = getattr(self._core, "hybrid_retrieval_service", None)
        if not service:
            return []
        if isinstance(plan_or_query, RetrievalPlan):
            plan = plan_or_query
        elif isinstance(plan_or_query, dict):
            fields = RetrievalPlan.__dataclass_fields__
            payload = {key: plan_or_query.get(key) for key in fields if key in plan_or_query}
            if isinstance(payload.get("source_scope"), dict):
                payload["source_scope"] = SourceScope(**payload["source_scope"])
            plan = RetrievalPlan(**payload)
        else:
            plan = service.build_plan(str(plan_or_query or ""), **kwargs) if hasattr(service, "build_plan") else RetrievalPlan(query=str(plan_or_query or ""))
        return [asdict(item) if is_dataclass(item) else dict(item) for item in service.retrieve(plan)]

    def register_retrieval_provider(self, provider_id: str, callback: Callable) -> str:
        service = getattr(self._core, "hybrid_retrieval_service", None)
        if not service:
            raise RuntimeError("Hybrid retrieval service is not available.")
        qualified_id = f"{self._plugin_id}:{provider_id}"
        service.register_provider(qualified_id, callback)
        if qualified_id not in self._registered_provider_ids:
            self._registered_provider_ids.append(qualified_id)
        return qualified_id

    def register_artifact_producer(self, producer_id: str, callback: Callable) -> str:
        service = getattr(self._core, "artifact_service", None)
        if not service or not hasattr(service, "register_producer"):
            raise RuntimeError("Artifact producer registry is not available.")
        qualified_id = f"{self._plugin_id}:{producer_id}"
        service.register_producer(qualified_id, callback)
        # Executable: a StageSpec whose handler_id is this qualified id runs it.
        producers = getattr(self._core, "intelligence_producer_registry", None)
        if producers is not None:
            producers.register(qualified_id, callback, plugin_id=self._plugin_id)
        if qualified_id not in self._registered_producer_ids:
            self._registered_producer_ids.append(qualified_id)
        return qualified_id

    def register_artifact_type(self, artifact_type: str, metadata: Optional[dict] = None) -> str:
        from core.plugins.artifact_type_registration import register_type
        return register_type(self, artifact_type, metadata)

    def resolve_reference(self, reference):
        """Headless read API; invoke from the plugin task API, not the Qt thread."""
        return self._core.artifact_service.resolve_reference(reference)

    def resolve_references(self, references, *, compact=False):
        """Batch up to 32 snapshots using api.tasks.run_background()."""
        return self._core.artifact_service.resolve_references(references, compact=compact)

    def prepare_reference_note(self, reference):
        return self._core.reference_note_service.prepare(reference)

    def prepare_selection_note(self, selection):
        """Verify PDF words, transcript ranges, raw blocks or explicit legacy adoption off Qt."""
        return self._core.reference_note_service.prepare_selection(selection)

    def selection_source_snapshot(self, source_path, block_ids=None):
        """Read file-bound cached text/image blocks off Qt; never start a model."""
        return self._core.reference_note_service.selection_source_snapshot(source_path, block_ids)

    def save_selection_note(self, selection, body, **guards):
        return self._core.reference_note_service.save({}, body, selection=selection, **guards)

    def linked_note_snapshot(self, highlight_id):
        return self._core.reference_note_service.note_snapshot(highlight_id)

    def query_notes(self, **query):
        """Read a guarded mixed-source Notes page off Qt (at most 25 rows)."""
        return self._core.reference_note_service.query_notes(**query)

    def prepare_event(self, reference):
        return self._core.reference_note_service.prepare(reference)

    def query_workspace_inbox(self, **query):
        return self._core.reference_note_service.query_workspace_inbox(**query)

    def prepare_workspace_import(self, items, **guards):
        return self._core.reference_note_service.prepare_workspace_import(items, **guards)

    def event_snapshot(self, artifact_id):
        return self._core.reference_note_service.event_snapshot(artifact_id)

    def save_event(self, reference, values, **guards):
        return self._core.reference_note_service.save_event(reference, values, **guards)

    def update_event(self, artifact_id, values, **guards):
        return self._core.reference_note_service.update_event(artifact_id, values, **guards)

    def query_artifact_history(self, artifact_id, cursor='', limit=25):
        """Read complete revision/proposal history through bounded cursor pages off Qt."""
        return self._core.artifact_service.query_artifact_history(artifact_id, cursor, limit)

    def query_timeline(self, query=None, filters=None):
        """Read bounded evidence-backed event candidates off Qt; no extraction runs."""
        return self._core.artifact_service.query_timeline(query,filters)

    def query_entity_network(self, query=None, filters=None):
        return self._core.artifact_service.query_entity_network(query, filters)

    def get_projection_views(self):
        return self._core.artifact_service.get_projection_views()

    def check_projection_ids(self, filters):
        return self._core.artifact_service.check_projection_ids(filters)

    def save_projection_view(self, **arguments):
        """Persist project-local semantic filters with a current token/project guard."""
        return self._core.artifact_service.save_projection_view(**arguments)

    def query_chat_history(self, tab_name, cursor='', limit=12):
        """Read a bounded saved-message page off Qt; saved quotes remain unchanged."""
        return self._core.reference_note_service.query_chat_history(tab_name, cursor, limit)

    def save_reference_note(self, reference, body, **guards):
        return self._core.reference_note_service.save(reference, body, **guards)

    def modify_reference_note(self, annotation_id, command, **guards):
        return self._core.reference_note_service.modify(annotation_id, command, **guards)

    def register_entity_extractor(self, extractor) -> str:
        """Register a plugin EntityExtractor layer (see EntityExtractor protocol).

        The extractor participates in the ENTITIES ingestion stage ordered by its
        ``layer``. Cleaned up automatically on plugin unload.
        """
        registry = getattr(self._core, "entity_extractor_registry", None)
        if not registry:
            raise RuntimeError("Entity extractor registry is not available.")
        registry.register(extractor, plugin_id=self._plugin_id)
        return getattr(extractor, "id", "")

    def register_entity_evidence_source(self, source) -> str:
        """Add typing evidence for the Entities stage (see EntityEvidenceSource).

        The source adds weighted type votes to mentions; it never sets a type.
        Votes become per-name support rates next to usage evidence. A source
        with a ``setting_key`` runs only when that project setting is on, and
        its ``identity()`` is part of the stage fingerprint. Removed on unload.
        """
        registry = getattr(self._core, "entity_evidence_registry", None)
        if registry is None:
            raise RuntimeError("Entity evidence registry is not available.")
        registry.register(source, plugin_id=self._plugin_id)
        return getattr(source, "id", "")

    def register_connection_type(self, key: str, label: str) -> str:
        """A type users can save an entity connection as (e.g. ``"treaty_party"``). Removed on unload."""
        from core.services.intelligence import connection_review
        connection_review.register_connection_type(key, label, plugin_id=self._plugin_id)
        return key

    def register_link_explainer(self, edge_type: str, explainer) -> str:
        """Plain-language reason for links of ``edge_type`` shown when they are reviewed.

        ``explainer(metadata, first_name, second_name) -> str`` receives the link
        assertion's metadata (its ``reason`` dict when the writer recorded one).
        Removed on unload."""
        from core.services.intelligence import link_explanations
        link_explanations.register_explainer(edge_type, explainer, plugin_id=self._plugin_id)
        return edge_type

    def register_stage(self, spec, producer: Optional[Callable] = None) -> str:
        """Register an executable intelligence stage.

        ``producer`` implements ``core.models.producer_contract.IntelligenceProducer``
        (``identity(context)`` + ``run(context) -> ProducerResult``) or is a
        callable ``(ProducerContext) -> list of artifact IDs``. It runs on the
        intelligence queue with cancellation via ``context.cancelled()``. A
        stage without a producer, or one creating a dependency cycle, is
        rejected. Both are removed (and their jobs cancelled) on unload.
        """
        from core.models.intelligence_models import StageSpec
        registry = getattr(self._core, "intelligence_stage_registry", None)
        producers = getattr(self._core, "intelligence_producer_registry", None)
        if not registry:
            raise RuntimeError("Intelligence stage registry is not available.")
        if isinstance(spec, dict):
            spec = StageSpec(**spec)
        handler = spec.handler_id or spec.id
        if producer is not None and producers is not None:
            producers.register(handler, producer, plugin_id=self._plugin_id)
        try:
            registry.register(spec, plugin_id=self._plugin_id)
        except ValueError:
            if producer is not None and producers is not None:
                producers.unregister(handler)
            raise
        return spec.id

    def register_review_queue(self, spec) -> str:
        from dataclasses import replace
        from core.registries.intelligence_review_registry import ReviewIssueSpec
        registry = getattr(self._core, "intelligence_review_registry", None)
        if registry is None:
            raise RuntimeError("Intelligence review registry is not available.")
        if isinstance(spec, dict):
            spec = ReviewIssueSpec(**spec)
        spec = replace(spec, plugin_id=self._plugin_id)
        registry.register(spec)
        return spec.id

    def prepare_review_decision(self, artifact_id, command, payload=None):
        return self._core.artifact_service.prepare_review_decision(artifact_id, command, payload)

    def query_decision_candidates(self, artifact_id, command, **kwargs):
        return self._core.artifact_service.query_decision_candidates(artifact_id, command, **kwargs)

    def review_artifact(self, artifact_id, command, **kwargs):
        return self._core.artifact_service.execute_review_command(artifact_id, command, **kwargs)

    def query_review_queue(self, issue_id, source_id="", **kwargs):
        return self._core.artifact_service.get_review_queue(issue_id, source_id, **kwargs)

    def query_occurrences(self, artifact_id, query=None):
        return self._core.artifact_service.query_occurrences(artifact_id, query)

    def query_stage_runs(self, query):
        return self._core.artifact_service.query_stage_runs(query)

    def get_browser_preferences(self):
        return self._core.artifact_service.get_browser_preferences()

    def processing_snapshot(self, source_id, selection=None):
        return self._core.processing_plan_service.get_processing_snapshot(source_id, selection)

    def save_processing_selection(self, source_id, selection, **kwargs):
        return self._core.processing_plan_service.save_selection(source_id, selection, **kwargs)

    def process_source(self, source_id, **kwargs):
        return self._core.processing_plan_service.process_source(source_id, **kwargs)

    def save_browser_preferences(self, state, **kwargs):
        return self._core.artifact_service.save_browser_preferences(state, **kwargs)

    def register_view(self, spec) -> str:
        from core.registries.intelligence_view_registry import IntelligenceViewSpec
        registry = getattr(self._core, "intelligence_view_registry", None)
        if not registry:
            raise RuntimeError("Intelligence view registry is not available.")
        if isinstance(spec, dict):
            spec = IntelligenceViewSpec(**spec)
        spec.plugin_id = self._plugin_id
        registry.register(spec)
        return spec.id

    def register_intent_provider(self, provider_id: str, callback: Callable) -> str:
        service = getattr(self._core, "hybrid_retrieval_service", None)
        if not service:
            raise RuntimeError("Hybrid retrieval service is not available.")
        qualified_id = f"{self._plugin_id}:{provider_id}"
        service.register_intent_provider(qualified_id, callback)
        if qualified_id not in self._registered_provider_ids:
            self._registered_provider_ids.append(qualified_id)
        return qualified_id

    def register_analysis_mode(self, spec) -> str:
        """Register a plugin analysis mode (blueprint-backed).

        ``spec`` is an AnalysisModeSpec (or a dict with the same fields). The
        mode is discoverable at runtime and runs through WorkflowRunnerService.
        """
        from core.registries.analysis_mode_registry import AnalysisModeSpec
        from core.models.intelligence_models import ModelCallBudget
        registry = getattr(self._core, "analysis_mode_registry", None)
        if not registry:
            raise RuntimeError("Analysis mode registry is not available.")
        if isinstance(spec, dict):
            payload = dict(spec)
            if isinstance(payload.get("model_call_budget"), dict):
                payload["model_call_budget"] = ModelCallBudget(
                    **payload["model_call_budget"]
                )
            spec = AnalysisModeSpec(**payload)
        spec.plugin_id = self._plugin_id
        registry.register(spec)
        return spec.id

    def list_analysis_modes(self, mount_point: str = "") -> list:
        from dataclasses import asdict
        registry = getattr(self._core, "analysis_mode_registry", None)
        if not registry:
            return []
        modes = registry.for_mount(mount_point) if mount_point else registry.all()
        return [asdict(m) for m in modes]

    def run_analysis_mode(self, mode_id: str, params: Optional[dict] = None, target_id: str = "") -> dict:
        """Run a registered analysis mode via WorkflowRunnerService (dispatches RUN_BLUEPRINT)."""
        svc = getattr(self._core, "analysis_mode_service", None)
        if not svc:
            return {"error": "Analysis mode service unavailable"}
        return svc.run(mode_id, params or {}, target_id=target_id or None)

    def register_edge_type(self, edge_type: str, metadata: Optional[dict] = None) -> str:
        service = getattr(self._core, "artifact_service", None)
        if not service or not hasattr(service, "register_edge_type"):
            raise RuntimeError("Artifact edge type registry is not available.")
        qualified = f"{self._plugin_id}.{edge_type}" if ":" not in edge_type and "." not in edge_type else edge_type
        service.register_edge_type(qualified, metadata or {})
        return qualified


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

    def __init__(
        self,
        core: "PapyrusCore",
        plugin_id: str = "",
        network_hosts=(),
    ) -> None:
        self._core = core
        self._plugin_id = plugin_id
        # Tracks (signal, slot) pairs so we can disconnect on unload
        self._subscriptions: List[Tuple[Any, Callable]] = []
        # Tracks service IDs registered by this plugin so _cleanup() can remove them
        self._registered_service_ids: List[str] = []
        # Tracks workflow step types registered by this plugin for cleanup
        self._registered_step_types: List[str] = []
        self._registered_rag_source_ids: List[str] = []
        self._registered_retrieval_provider_ids: List[str] = []
        self._registered_artifact_producer_ids: List[str] = []
        # Lazy-initialised namespaces
        self._db_ns: Optional["_PluginDBNamespace"] = None
        self._tasks_ns: Optional["_PluginTaskNamespace"] = None
        self._sources_ns: Optional["_PluginSourceNamespace"] = None
        self._network_ns: Optional["_PluginNetworkNamespace"] = None
        self._storage_ns: Optional["_PluginStorageNamespace"] = None
        self._network_hosts = tuple(network_hosts)
        self._source_formats_ns: Optional["_PluginSourceFormatNamespace"] = None
        self._video_ns: Optional["_PluginVideoNamespace"] = None
        self._memory_ns: Optional["_PluginMemoryNamespace"] = None
        self._intelligence_ns: Optional["_PluginIntelligenceNamespace"] = None
        self._tags_ns: Optional["_PluginTagNamespace"] = None
        self._widgets_ns: Optional["_PluginWidgetNamespace"] = None
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
        """Run AI blueprints programmatically, including prompt-managed vision steps."""
        return self._core.workflow_runner_service

    @property
    def media_assets(self):
        """Persist and load project-contained binary assets by stable asset ID."""
        return self._core.media_asset_service

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

    @property
    def discovery_extractors(self) -> "DeterministicExtractorRegistry":
        """Register custom entity extractors for the Discovery tab.

        Usage::

            from core.extraction.deterministic import ExtractorSpec
            api.discovery_extractors.register(ExtractorSpec(
                entity_type="entity.court_case",
                display_name="Court Cases & Precedent",
                extractor=my_extractor_fn,
                plugin_id="my_plugin",
            ))

        Call ``api.discovery_extractors.remove_by_plugin("my_plugin")`` in
        ``on_unload()`` to clean up on hot-reload.
        """
        from core.extraction.deterministic import DeterministicExtractorRegistry
        return DeterministicExtractorRegistry

    @property
    def extraction(self):
        """Access the DocumentExtractionService to query shared extraction results.

        Usage::

            index = api.extraction.get_citation_index(doc_id)
            blocks = api.extraction.get_blocks(doc_id)
        """
        return getattr(self._core, "document_extraction_service", None)

    @property
    def pattern_detector_registry(self):
        """Register custom pattern packs for entity, citation, and date detection.

        Usage::

            from core.registries.pattern_detector_registry import PatternPack
            api.pattern_detector_registry.register(PatternPack(
                pack_id="my_plugin_citations",
                pack_name="My Domain Citations",
                entity_types=["citation"],
                raw_patterns=[r"\\bmy_pattern\\b"],
                plugin_id="my_plugin",
            ))
        """
        return getattr(self._core, "pattern_detector_registry", None)

    @property
    def table_extraction(self):
        """Access the TableExtractionService for canonical table objects.

        Usage::

            tables = api.table_extraction.get_tables(doc_id)
        """
        return getattr(self._core, "table_extraction_service", None)

    @property
    def ranking(self):
        """Access the RankingFeatureService to score and rank document blocks.

        Usage::

            ranked = api.ranking.rank_with_compute(blocks, preferred_features=["contains_in_text_citation"])
        """
        return getattr(self._core, "ranking_feature_service", None)

    @property
    def deterministic_pass_registry(self):
        """Register named deterministic (no-LLM) block ranking/filtering passes.

        Passes are resolved by pass_id from analysis-template ``deterministic_passes``
        fields and run by the RANK_BLOCKS step. Call ``remove_by_plugin(plugin_id)``
        on unload if your plugin registers passes.

        Usage::

            from core.registries.deterministic_pass_registry import DeterministicPassSpec
            api.deterministic_pass_registry.register(DeterministicPassSpec(
                pass_id="my_plugin_legal_citation_rank",
                pass_name="Legal Citation Density",
                description="Ranks blocks by legal-citation marker density.",
                run_fn=my_run_fn,
                plugin_id="my_plugin",
            ))
        """
        return getattr(self._core, "deterministic_pass_registry", None)

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
    def network(self) -> "_PluginNetworkNamespace":
        """Permissioned HTTPS requests restricted to declared public hosts."""
        if self._network_ns is None:
            self._network_ns = _PluginNetworkNamespace(
                self._core, self._plugin_id, self._network_hosts
            )
        return self._network_ns

    @property
    def storage(self) -> "_PluginStorageNamespace":
        """Plugin-private bounded-cache path helpers."""
        if self._storage_ns is None:
            self._storage_ns = _PluginStorageNamespace(self._core, self._plugin_id)
        return self._storage_ns

    @property
    def source_formats(self) -> "_PluginSourceFormatNamespace":
        """Register new ingestible source formats (see _PluginSourceFormatNamespace)."""
        if self._source_formats_ns is None:
            self._source_formats_ns = _PluginSourceFormatNamespace(self._core)
        return self._source_formats_ns

    @property
    def memory(self) -> "_PluginMemoryNamespace":
        """Central memory manager: register caches/models, reserve memory."""
        if self._memory_ns is None:
            self._memory_ns = _PluginMemoryNamespace(self._core, self._plugin_id)
        return self._memory_ns

    @property
    def video(self) -> "_PluginVideoNamespace":
        """Video transcript and transcription helpers."""
        if self._video_ns is None:
            self._video_ns = _PluginVideoNamespace(self._core)
        return self._video_ns

    @property
    def intelligence(self) -> "_PluginIntelligenceNamespace":
        """Source intelligence, artifact, and retrieval-provider helpers."""
        if self._intelligence_ns is None:
            self._intelligence_ns = _PluginIntelligenceNamespace(
                self._core,
                self._plugin_id,
                self._registered_retrieval_provider_ids,
                self._registered_artifact_producer_ids,
            )
        return self._intelligence_ns

    @property
    def tags(self) -> "_PluginTagNamespace":
        """Project tag helpers: ensure_tag, create, delete, rename, assign_to_doc, sync_tags, etc."""
        if self._tags_ns is None:
            self._tags_ns = _PluginTagNamespace(self._core)
        return self._tags_ns

    @property
    def widgets(self) -> "_PluginWidgetNamespace":
        """AI output widget registry helpers.

        Call ``api.widgets.register(AIWidgetSpec(...))`` to contribute a full
        widget type: LLM prompt instructions, payload parser, and optional GUI
        renderer — all in one call.  The widget will appear in the blueprint
        editor's format picker, its prompt will be editable in the prompt
        editor's 'By Widget' view, and any blueprint step that selects this
        widget will automatically receive the correct LLM instructions.
        """
        if self._widgets_ns is None:
            self._widgets_ns = _PluginWidgetNamespace(self._core, self._plugin_id)
        return self._widgets_ns

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

    def register_rag_source(self, source_id: str, query: Callable) -> str:
        """Contribute a plugin-owned database to every standard RAG search.

        The callback receives ``embedding_vector``, ``n_results``,
        ``allowed_docs``, ``tag_filters``, and ``tag_logic`` as keyword
        arguments. It may query any storage backend and must return a standard
        vector result payload containing nested ``ids``, ``documents``,
        ``metadatas``, and ``distances`` lists.
        """
        if not source_id or not callable(query):
            raise ValueError("A RAG source requires a non-empty id and callable query.")
        qualified_id = f"{self._plugin_id}:{source_id}"
        self._core.llm_manager.register_rag_source(qualified_id, query)
        if qualified_id not in self._registered_rag_source_ids:
            self._registered_rag_source_ids.append(qualified_id)
        return qualified_id

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
        # MainWindow consumes this registry at startup and on plugin_loaded.
        # Keep headless registration and hot reload on that one GUI-owned path.

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

    def register_reading_text_filter(self, spec: Any) -> None:
        """Register a reading-mode page filter owned by this plugin.

        The callback receives ``List[ReadingPage]`` and may return a replacement
        list. Exceptions are isolated by the reading mode pipeline.
        """
        spec.plugin_id = self._plugin_id
        self.gui_extensions.add_reading_text_filter(spec)

    def register_reading_token_transform(self, spec: Any) -> None:
        """Register a reading-mode token transform owned by this plugin.

        The callback receives ``List[ReadingToken]`` and may return a replacement
        list. Exceptions are isolated by the reading mode pipeline.
        """
        spec.plugin_id = self._plugin_id
        self.gui_extensions.add_reading_token_transform(spec)

    def register_data_grid_extractor(self, spec: Any) -> None:
        """Register a deterministic PDF/grid extractor owned by this plugin."""
        spec.plugin_id = self._plugin_id
        self._core.data_provider_registry.register_grid_extractor(spec)

    def register_data_mapping_strategy(self, spec: Any) -> None:
        """Register an optional grid mapping strategy owned by this plugin."""
        spec.plugin_id = self._plugin_id
        self._core.data_provider_registry.register_grid_mapping_strategy(spec)

    def register_data_chart_type(self, spec: Any) -> None:
        """Register a Data Dock chart adapter/renderer owned by this plugin."""
        spec.plugin_id = self._plugin_id
        self._core.data_provider_registry.register_chart_type(spec)

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

    def contribute_analysis_template(self, template: dict) -> None:
        """Register a plugin-contributed analysis template.

        The template appears in the Analysis Tab alongside built-in templates.
        It is held in-memory and disappears cleanly on plugin unload/hot-reload —
        nothing is written to the project file.

        Use the same dict format as ``DocumentDB._default_analysis_templates()``:
        ``id``, ``title``, ``instructions``, ``node_types``, ``relation_types``,
        ``chunk_prompt_key``, ``synthesis_prompt_key``, ``master_prompt_key``,
        ``limits``, etc.
        """
        import copy
        t = copy.deepcopy(template)
        t.setdefault("plugin_id", self._plugin_id)
        registry = getattr(self._core, "_plugin_analysis_templates", None)
        if registry is None:
            return
        # Idempotent: replace any prior registration of the same id by this plugin
        registry[:] = [
            x for x in registry
            if not (x.get("id") == t.get("id") and x.get("plugin_id") == self._plugin_id)
        ]
        registry.append(t)

    def contribute_rag_source_filter(self, source_id: str, label: str, description: str = "") -> None:
        """Register a plugin RAG source entry in the Global AI Settings filter dialog.

        The entry appears as a checkbox that lets users enable/disable this plugin's RAG
        contribution per-project. State is stored in project metadata.
        Disappears cleanly on plugin unload/hot-reload via extension_registry cleanup.
        """
        from core.plugins.extension_registry import RagSourceFilterSpec
        qualified_id = f"{self._plugin_id}:{source_id}"
        spec = RagSourceFilterSpec(
            source_id=qualified_id,
            label=label,
            description=description,
            plugin_id=self._plugin_id,
        )
        self.gui_extensions.add_rag_source_filter(spec)

    def _cleanup(self) -> None:
        """Disconnect all tracked subscriptions and remove registered services. Called on plugin unload."""
        if self._intelligence_ns is not None:
            from core.plugins.artifact_type_registration import cleanup
            cleanup(self._intelligence_ns)
        if self._memory_ns is not None:
            self._core.memory_manager_service.registry.remove_by_plugin(self._plugin_id)
        for signal, slot in self._subscriptions:
            try:
                signal.disconnect(slot)
            except RuntimeError:
                pass
        self._subscriptions.clear()
        for service_id in self._registered_service_ids:
            self._core._custom_services.pop(service_id, None)
        self._registered_service_ids.clear()
        for source_id in self._registered_rag_source_ids:
            self._core.llm_manager.unregister_rag_source(source_id)
        self._registered_rag_source_ids.clear()
        retrieval = getattr(self._core, "hybrid_retrieval_service", None)
        if retrieval is not None:
            for provider_id in self._registered_retrieval_provider_ids:
                retrieval.unregister_provider(provider_id)
                if hasattr(retrieval, "unregister_intent_provider"):
                    retrieval.unregister_intent_provider(provider_id)
        self._registered_retrieval_provider_ids.clear()
        artifact_service = getattr(self._core, "artifact_service", None)
        if artifact_service is not None:
            for producer_id in self._registered_artifact_producer_ids:
                if hasattr(artifact_service, "unregister_producer"):
                    artifact_service.unregister_producer(producer_id)
        self._registered_artifact_producer_ids.clear()
        # Remove plugin table registrations (does NOT drop the actual SQLite table)
        from core.plugins.plugin_db_registry import PluginTableRegistry
        PluginTableRegistry.get_instance().remove_by_plugin(self._plugin_id)
        # Kill any running background tasks
        if self._tasks_ns is not None:
            self._tasks_ns.kill_all()
        network_service = getattr(self._core, "network_access_service", None)
        if network_service is not None:
            network_service.close_plugin(self._plugin_id)
        # Unregister pack contributors
        reg = getattr(self._core, "pack_contributor_registry", None)
        if reg is not None:
            reg.remove_plugin(self._plugin_id)
        # Unregister plugin workflow step handlers
        from core.engine.master_runner import MasterActionRunner
        for st in self._registered_step_types:
            MasterActionRunner.unregister_plugin_step_handler(st)
        # Unregister plugin-contributed widget types
        if self._widgets_ns is not None:
            self._widgets_ns._cleanup()
        self._registered_step_types.clear()
        # Remove plugin help content
        hs = getattr(self._core, "help_service", None)
        if hs is not None:
            hs.help_registry.remove_plugin_topics(self._plugin_id)
            hs.tutorial_registry.remove_plugin_tutorials(self._plugin_id)
        data_registry = getattr(self._core, "data_provider_registry", None)
        if data_registry is not None and hasattr(data_registry, "remove_plugin"):
            data_registry.remove_plugin(self._plugin_id)
        # Remove plugin-contributed source formats (SourceFormatRegistry)
        source_format_registry = getattr(self._core, "source_format_registry", None)
        if source_format_registry is not None:
            source_format_registry.remove_by_plugin(self._plugin_id)
        # Remove plugin-contributed analysis templates
        plugin_templates = getattr(self._core, "_plugin_analysis_templates", None)
        if plugin_templates is not None:
            plugin_templates[:] = [t for t in plugin_templates if t.get("plugin_id") != self._plugin_id]
        # Stop the plugin's queued/running stages, then retire its producers
        # before the stage specs themselves are removed below.
        stage_registry = getattr(self._core, "intelligence_stage_registry", None)
        scheduler = getattr(self._core, "intelligence_scheduler", None)
        if stage_registry is not None and scheduler is not None:
            scheduler.cancel_stage_jobs(stage_registry.stages_owned_by(self._plugin_id))
        producers = getattr(self._core, "intelligence_producer_registry", None)
        if producers is not None:
            producers.remove_by_plugin(self._plugin_id)
        # Remove plugin-contributed entity extractors and analysis modes
        for reg_attr in (
            "entity_extractor_registry", "entity_evidence_registry", "analysis_mode_registry", "document_link_service",
            "intelligence_stage_registry", "intelligence_view_registry", "intelligence_review_registry",
        ):
            reg = getattr(self._core, reg_attr, None)
            if reg is not None and hasattr(reg, "remove_by_plugin"):
                reg.remove_by_plugin(self._plugin_id)
        from core.services.intelligence import connection_review, link_explanations
        link_explanations.remove_by_plugin(self._plugin_id)
        connection_review.remove_by_plugin(self._plugin_id)
