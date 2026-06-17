from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.base.core import SOLARIZED_THEME


class _ClickableCard(QFrame):
    clicked = Signal(object)

    def __init__(self, raw_item: dict, display: dict, parent=None):
        super().__init__(parent)
        self._raw = raw_item
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self._title = QLabel(display.get("title", ""))
        self._title.setWordWrap(True)
        f = self._title.font()
        f.setBold(True)
        self._title.setFont(f)
        top_row.addWidget(self._title, 1)

        badge_text = display.get("badge", "")
        self._badge = QLabel(badge_text)
        self._badge.setVisible(bool(badge_text))
        self._badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        top_row.addWidget(self._badge)

        layout.addLayout(top_row)

        sub = display.get("subtitle", "")
        self._subtitle = QLabel(sub)
        self._subtitle.setWordWrap(True)
        self._subtitle.setVisible(bool(sub))
        layout.addWidget(self._subtitle)

        meta = display.get("meta", "")
        self._meta = QLabel(meta)
        self._meta.setWordWrap(True)
        self._meta.setVisible(bool(meta))
        layout.addWidget(self._meta)

        self._display = display

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._raw)
        super().mousePressEvent(event)

    def apply_theme(self, theme: dict):
        bg = theme.get("bg_panel", SOLARIZED_THEME.get("bg_panel", "#073642"))
        bg_hover = theme.get("bg_input", SOLARIZED_THEME.get("bg_input", "#002b36"))
        border = theme.get("border", SOLARIZED_THEME.get("border", "#586e75"))
        text = theme.get("text_main", "#ffffff")
        muted = theme.get("text_muted", "#aaaaaa")
        accent = theme.get("accent", "#268bd2")

        self.setStyleSheet(
            f"_ClickableCard, QFrame {{"
            f"  background: {bg}; border: 1px solid {border}; border-radius: 5px;"
            f"}}"
            f"_ClickableCard:hover, QFrame:hover {{"
            f"  background: {bg_hover};"
            f"}}"
        )
        self._title.setStyleSheet(f"color: {text}; background: transparent; border: none;")
        self._subtitle.setStyleSheet(f"color: {muted}; background: transparent; border: none;")
        self._meta.setStyleSheet(f"color: {muted}; font-size: 11px; background: transparent; border: none;")
        self._badge.setStyleSheet(
            f"color: #ffffff; background: {accent}; border-radius: 3px;"
            f"padding: 1px 5px; font-size: 11px; border: none;"
        )

    def matches(self, query: str) -> bool:
        q = query.lower()
        return any(
            q in (self._display.get(k) or "").lower()
            for k in ("title", "subtitle", "meta", "badge")
        )


class ItemCardList(QWidget):
    """Searchable scrollable card list. Plugins use this instead of raw QListWidget."""

    item_selected = Signal(object)

    def __init__(
        self,
        render_fn: Callable[[dict], dict],
        placeholder: str = "Search…",
        parent=None,
    ):
        super().__init__(parent)
        self._render_fn = render_fn
        self._theme: dict = {}
        self._cards: list[_ClickableCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText(placeholder)
        root.addWidget(self._search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch(1)
        scroll.setWidget(self._container)
        root.addWidget(scroll)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._filter)
        self._search.textChanged.connect(lambda _: self._debounce.start())

    def set_items(self, items: list[dict]):
        self.clear()
        for raw in items:
            display = self._render_fn(raw)
            card = _ClickableCard(raw, display)
            card.clicked.connect(self.item_selected)
            if self._theme:
                card.apply_theme(self._theme)
            self._cards.append(card)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)
        self._filter()

    def clear(self):
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

    def _filter(self):
        q = self._search.text().strip()
        for card in self._cards:
            card.setVisible(not q or card.matches(q))

    def apply_theme(self, theme: dict):
        self._theme = theme
        bg = theme.get("bg_main", "#1e1e2e")
        border = theme.get("border", "#444")
        text = theme.get("text_main", "#fff")
        bg_input = theme.get("bg_input", "#2b2b3b")
        self._search.setStyleSheet(
            f"background: {bg_input}; color: {text}; border: 1px solid {border};"
            f"border-radius: 4px; padding: 5px;"
        )
        self._container.setStyleSheet(f"background: {bg};")
        for card in self._cards:
            card.apply_theme(theme)


class DetailPane(QWidget):
    """Scrollable key-value detail display. Renders sections with headings, fields, buttons, widgets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 8, 8, 8)
        self._content_layout.setSpacing(8)
        self._content_layout.addStretch(1)
        self._scroll.setWidget(self._content)

        self._placeholder = QLabel("Select an item to view details.")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)

        root.addWidget(self._placeholder)
        root.addWidget(self._scroll)
        self._scroll.hide()

        self._section_widgets: list[QWidget] = []

    def set_data(self, sections: list[dict]):
        self.clear()
        if not sections:
            return
        self._placeholder.hide()
        self._scroll.show()

        for section in sections:
            heading = section.get("heading")
            if heading:
                lbl = QLabel(f"<b>{heading}</b>")
                lbl.setWordWrap(True)
                self._content_layout.insertWidget(
                    self._content_layout.count() - 1, lbl
                )
                self._section_widgets.append(lbl)

            for label, value in section.get("fields", []):
                row = QWidget()
                row.setStyleSheet("background: transparent;")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.setSpacing(8)
                key_lbl = QLabel(f"{label}:")
                key_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
                val_lbl = QLabel(str(value))
                val_lbl.setWordWrap(True)
                val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                rl.addWidget(key_lbl)
                rl.addWidget(val_lbl, 1)
                self._content_layout.insertWidget(
                    self._content_layout.count() - 1, row
                )
                self._section_widgets.append(row)

            for btn_label, callback in section.get("buttons", []):
                btn = QPushButton(btn_label)
                btn.clicked.connect(callback)
                self._content_layout.insertWidget(
                    self._content_layout.count() - 1, btn
                )
                self._section_widgets.append(btn)

            widget = section.get("widget")
            if widget is not None:
                self._content_layout.insertWidget(
                    self._content_layout.count() - 1, widget
                )
                self._section_widgets.append(widget)

        self._apply_theme_to_children()

    def clear(self):
        for w in self._section_widgets:
            w.setParent(None)
            w.deleteLater()
        self._section_widgets.clear()
        self._placeholder.show()
        self._scroll.hide()

    def apply_theme(self, theme: dict):
        self._theme = theme
        bg = theme.get("bg_main", "#1e1e2e")
        self._content.setStyleSheet(f"background: {bg};")
        self._scroll.setStyleSheet(f"background: {bg}; border: none;")
        self._placeholder.setStyleSheet(
            f"color: {theme.get('text_muted', '#aaa')}; background: transparent;"
        )
        self._apply_theme_to_children()

    def _apply_theme_to_children(self):
        if not self._theme:
            return
        text = self._theme.get("text_main", "#fff")
        muted = self._theme.get("text_muted", "#aaa")
        accent = self._theme.get("accent", "#268bd2")
        border = self._theme.get("border", "#444")
        bg_input = self._theme.get("bg_input", "#2b2b3b")

        for w in self._section_widgets:
            if isinstance(w, QLabel):
                if "<b>" in (w.text() or ""):
                    w.setStyleSheet(
                        f"color: {text}; font-size: 13px; background: transparent; border: none;"
                    )
                else:
                    w.setStyleSheet(f"color: {muted}; background: transparent; border: none;")
            elif isinstance(w, QPushButton):
                w.setStyleSheet(
                    f"background: {accent}; color: #fff; border: none;"
                    f"border-radius: 4px; padding: 5px 10px; font-weight: bold;"
                )
            elif isinstance(w, QWidget):
                for child in w.findChildren(QLabel):
                    child.setStyleSheet(f"color: {text}; background: transparent; border: none;")
