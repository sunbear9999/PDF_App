import os
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
        self._source_intelligence_service = None
        self.bus.index_action_requested.connect(self._handle_intent)

    def set_source_intelligence_service(self, service) -> None:
        self._source_intelligence_service = service

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

        status = self.status_snapshot()
        self.bus.index_status_ready.emit(
            IndexEvent.STATUS_READY,
            IndexEventPayload(
                is_indexed=bool(status["is_indexed"]),
                doc_count=int(status["indexed_sources"]),
                progress_msg=status["message"],
            ),
        )

    def resolve_sources(self, references=None):
        """Resolve IDs, full paths, basenames, and display titles once."""
        sources = list(self.pm.list_sources(include_removed=False))
        if not references:
            return sources
        refs = {
            str(value).strip() for value in references or []
            if str(value).strip()
        }
        normalized_paths = {
            os.path.normcase(os.path.abspath(os.path.expanduser(value)))
            for value in refs
        }
        lowered = {
            alias.casefold()
            for value in refs
            for alias in (value, os.path.basename(value))
        }
        matched = []
        for source in sources:
            path = str(source.get("path") or "")
            aliases = {
                str(source.get("id") or "").casefold(),
                path.casefold(),
                os.path.basename(path).casefold(),
                str(source.get("title") or "").casefold(),
            }
            normalized = (
                os.path.normcase(os.path.abspath(os.path.expanduser(path)))
                if path else ""
            )
            if aliases.intersection(lowered) or normalized in normalized_paths:
                matched.append(source)
        return matched

    def status_snapshot(self, references=None):
        """Return canonical per-source vector status, not a global chunk count."""
        sources = self.resolve_sources(references)
        db = getattr(self.pm, "db_intelligence", None)
        indexed = []
        pending = []
        failed = []
        for source in sources:
            source_id = str(source.get("id") or "")
            status = (
                (db.get_stage_statuses(source_id).get("embeddings") or {})
                if db and source_id else {}
            )
            vector_count = (
                db.count_artifact_embeddings(source_id)
                if db and hasattr(db, "count_artifact_embeddings") else 0
            )
            if status.get("status") == "complete" and vector_count > 0:
                indexed.append(source_id)
            elif status.get("status") == "failed":
                failed.append(source_id)
            else:
                pending.append(source_id)
        total = len(sources)
        count = len(indexed)
        if total and count == total:
            message = f"Indexed {count}/{total} source(s)"
        elif total:
            message = f"Indexed {count}/{total} source(s)"
        else:
            message = "No project sources found"
        return {
            "is_indexed": bool(total and count == total),
            "indexed_sources": count,
            "total_sources": total,
            "pending_source_ids": pending,
            "failed_source_ids": failed,
            "message": message,
        }

    def queue_indexing(self, references=None):
        if self._source_intelligence_service is None:
            return {"queued": 0, "matched": 0, "error": "Intelligence service unavailable."}
        sources = self.resolve_sources(references)
        db = getattr(self.pm, "db_intelligence", None)
        jobs = []
        already_indexed = 0
        for source in sources:
            source_id = source.get("id")
            if source_id:
                vector_count = (
                    db.count_artifact_embeddings(source_id)
                    if db and hasattr(db, "count_artifact_embeddings") else 0
                )
                status = (
                    (db.get_stage_statuses(source_id).get("embeddings") or {})
                    if db else {}
                )
                if status.get("status") == "complete" and vector_count > 0:
                    already_indexed += 1
                    continue
                # Older builds could mark this stage complete without writing a
                # vector. Force a real retry instead of treating that row as fresh.
                if db and status.get("status") == "complete" and vector_count == 0:
                    db.mark_source_stages_status(
                        source_id, "stale", stages=["embeddings"]
                    )
                job = self._source_intelligence_service.run_stage(
                    source_id, "embeddings"
                )
                if job:
                    jobs.append(job)
        return {
            "queued": len(jobs),
            "matched": len(sources),
            "already_indexed": already_indexed,
            "error": "",
        }

    def _run_indexing(self, pdf_paths=None):
        if self._source_intelligence_service is not None:
            result = self.queue_indexing(pdf_paths)
            if result["matched"] == 0:
                self.bus.index_status_ready.emit(
                    IndexEvent.FAILED,
                    IndexEventPayload(
                        error="No project sources matched the selected documents."
                    ),
                )
                return
            self.bus.index_status_ready.emit(
                IndexEvent.PROGRESS,
                IndexEventPayload(
                    progress_msg=(
                        f"Queued {result['queued']} source(s); "
                        f"{result.get('already_indexed', 0)} already indexed."
                    )
                ),
            )
            return
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
