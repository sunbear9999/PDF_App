"""Historical Archives — first-party public archive research plugin."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.plugins.papyrus_api import PapyrusAPI


class Plugin:
    plugin_id = "historical_archives"
    name = "Historical Archives"
    version = "1.0.0"
    dependencies: list = []
    requires_internet = True
    network_hosts = ["loc.gov", "archive.org"]
    settings_schema = [
        {
            "key": "max_results",
            "label": "Maximum results per provider",
            "type": "int",
            "default": 25,
        },
        {
            "key": "request_timeout",
            "label": "Request timeout (seconds)",
            "type": "int",
            "default": 20,
        },
        {
            "key": "cache_hours",
            "label": "Metadata cache duration (hours)",
            "type": "int",
            "default": 24,
        },
        {
            "key": "max_download_mb",
            "label": "Maximum source download (MB)",
            "type": "int",
            "default": 250,
        },
        {
            "key": "ocr_fuzziness",
            "label": "OCR matching tolerance",
            "type": "float",
            "default": 0.82,
        },
    ]

    def __init__(self) -> None:
        self._api: Optional["PapyrusAPI"] = None
        self._cache = None
        self._repository = None
        self._coordinator = None
        self._importer = None
        self._dock = None
        self._app_context = None

    def on_load(self, api: "PapyrusAPI") -> None:
        from .persistence.repository import HistoricalArchiveRepository
        from .persistence.pack_contributor import HistoricalSearchPackContributor
        from .providers.iiif import IIIFManifestProvider
        from .providers.internet_archive import InternetArchiveProvider
        from .providers.loc import LibraryOfCongressProvider
        from .services.cache import CachedNetworkClient, ResponseCache
        from .services.coordinator import FederatedSearchCoordinator
        from .services.importer import ArchiveImportCoordinator

        self._api = api
        HistoricalArchiveRepository.register_tables(api)
        self._repository = HistoricalArchiveRepository(api)
        api.register_pack_contributor(HistoricalSearchPackContributor(self._repository))
        cache_path = f"{api.storage.cache_dir('metadata')}/responses.sqlite3"
        self._cache = ResponseCache(
            cache_path,
            ttl_seconds=int(api.config.get("cache_hours", 24)) * 3600,
        )
        client = CachedNetworkClient(api.network, self._cache)
        providers = [
            LibraryOfCongressProvider(),
            InternetArchiveProvider(),
            IIIFManifestProvider(),
        ]
        self._coordinator = FederatedSearchCoordinator(providers, client)
        self._importer = ArchiveImportCoordinator(
            api,
            {provider.provider_id: provider for provider in providers},
            client,
            self._repository,
        )
        api.register_service("historical_archives.search", self._coordinator)
        api.register_service("historical_archives.import", self._importer)
        self._register_help(api)
        api.register_shortcut(
            "focus_search",
            "Focus Historical Archives Search",
            self._focus_search,
            "",
            "Open Historical Archives and focus its search field.",
        )

    def _register_help(self, api):
        from core.help.models import HelpTopic

        api.help_registry.register(HelpTopic(
            id="plugin.historical_archives.search",
            title="Search Historical Archives",
            summary="Search public archives, inspect OCR and rights, and import sources.",
            body=(
                "Open **Historical Archives** from the View menu. Enter a simple "
                "query, Boolean expression, or natural request such as "
                "`Colorado newspapers between 1900 and 1910`. Advanced Search "
                "adapts to the selected providers and offers exact phrase, "
                "AND/OR/NOT, dates, places, collections, title, creator, subject, "
                "language, and material type where supported. Papyrus shows every "
                "generated query variant before searching; uncheck any variant "
                "you do not want sent.\n\n"
                "Search controls stay on the **Search** tab; searching opens "
                "**Results and Import**, with a large result table and independently "
                "resizable scan, OCR, and metadata previews. **Open Original** "
                "visits the archive record. **Import to Project** downloads the "
                "selected source into project-owned storage, verifies it, records "
                "provenance and citation data, and adds any available OCR through "
                "the normal extraction pipeline. Progress, failures, and retry "
                "remain visible beside the import action.\n\n"
                "Internet access is disabled until explicitly granted in Plugin "
                "Settings, Plugin Manager, or the dock banner. Generic IIIF "
                "manifests may use the public HTTPS "
                "host shown in the pasted URL; private and local hosts are blocked."
            ),
            category="Plugins",
            keywords=[
                "archives", "historical", "newspapers", "OCR", "IIIF",
                "Library of Congress", "Internet Archive", "sources",
            ],
            feature_id="plugin.historical_archives.dock",
            namespace="plugin",
            plugin_id=self.plugin_id,
        ))

    def register_gui_extensions(self, registry) -> None:
        from core.plugins.extension_registry import DockSpec

        registry.add_dock(DockSpec(
            id="historical_archives_dock",
            label="Historical Archives",
            menu_name="Historical Archives",
            area="right",
            is_singleton=True,
            factory=self._make_dock,
            plugin_id=self.plugin_id,
        ))

    def _make_dock(self, app_context):
        from .ui.dock import HistoricalArchivesDock

        self._app_context = app_context
        setattr(app_context, "plugin_historical_archives", {
            "api": self._api,
            "coordinator": self._coordinator,
            "importer": self._importer,
            "repository": self._repository,
            "cache": self._cache,
        })
        self._dock = HistoricalArchivesDock(app_context)
        return self._dock

    def _focus_search(self) -> None:
        manager = getattr(self._app_context, "dock_manager", None)
        if manager:
            manager.spawn("historical_archives_dock")
        if self._dock:
            self._dock.focus_search()

    def on_project_open(self, filepath: str) -> None:
        if self._dock:
            self._dock._refresh_saved()

    def on_project_close(self) -> None:
        pass

    def on_unload(self) -> None:
        if self._api:
            self._api.tasks.kill_all()
        if self._cache:
            self._cache.close()
        if self._app_context and hasattr(self._app_context, "plugin_historical_archives"):
            delattr(self._app_context, "plugin_historical_archives")
        self._dock = None
        self._app_context = None

    def on_register(self, api) -> None:
        pass

    def register_blueprints(self, registry) -> None:
        pass

    def register_workspace_tools(self, registry) -> None:
        pass

    def register_ontology_types(self, registry) -> None:
        pass

    def get_dock_spec(self):
        return None
