"""Paged citation rows with one reusable note bubble and retained unsaved drafts."""
from PySide6.QtWidgets import QWidget, QVBoxLayout
import weakref
from gui.components.paged_collection import PagedCollection

PAGE_SIZE = 25


class CitationCollection(QWidget):
    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self._message_owner = weakref.ref(parent)
        self.items, self.bubble, self.selected_item = [], None, None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.browser = PagedCollection(app_context, self)
        self.browser.setMinimumHeight(300)
        self.browser.pager.register_actions('intelligence.citations', 'toolbar:chat:citations')
        self.browser.pager.requested.connect(self._page)
        self.browser.selected.connect(self._select)
        layout.addWidget(self.browser)

    def set_items(self, items):
        self._retain_draft()
        self.items = list(items)
        self.browser.pager.reset()

    def _retain_draft(self):
        if self.bubble is not None and self.selected_item is not None:
            self.selected_item['draft'] = self.bubble.note_edit.toPlainText()
        self.bubble, self.selected_item = None, None

    def _page(self):
        self._retain_draft()
        start = int(self.browser.pager.cursor or 0)
        rows = [{'title': str(item.get('doc_name') or 'Source'),
                 'preview': str(item.get('quote') or '')[:240], 'index': index}
                for index, item in enumerate(self.items[start:start+PAGE_SIZE], start)]
        next_cursor = str(start+PAGE_SIZE) if start+PAGE_SIZE < len(self.items) else ''
        self.browser.apply({'items': rows, 'next_cursor': next_cursor})

    def _select(self, index):
        self._retain_draft()
        item = self.items[self.browser.model.items[index]['index']]
        self.selected_item = item
        owner = self._message_owner()
        if owner is None:
            return
        self.bubble = owner._create_bubble(item.get('doc_name') or 'Source', item.get('quote') or '',
            item.get('note') or '', item.get('source_meta') or {})
        self.bubble.note_edit.setPlainText(item.get('draft', item.get('note') or ''))
        self.browser.set_detail(self.bubble)

    def update_theme(self, theme):
        if self.bubble is not None:
            self.bubble.update_theme(theme)
