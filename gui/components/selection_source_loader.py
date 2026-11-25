"""One cancellable worker reader per text/image source viewer."""
from PySide6.QtCore import QObject
from gui.components.artifact_tasks import ArtifactTaskRunner


class SelectionSourceLoader(QObject):
    def __init__(self, app_context, parent, loaded, failed, cleared):
        super().__init__(parent)
        self.path = None
        self.loaded, self.failed, self.cleared = loaded, failed, cleared
        self.runner = ArtifactTaskRunner(app_context, self, service_name='reference_note_service')
        self.runner.completed.connect(self._completed)
        self.runner.failed.connect(self._failed)
        parent.destroyed.connect(self.runner.shutdown)
        bus = getattr(app_context, 'bus', None)
        if bus is not None:
            bus.project_clearing_started.connect(self.clear)

    def load(self, path, block_ids=None, *, text_page=False, cursor='', spans=None):
        self.path = path
        arguments = {'source_path': path, 'block_ids': block_ids}
        if text_page:
            arguments.update(cursor=cursor, spans=spans)
        operation = 'source_view_page' if text_page else 'selection_source_snapshot'
        if not self.runner.start(operation, **arguments):
            self.failed('Open a project and run source extraction to view selectable text.')

    def _completed(self, operation):
        snapshot = self.runner.result or {}
        if self.path and snapshot.get('source_path') == self.path:
            self.loaded(snapshot)

    def _failed(self, error):
        self.failed(error)

    def clear(self, *_):
        self.path = None
        self.runner.cancel()
        self.cleared()
