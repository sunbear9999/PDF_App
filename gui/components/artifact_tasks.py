"""Cancellable background artifact operations shared by inspectors and queues."""
import uuid
import json
from contextlib import nullcontext

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtWidgets import QApplication


class _ArtifactTask(QThread):
    ready = Signal(str)
    failed = Signal(str)

    def __init__(self, service, operation, arguments, generation, parent=None):
        super().__init__(parent)
        self.service, self.operation, self.arguments = service, operation, arguments
        self.generation = generation
        self.token, self.result = str(uuid.uuid4()), None
        db = getattr(service, "db", None)
        self.project = (id(db), id(getattr(db, "_conn", None)))

    def run(self):
        try:
            if self.operation in {'format_record', 'decode_image'}:
                self.result = self._projection()
                if not self.isInterruptionRequested():
                    self.ready.emit(self.token)
                return
            pm = self.service.project_manager
            lock = getattr(pm, "db_lock", None)
            db = self.service.db
            independent = getattr(self.service, 'manages_project_locks', False)
            if db is None and not getattr(self.service, 'allow_without_project', False):
                raise ValueError("Open a project first.")
            with db._write_lock if db is not None and not independent else nullcontext():
                with lock if lock is not None and not independent else nullcontext():
                    if self.isInterruptionRequested() or self.project != (id(self.service.db), id(getattr(self.service.db, '_conn', None))):
                        return
                    arguments = dict(self.arguments)
                    if self.operation in getattr(self.service, 'cancellable_operations', ()):
                        arguments['cancel_check'] = self.isInterruptionRequested
                    if self.operation in {"review_artifacts", "query_notes", "query_timeline", "query_entity_network", "query_workspace_inbox"}:
                        arguments["cancelled"] = self.isInterruptionRequested
                    if self.operation == 'source_view_page':
                        from gui.components.source_page_projection import text_page
                        cursor, spans = arguments.pop('cursor', ''), arguments.pop('spans', None)
                        self.result = text_page(self.service.selection_source_snapshot(**arguments), cursor, spans)
                    else:
                        self.result = getattr(self.service, self.operation)(**arguments)
            if not self.isInterruptionRequested():
                self.ready.emit(self.token)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _projection(self):
        if self.operation == 'format_record':
            return json.dumps(self.arguments['record'], ensure_ascii=False, indent=2, default=str)
        from gui.components.source_page_projection import decode_image
        return decode_image(self.arguments['path'])


class ArtifactTaskRunner(QObject):
    completed = Signal(str)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, app_context, parent=None, *, service_name="artifact_service"):
        super().__init__(parent)
        self.ctx = app_context
        self._service_name = service_name
        self._workers, self._generation = [], 0
        self.result = None
        self._closing = False
        self._pending = None
        bus = getattr(app_context, "bus", None)
        self._closing_signal = getattr(bus, "project_clearing_started", None)
        if self._closing_signal is not None:
            self._closing_signal.connect(self.cancel)

    @property
    def busy(self):
        return bool(self._workers)

    def start(self, operation, **arguments):
        service = getattr(self.ctx, self._service_name, None)
        if self._closing or (service is None and operation not in {'format_record', 'decode_image'}):
            return False
        if self._workers:
            self.cancel()
            self._pending = (operation, arguments)
            return True
        worker = _ArtifactTask(service, operation, arguments, self._generation, QApplication.instance())
        worker.ready.connect(self._complete, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self._workers.append(worker)
        self.busy_changed.emit(True)
        worker.start()
        return True

    def _complete(self, token):
        worker = self.sender()
        if self._closing or worker.generation != self._generation or token != worker.token:
            return
        db = getattr(worker.service, "db", None)
        if worker.project != (id(db), id(getattr(db, "_conn", None))):
            return
        self.result, worker.result = worker.result, None
        self.completed.emit(worker.operation)

    def _failed(self, error):
        worker = self.sender()
        if not self._closing and worker.generation == self._generation:
            self.failed.emit(error)

    def _finished(self):
        worker = self.sender()
        if worker in self._workers:
            self._workers.remove(worker)
        if not self._workers and self._pending and not self._closing:
            operation, arguments = self._pending
            self._pending = None
            self.start(operation, **arguments)
        else:
            self.busy_changed.emit(bool(self._workers))

    def cancel(self, *_):
        self._generation += 1
        for worker in self._workers:
            worker.requestInterruption()
        self.result = None
        self._pending = None

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.cancel()
        if self._closing_signal is not None:
            try:
                self._closing_signal.disconnect(self.cancel)
            except (TypeError,RuntimeError):
                pass  # The application bus may already be gone during teardown.
            self._closing_signal=None
