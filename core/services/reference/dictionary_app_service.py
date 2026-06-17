from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
from core.models.dictionary_models import DictionaryDefinitionGroup
from core.events.domains.tool_events import DictionaryEvent, DictionaryEventPayload, DictionaryIntent, DictionaryPayload

class DictSearchWorker(QThread):
    results_ready = Signal(list)
    def __init__(self, dm, query, dict_id, fuzzy, parent=None):
        super().__init__(parent)
        self.dm = dm
        self.query = query
        self.dict_id = dict_id
        self.fuzzy = fuzzy

    def run(self):
        results = self.dm.exact_search(self.query, self.dict_id, self.fuzzy)
        self.results_ready.emit(DictionaryAppService.group_results(results))

class DictionaryAppService(QObject):
    def __init__(self, dictionary_manager):
        super().__init__()
        self.dm = dictionary_manager
        self.bus = EventBus.get_instance()
        self.bus.dictionary_action_requested.connect(self._handle_intent)
        self.worker = None

    @staticmethod
    def group_results(results: list) -> list:
        grouped = {}
        for result in results or []:
            word = str(result.get("word", "")).upper()
            if not word:
                continue
            if word not in grouped:
                grouped[word] = DictionaryDefinitionGroup(word=word)
            source = result.get("dictionary")
            if source and source not in grouped[word].sources:
                grouped[word].sources.append(source)
            definition = str(result.get("definition", ""))
            bullets = [part.strip() for part in definition.replace("<br>", "\n").split("\n") if part.strip()]
            grouped[word].definitions.extend(bullets)
        return [group.as_dict() for group in grouped.values()]

    def _handle_intent(self, intent: DictionaryIntent, payload: DictionaryPayload):
        if intent == DictionaryIntent.FETCH_DICTS:
            dicts = self.dm.get_available_dictionaries()
            self.bus.dictionary_status_updated.emit(DictionaryEvent.DICTS_LOADED, DictionaryEventPayload(data=dicts))

        elif intent == DictionaryIntent.PUBLIC_SEARCH:
            query = (payload.get("query") or "").strip()
            if query:
                self.bus.dictionary_status_updated.emit(DictionaryEvent.PUBLIC_SEARCH, DictionaryEventPayload(query=query))
                self._handle_intent(DictionaryIntent.SEARCH, DictionaryPayload(query=query, dict_id="ALL", fuzzy=True))

        elif intent == DictionaryIntent.SEARCH:
            if self.worker and self.worker.isRunning(): return
            self.worker = DictSearchWorker(self.dm, payload.get("query"), payload.get("dict_id"), payload.get("fuzzy"))
            self.worker.results_ready.connect(
                lambda res: self.bus.dictionary_results_ready.emit(
                    DictionaryEvent.RESULTS_READY,
                    DictionaryEventPayload(results=res),
                )
            )
            self.worker.start()

        elif intent == DictionaryIntent.ADD_WORD:
            success = self.dm.add_custom_entry(payload["dict_id"], payload["word"], payload["definition"])
            if success:
                self.bus.dictionary_status_updated.emit(DictionaryEvent.WORD_ADDED, DictionaryEventPayload(word=payload["word"]))
            else:
                self.bus.dictionary_status_updated.emit(DictionaryEvent.ERROR, DictionaryEventPayload(msg="Database write failed."))

        elif intent == DictionaryIntent.IMPORT:
            # For massive imports, wrap this in a QThread in production.
            # SQLite is fast enough that it usually doesn't block for long, but it's safer.
            ext = payload["ext"]
            path = payload["path"]
            success = False

            if ext == 'json': success = self.dm.import_json(path)
            elif ext == 'csv': success = self.dm.import_csv(path)
            elif ext == 'xdxf': success = self.dm.import_xdxf(path)
            elif ext == 'ifo': success = self.dm.import_stardict(path)

            if success:
                self.bus.dictionary_status_updated.emit(DictionaryEvent.IMPORT_SUCCESS, DictionaryEventPayload())
                self._handle_intent(DictionaryIntent.FETCH_DICTS, DictionaryPayload()) # Refresh dropdown
            else:
                self.bus.dictionary_status_updated.emit(DictionaryEvent.ERROR, DictionaryEventPayload(msg="Import failed to parse."))
