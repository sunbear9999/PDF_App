"""Book presentation reuses the verified, paged block reader and its selection map."""
from core.events.domains.document_events import SourceEvent
from core.models.display_settings import SourceViewerDisplaySettings
from gui.components.reflowable_text_viewer import ReflowableTextViewer

TEXT_TYPES = {'txt', 'md', 'html', 'docx', 'epub', 'rtf', 'doc', 'pdf'}


class BookViewWidget(ReflowableTextViewer):
    def __init__(self, app_context=None, parent=None):
        super().__init__(app_context, parent)

    def _on_source_opened(self, event, payload):
        if (getattr(payload, 'context', None) or {}).get('source_anchor'):
            return
        service = getattr(self._ctx, 'source_viewer_settings_service', None)
        settings = service.get_settings() if service else SourceViewerDisplaySettings()
        if settings.reading_mode != 'book' or event != SourceEvent.OPENED or payload.source_type not in TEXT_TYPES:
            return
        self._load(payload.path, payload.source_type)

    def _load(self, path, source_type=None):
        self._source_type = source_type or self._source_type
        super()._load(path)
        if self._source_type == 'pdf':
            self._title_label.setText('PDF book preview — select words in PDF view to add a source note')

    def _on_context_menu(self, position):
        if self._source_type == 'pdf':
            self.bus.status_message_requested.emit('Switch to PDF view to select verified word rectangles for a note.', 6000)
            return
        super()._on_context_menu(position)

    def next_page(self):
        self.pager.next_page()

    def previous_page(self):
        self.pager.previous_page()

    def toggle_dark_mode(self):
        self._display_settings.dark_mode_enabled = not self._display_settings.dark_mode_enabled
        self._render()

    def toggle_theme_mode(self):
        self._display_settings.theme_mode_enabled = not self._display_settings.theme_mode_enabled
        self._render()
