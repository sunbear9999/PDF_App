from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Protocol, runtime_checkable

import shiboken6
from PySide6.QtCore import QObject, QThread, Signal

from core.events.event_bus import EventBus
from core.events.domains.tool_events import (
    CitationEvent, CitationEventPayload, CitationIntent, CitationPayload,
)

if TYPE_CHECKING:
    from core.project_manager import ProjectManager
    from core.citation_manager import CitationManager


@runtime_checkable
class CitationProvider(Protocol):
    """
    Protocol that plugins implement to contribute citation entries.

    Registered via ``api.citation_service.register_provider(id, provider)``.
    Entries are merged with PDF-extracted citations on every REFRESH_TABLE.
    """

    def get_entries(self) -> List[dict]: ...
    def get_entry(self, doc_id: str) -> dict | None: ...
    def on_project_open(self, filepath: str) -> None: ...
    def on_project_close(self) -> None: ...


class ExtractionWorker(QThread):
    finished_extraction = Signal(list)

    def __init__(self, pm: "ProjectManager", cm: "CitationManager", parent=None):
        super().__init__(parent)
        self.pm = pm
        self.cm = cm

    def run(self):
        data_list = []
        for doc_path in self.pm.pdfs:
            data = self.pm.get_citation(doc_path)
            if not data or not data.get("title"):
                data = self.cm.extract_metadata(doc_path)
                self.pm.upsert_citation(data)
            data_list.append((doc_path, data))
        self.finished_extraction.emit(data_list)


class CitationAppService(QObject):
    def __init__(self, project_manager: "ProjectManager", citation_manager: "CitationManager"):
        super().__init__()
        self.pm = project_manager
        self.cm = citation_manager
        self.bus = EventBus.get_instance()
        self.bus.citation_action_requested.connect(self._handle_intent)
        self.bus.pdf_removed.connect(self._on_pdf_removed)
        self.worker = None
        self._providers: Dict[str, CitationProvider] = {}

    # ------------------------------------------------------------------
    # Provider registration
    # ------------------------------------------------------------------

    def register_provider(self, provider_id: str, provider: CitationProvider) -> None:
        """Register a citation provider contributed by a plugin."""
        self._providers[provider_id] = provider

    # ------------------------------------------------------------------
    # Intent handling
    # ------------------------------------------------------------------

    def _on_pdf_removed(self, event, payload):
        path = getattr(payload, "path", None)
        if path:
            self.pm.delete_citation(path)
            self._run_extraction()

    def _handle_intent(self, intent: CitationIntent, payload: CitationPayload):
        if intent == CitationIntent.REFRESH_TABLE:
            self._run_extraction()
        elif intent == CitationIntent.UPDATE_ENTRY:
            data = payload.get("data") or {}
            doc_id = data.get("doc_id", "")
            if not self._provider_entry(str(doc_id)) or doc_id in getattr(self.pm, "pdfs", []):
                self.pm.upsert_citation(data)
        elif intent == CitationIntent.GENERATE_WORKS_CITED:
            self._generate_works_cited(payload)

    def _run_extraction(self):
        if self.worker and shiboken6.isValid(self.worker) and self.worker.isRunning():
            return
        self.worker = ExtractionWorker(self.pm, self.cm)
        self.worker.finished_extraction.connect(self._on_extraction_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _on_extraction_finished(self, data: list):
        # Merge entries from all registered providers
        for provider_id, provider in self._providers.items():
            try:
                for entry in provider.get_entries():
                    data.append((None, entry))
            except Exception as exc:
                print(f"[CitationAppService] Provider '{provider_id}' error: {exc}")
        self.bus.citation_table_data_ready.emit(
            CitationEvent.TABLE_DATA_READY,
            CitationEventPayload(data=data),
        )

    def _generate_works_cited(self, payload: CitationPayload):
        style = payload.get("style", "APA")
        self.cm.set_style(style)
        all_doc_ids = payload.get("doc_ids") or []

        works = []
        for doc_id in all_doc_ids:
            if not doc_id:
                continue
            entry = self._provider_entry(str(doc_id))
            if entry and doc_id not in getattr(self.pm, "pdfs", []):
                formatted = self.cm.format_entry(entry)
            else:
                formatted = self.cm.format_entry(self.pm.get_citation(doc_id))
            if formatted:
                works.append(formatted)

        works = sorted(works)
        formatted_text = f"Works Cited ({style})\n\n" + "\n\n".join(works)
        self.bus.citation_status_updated.emit(
            CitationEvent.WORKS_CITED_GENERATED,
            CitationEventPayload(works=works, formatted_text=formatted_text),
        )

    def _provider_entry(self, doc_id: str) -> dict | None:
        for provider in self._providers.values():
            try:
                entry = provider.get_entry(doc_id)
                if entry:
                    return entry
            except Exception:
                continue
        return None
