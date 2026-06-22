import shiboken6
from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
from core.events.domains.tool_events import (
    IndexIntent, IndexEvent, IndexPayload, IndexEventPayload,
)


class _IndexWorker(QThread):
    progress = Signal(str)
    finished_indexing = Signal(bool, str)

    def __init__(self, llm_manager, filepaths, parent=None):
        super().__init__(parent)
        self.llm = llm_manager
        self.filepaths = filepaths

    def run(self):
        try:
            self.llm.index_documents(
                self.filepaths,
                progress_callback=lambda msg: self.progress.emit(msg),
            )
            self.finished_indexing.emit(True, "")
        except Exception as e:
            self.finished_indexing.emit(False, str(e))


class IndexAppService(QObject):
    """
    Owns all document-indexing and index-status logic.
    Absorbed from UnifiedResearchDock.check_index_status() / start_indexing().
    """

    def __init__(self, llm_manager, project_manager, parent=None):
        super().__init__(parent)
        self.llm = llm_manager
        self.pm = project_manager
        self.bus = EventBus.get_instance()
        self._worker: "_IndexWorker | None" = None
        self.bus.index_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent: IndexIntent, payload: IndexPayload):
        if isinstance(payload, dict):
            payload = IndexPayload(**payload)
        if intent == IndexIntent.CHECK_STATUS:
            self._check_status()
        elif intent == IndexIntent.RUN_INDEXING:
            self._run_indexing(payload.pdf_paths)

    def _check_status(self):
        proj_path = self.pm.project_filepath
        if not proj_path or not self.llm:
            self.bus.index_status_ready.emit(
                IndexEvent.STATUS_READY,
                IndexEventPayload(is_indexed=False),
            )
            return

        if self.llm.collection is None:
            self.llm.set_project_database(proj_path)

        try:
            count = self.llm.collection.count() if self.llm.collection else 0
            self.bus.index_status_ready.emit(
                IndexEvent.STATUS_READY,
                IndexEventPayload(is_indexed=count > 0, doc_count=count),
            )
        except Exception:
            self.bus.index_status_ready.emit(
                IndexEvent.STATUS_READY,
                IndexEventPayload(is_indexed=False),
            )

    def _run_indexing(self, pdf_paths=None):
        if self._worker and self._worker.isRunning():
            return
        if pdf_paths:
            paths = pdf_paths
        elif hasattr(self.pm, "list_sources"):
            paths = [s.get("path") for s in self.pm.list_sources("pdf") if s.get("path")]
        else:
            paths = self.pm.pdfs
        if not paths:
            self.bus.index_status_ready.emit(
                IndexEvent.FAILED,
                IndexEventPayload(error="No PDFs available to index."),
            )
            return

        self.bus.index_status_ready.emit(
            IndexEvent.PROGRESS,
            IndexEventPayload(progress_msg="Starting indexing..."),
        )
        self._worker = _IndexWorker(self.llm, paths, parent=self)
        self._worker.progress.connect(
            lambda msg: self.bus.index_status_ready.emit(
                IndexEvent.PROGRESS,
                IndexEventPayload(progress_msg=msg),
            )
        )
        self._worker.finished_indexing.connect(self._on_worker_finished)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_worker_finished(self, success: bool, error_msg: str):
        if success:
            if hasattr(self.pm, "_sync_doc_tags_for_llm"):
                for pdf_path in getattr(self.pm, "pdfs", []):
                    self.pm._sync_doc_tags_for_llm(pdf_path)
                if hasattr(self.pm, "list_sources"):
                    for source in self.pm.list_sources("video"):
                        self.pm._sync_doc_tags_for_llm(source.get("path"))
            self._check_status()
            self.bus.index_status_ready.emit(
                IndexEvent.COMPLETE,
                IndexEventPayload(is_indexed=True),
            )
        else:
            self.bus.index_status_ready.emit(
                IndexEvent.FAILED,
                IndexEventPayload(error=error_msg),
            )
