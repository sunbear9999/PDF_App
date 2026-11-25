import shiboken6
from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
import fitz
from core.events.domains.metadata_events import NotesEvent, NotesEventPayload, NotesIntent, NotesPayload
from core.events.domains.document_events import AnnotationIntent, AnnotationPayload, DocumentEvent, DocumentEventPayload
from core.events.domains.workspace_events import WorkspaceIntent, WorkspacePayload
class NotesFetchWorker(QThread):
    notes_ready = Signal(list)
    notes_failed = Signal(str)

    def __init__(self, pm, scope, tag_name, active_pdf, parent=None, *, cursor="", limit=25):
        super().__init__(parent)
        self.pm = pm
        self.cursor, self.limit, self.snapshot = cursor, limit, {}
        self.scope = scope
        self.tag_name = tag_name
        self.active_pdf = active_pdf
        self.project_connection = getattr(pm, "_conn", None)

    def run(self):
        from types import SimpleNamespace
        from core.services.content.note_queries import query
        from contextlib import nullcontext
        try:
            if self.project_connection is not getattr(self.pm, '_conn', None):
                return
            if self.scope in {'Current PDF', 'Current Document'} and not self.active_pdf:
                self.notes_ready.emit([])
                return
            adapter = SimpleNamespace(project_manager=self.pm, db=self.pm.db_intelligence)
            with adapter.db._write_lock, getattr(self.pm, 'db_lock', nullcontext()):
                if self.project_connection is not getattr(self.pm, '_conn', None):
                    return
                self.snapshot = query(adapter, source_path=self.active_pdf if self.scope in {'Current PDF','Current Document'} else '',
                    tag=self.tag_name or '', cursor=self.cursor, limit=self.limit, cancelled=self.isInterruptionRequested)
            if self.project_connection is getattr(self.pm, '_conn', None) and not self.isInterruptionRequested():
                self.notes_ready.emit(self.snapshot['items'])
        except Exception as exc:
            self.notes_failed.emit(str(exc))


class NotesAppService(QObject):
    def __init__(self, project_manager):
        super().__init__()
        self.pm = project_manager
        self.bus = EventBus.get_instance()
        self.bus.notes_action_requested.connect(self._handle_intent)
        self.worker = None
        self._pending_fetch = None
        self.bus.project_clearing_started.connect(self._clear_pending)

    def _handle_intent(self, intent: NotesIntent, payload: NotesPayload):
        if intent == NotesIntent.FETCH:
            self._fetch_notes(payload)
        elif intent == NotesIntent.DELETE:
            self._modify_note(payload, action="delete")
        elif intent == NotesIntent.CHANGE_COLOR:
            self._modify_note(payload, action="color")
        elif intent == NotesIntent.SYNC_TAGS:
            # Tell the Workspace Service to update its nodes
            self.bus.workspace_action_requested.emit(WorkspaceIntent.SYNC_TAGS_FROM_ANNOT, WorkspacePayload(annot_id=payload.get("annot_id")))
            self.pm.mark_dirty("workspace")
            # Refresh the notes list
            self._fetch_notes(NotesPayload(scope="Entire Project", tag=None, active_pdf=None))

    def _fetch_notes(self, payload: NotesPayload):
        if self.worker and shiboken6.isValid(self.worker) and self.worker.isRunning():
            self._pending_fetch = (payload, getattr(self.pm, "_conn", None))
            return

        self.worker = NotesFetchWorker(
            self.pm,
            payload.get("scope", "Current PDF"),
            payload.get("tag"),
            payload.get("active_pdf"), cursor=payload.cursor, limit=payload.limit
        )
        self.worker.notes_ready.connect(lambda data, worker=self.worker: self._deliver_notes(worker, data))
        self.worker.notes_failed.connect(
            lambda message: self.bus.status_message_requested.emit(f"Notes failed to load: {message}", 7000)
        )
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.finished.connect(self._fetch_finished)
        self.worker.start()

    def _deliver_notes(self, worker, data):
        if worker.project_connection is getattr(self.pm, "_conn", None) and not worker.isInterruptionRequested():
            self.bus.notes_data_ready.emit(NotesEvent.DATA_READY, NotesEventPayload(notes=data, next_cursor=worker.snapshot.get("next_cursor", ""), diagnostics=worker.snapshot.get("diagnostics") or []))

    def _fetch_finished(self):
        if self.worker is self.sender():
            self.worker = None
        if self._pending_fetch is not None:
            (payload, connection), self._pending_fetch = self._pending_fetch, None
            if connection is getattr(self.pm, "_conn", None):
                self._fetch_notes(payload)

    def _clear_pending(self, *_):
        self._pending_fetch = None
        if self.worker and shiboken6.isValid(self.worker) and self.worker.isRunning():
            self.worker.requestInterruption()

    def _modify_note(self, payload: NotesPayload, action: str):
        self.bus.status_message_requested.emit(
            'Use guarded project-note actions. Physical or unlocated legacy notes must be adopted or reselected before editing; the source stays unchanged.', 7000)
