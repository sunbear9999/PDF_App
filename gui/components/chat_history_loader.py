"""Worker-paged chat history replayed one turn record per Qt event cycle."""
from PySide6.QtCore import QObject, QTimer
from gui.components.artifact_tasks import ArtifactTaskRunner
from gui.components.cursor_pager import CursorPager
from gui.docks.unified_research.components.history_renderer import ChatHistoryRenderer

REPLAY_INTERVAL_MS = 1


class ChatHistoryLoader(QObject):
    def __init__(self, app_context, parent):
        super().__init__(parent)
        self.ctx, self.tab = app_context, parent
        self.messages, self.renderer = [], None
        self.pager = CursorPager(app_context, parent)
        self.pager.previous.setText('Newer messages')
        self.pager.next.setText('Older messages')
        self.pager.register_actions('intelligence.chat', 'toolbar:chat:history')
        parent.layout().insertWidget(0, self.pager)
        self.pager.requested.connect(self.load)
        self.runner = ArtifactTaskRunner(app_context, self, service_name='reference_note_service')
        self.runner.completed.connect(self._ready)
        self.runner.failed.connect(self._failed)
        self.timer = QTimer(self)
        self.timer.setInterval(REPLAY_INTERVAL_MS)
        self.timer.timeout.connect(self._tick)
        parent.destroyed.connect(self.runner.shutdown)
        bus = getattr(app_context, 'bus', None)
        if bus is not None:
            bus.project_clearing_started.connect(self.clear)

    def load(self):
        self.timer.stop()
        self.messages = []
        if getattr(self.tab, '_active_stream_widget', None) is not None:
            self._failed('Finish the active response before changing history pages.')
            return
        self.pager.setEnabled(False)
        self.pager.status.setText('Loading saved messages…')
        if not self.runner.start('query_chat_history', tab_name=self.tab.target_id, cursor=self.pager.cursor):
            self._failed('Saved chat browsing is unavailable.')

    def _ready(self, operation):
        if getattr(self.tab, '_active_stream_widget', None) is not None:
            self._failed('History load cancelled because a live response started.')
            return
        snapshot, self.runner.result = self.runner.result or {}, None
        self.tab.clear_rendered_chat()
        self.pager.apply(snapshot)
        if snapshot.get('continued'):
            self.pager.status.setText(self.pager.status.text() + ' · Continued response; its beginning is on an older page')
        self.messages = list(snapshot.get('items') or [])
        self.renderer = ChatHistoryRenderer(self.tab, theme=self.tab.theme, app_context=self.ctx)
        self.timer.start()

    def _tick(self):
        if not self.messages:
            self.timer.stop()
            self.pager.setEnabled(True)
            self.renderer.scroll_to_bottom()
            return
        message = self.messages.pop(0)
        self.tab._replaying_history = True
        try:
            self.renderer.render_message(message)
        finally:
            self.tab._replaying_history = False
            self.tab._active_stream_widget = None

    def cancel(self):
        self.timer.stop()
        self.messages = []
        self.runner.cancel()
        self.pager.setEnabled(True)

    def clear(self, *_):
        self.cancel()
        self.pager.cursor, self.pager.next_cursor, self.pager.history = '', '', []
        self.pager.apply({'items': []})
        self.tab.clear_rendered_chat()

    def _failed(self, error):
        self.pager.setEnabled(True)
        self.pager.status.setText(error + ' Reopen the tab or refresh to restart.')
