from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
from core.events.domains.tool_events import CitationEvent, CitationEventPayload, CitationIntent, CitationPayload

class ExtractionWorker(QThread):
    finished_extraction = Signal(list)
    
    def __init__(self, pm, cm, parent=None):
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
    def __init__(self, project_manager, citation_manager):
        super().__init__()
        self.pm = project_manager
        self.cm = citation_manager
        self.bus = EventBus.get_instance()
        self.bus.citation_action_requested.connect(self._handle_intent)
        self.worker = None

    def _handle_intent(self, intent: CitationIntent, payload: CitationPayload):
        if intent == CitationIntent.REFRESH_TABLE:
            self._run_extraction()
        elif intent == CitationIntent.UPDATE_ENTRY:
            self.pm.upsert_citation(payload.get("data") or {})
        elif intent == CitationIntent.GENERATE_WORKS_CITED:
            self.cm.set_style(payload.get("style", "APA"))
            works = self.cm.format_works_cited(payload.get("doc_ids", []))
            self.bus.citation_status_updated.emit(
                CitationEvent.WORKS_CITED_GENERATED,
                CitationEventPayload(works=works),
            )

    def _run_extraction(self):
        if self.worker and self.worker.isRunning(): return
        self.worker = ExtractionWorker(self.pm, self.cm)
        self.worker.finished_extraction.connect(
            lambda data: self.bus.citation_table_data_ready.emit(
                CitationEvent.TABLE_DATA_READY,
                CitationEventPayload(data=data),
            )
        )
        self.worker.start()
