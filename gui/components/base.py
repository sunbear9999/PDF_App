from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


DEFAULT_THEME = {
    "bg_main": "#1e1e1e",
    "bg_panel": "#333333",
    "bg_input": "#2b2b2b",
    "border": "#444444",
    "accent": "#b366ff",
    "success": "#00cc66",
    "text_main": "#ffffff",
    "text_muted": "#aaaaaa",
}


class ThemedMixin:
    def set_theme(self, theme: dict | None):
        self.theme = {**DEFAULT_THEME, **(theme or {})}
        self.update_theme(self.theme)

    def update_theme(self, theme: dict):
        self.theme = {**DEFAULT_THEME, **(theme or {})}

    def input_style(self, padding: int = 6) -> str:
        return (
            f"background-color: {self.theme.get('bg_input')}; "
            f"color: {self.theme.get('text_main')}; "
            f"border: 1px solid {self.theme.get('border')}; "
            f"border-radius: 4px; padding: {padding}px;"
        )

    def button_style(self, accent: bool = False, transparent: bool = False) -> str:
        if transparent:
            return (
                f"background: transparent; color: {self.theme.get('text_muted')}; "
                "border: none; font-weight: bold;"
            )
        if accent:
            return (
                f"background-color: {self.theme.get('accent')}; color: #ffffff; "
                "border: none; border-radius: 4px; padding: 6px 10px; font-weight: bold;"
            )
        return (
            f"background-color: {self.theme.get('bg_panel')}; "
            f"color: {self.theme.get('text_main')}; "
            f"border: 1px solid {self.theme.get('border')}; "
            "border-radius: 4px; padding: 6px 10px; font-weight: bold;"
        )


class BasePanel(QFrame, ThemedMixin):
    def __init__(self, theme: dict | None = None, parent=None):
        super().__init__(parent)
        self.theme = {**DEFAULT_THEME, **(theme or {})}

    def update_theme(self, theme: dict):
        ThemedMixin.update_theme(self, theme)
        self.setStyleSheet(
            f"QFrame {{ background-color: {self.theme.get('bg_panel')}; "
            f"color: {self.theme.get('text_main')}; "
            f"border: 1px solid {self.theme.get('border')}; border-radius: 6px; }}"
        )


class BaseCard(BasePanel):
    action_requested = Signal(str, dict)

    def __init__(self, theme: dict | None = None, accent_color: str | None = None, parent=None):
        super().__init__(theme, parent)
        self.accent_color = accent_color
        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(12, 12, 12, 12)
        self.body_layout.setSpacing(8)

    def add_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        self.body_layout.addWidget(label)
        return label

    def add_body_text(self, text: str, muted: bool = False, selectable: bool = False) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        if selectable:
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setProperty("muted", muted)
        self.body_layout.addWidget(label)
        return label

    def add_action_button(self, label: str, action: str, payload: dict | None = None) -> QPushButton:
        button = QPushButton(label)
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        button.clicked.connect(lambda: self.action_requested.emit(action, payload or {}))
        self.body_layout.addWidget(button)
        return button

    def update_theme(self, theme: dict):
        super().update_theme(theme)
        border = self.accent_color or self.theme.get("border")
        self.setStyleSheet(
            f"QFrame {{ background-color: {self.theme.get('bg_input')}; "
            f"border: 1px solid {self.theme.get('border')}; "
            f"border-left: 4px solid {border}; border-radius: 6px; margin-bottom: 8px; }}"
            f"QLabel {{ color: {self.theme.get('text_main')}; border: none; background: transparent; }}"
            f"QPushButton {{ {self.button_style()} }}"
        )
        for label in self.findChildren(QLabel):
            if label.property("muted"):
                label.setStyleSheet(f"color: {self.theme.get('text_muted')}; background: transparent; border: none;")


class BaseSearchBar(BasePanel):
    search_requested = Signal(str, str)

    def __init__(
        self,
        title: str = "",
        placeholder: str = "Search...",
        buttons: list[tuple[str, str]] | None = None,
        theme: dict | None = None,
        parent=None,
    ):
        super().__init__(theme, parent)
        self.buttons_config = buttons or [("Search", "default")]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.title_label = QLabel(f"<b>{title}</b>") if title else None
        if self.title_label:
            layout.addWidget(self.title_label)
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText(placeholder)
        layout.addWidget(self.input_field)
        self.button_layout = QHBoxLayout()
        self.action_buttons = []
        for label, engine_id in self.buttons_config:
            button = QPushButton(label)
            button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            button.clicked.connect(lambda checked=False, e=engine_id: self.search_requested.emit(e, self.input_field.text().strip()))
            self.button_layout.addWidget(button)
            self.action_buttons.append(button)
        layout.addLayout(self.button_layout)
        self.update_theme(self.theme)

    def update_theme(self, theme: dict):
        super().update_theme(theme)
        self.setStyleSheet("QFrame { background: transparent; border: none; }")
        self.input_field.setStyleSheet(self.input_style())
        for button in self.action_buttons:
            button.setStyleSheet(self.button_style())
        if self.title_label:
            self.title_label.setStyleSheet(f"color: {self.theme.get('text_main')};")


class BaseDialog(QDialog, ThemedMixin):
    def __init__(self, title: str = "", theme: dict | None = None, parent=None):
        super().__init__(parent)
        self.theme = {**DEFAULT_THEME, **(theme or {})}
        if title:
            self.setWindowTitle(title)

    def update_theme(self, theme: dict):
        ThemedMixin.update_theme(self, theme)
        self.setStyleSheet(
            f"QDialog {{ background-color: {self.theme.get('bg_main')}; color: {self.theme.get('text_main')}; }}"
            f"QLabel {{ color: {self.theme.get('text_main')}; background: transparent; }}"
            f"QLineEdit, QTextEdit, QComboBox {{ {self.input_style()} }}"
            f"QPushButton {{ {self.button_style()} }}"
        )


def make_transparent_scroll_area(content: QWidget | None = None) -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setStyleSheet("background: transparent; border: none;")
    viewport = content or QWidget()
    viewport.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(viewport)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    scroll.setWidget(viewport)
    return scroll, viewport, layout
