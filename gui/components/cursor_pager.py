"""Shared bounded cursor navigation for artifact, occurrence and run views."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel
from gui.components.artifact_actions import bind_artifact_action


class CursorPager(QWidget):
    requested = Signal()

    def __init__(self, app_context, parent=None):
        super().__init__(parent)
        self.ctx = app_context
        self.cursor, self.next_cursor, self.history = "", "", []
        layout = QHBoxLayout(self)
        self.previous = QPushButton("Previous page", self)
        self.next = QPushButton("Next page", self)
        self.status = QLabel("", self)
        self.status.setAccessibleName("Page information")
        for widget in (self.previous, self.status, self.next):
            layout.addWidget(widget)
        self.previous.clicked.connect(self.previous_page)
        self.next.clicked.connect(self.next_page)
        self._update_buttons()

    def register_actions(self, prefix, mount):
        bind_artifact_action(self.ctx, self, f"{prefix}.previous_page", "Previous Page", self.previous_page, mount, self.previous)
        bind_artifact_action(self.ctx, self, f"{prefix}.next_page", "Next Page", self.next_page, mount, self.next)

    def reset(self, *_):
        self.cursor, self.next_cursor, self.history = "", "", []
        self._update_buttons()
        self.requested.emit()

    def next_page(self, *_):
        if self.next_cursor:
            self.history.append(self.cursor)
            self.cursor, self.next_cursor = self.next_cursor, ""
            self._update_buttons()
            self.requested.emit()

    def previous_page(self, *_):
        if self.history:
            self.cursor, self.next_cursor = self.history.pop(), ""
            self._update_buttons()
            self.requested.emit()

    def apply(self, snapshot):
        self.next_cursor = snapshot.get("next_cursor") or ""
        count = snapshot.get("page_count", len(snapshot.get("items") or []))
        self.status.setText(f"Page {len(self.history)+1} · {count} items")
        self._update_buttons()

    def _update_buttons(self):
        self.previous.setEnabled(bool(self.history))
        self.next.setEnabled(bool(self.next_cursor))
