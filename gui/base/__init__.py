from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
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

# Window flags applied to non-modal PapyrusDialog windows.
# Qt.Tool causes the window manager to:
#  - skip the window in the taskbar / dock  (X11: _NET_WM_WINDOW_TYPE_UTILITY)
#  - keep it in front of its transient parent without system-wide always-on-top
#  - not reveal the desktop panel when the parent is fullscreen (most WMs)
_DIALOG_MODELESS_FLAGS: Qt.WindowType = (
    Qt.WindowType.Tool
    | Qt.WindowType.WindowTitleHint
    | Qt.WindowType.WindowSystemMenuHint
    | Qt.WindowType.WindowCloseButtonHint
)

from gui.base.core import (
    FALLBACK_THEME, SOLARIZED_THEME,
    UnifiedThemedMixin, ThemedMixin as _CoreThemedMixin, ModernThemedMixin,
    BaseDock,
)
from gui.base.forms import SchemaFormBuilder
from gui.base.layouts import BasePromptWorkspace, BaseToolDock
from gui.base.widgets import ItemCardList, DetailPane

# Keep DEFAULT_THEME as a legacy alias so old imports don't break.
DEFAULT_THEME = FALLBACK_THEME


class CompatThemedMixin(UnifiedThemedMixin):
    """
    Backward-compatible styling API for older widgets.
    Delegates to UnifiedThemedMixin internally — no duplicated logic.
    """

    def set_theme(self, theme: dict | None = None) -> None:
        self.apply_theme({**FALLBACK_THEME, **(theme or {})})

    def update_theme(self, theme: dict) -> None:
        self.apply_theme({**FALLBACK_THEME, **(theme or {})})

    # Legacy method names — map to UnifiedThemedMixin helpers
    def input_style(self, padding: int = 6) -> str:
        return self._input_style(padding)

    def button_style(self, accent: bool = False, transparent: bool = False) -> str:
        if transparent:
            return self._button_style("transparent")
        if accent:
            return self._button_style("primary")
        return self._button_style("default")


class BasePanel(QFrame, CompatThemedMixin):
    def __init__(self, theme: dict | None = None, parent=None):
        super().__init__(parent)
        self.theme = {**DEFAULT_THEME, **(theme or {})}

    def update_theme(self, theme: dict):
        CompatThemedMixin.update_theme(self, theme)
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
        # Alias so PluginCard callers using content_layout still work
        self.content_layout = self.body_layout

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
    search_requested = Signal(str, str)   # (engine_id, query) — button-triggered
    search_changed = Signal(str)          # (query) — debounced live search

    def __init__(
        self,
        title: str = "",
        placeholder: str = "Search...",
        buttons: list[tuple[str, str]] | None = None,
        theme: dict | None = None,
        debounce_ms: int = 0,            # >0 enables live search_changed signal
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

        if debounce_ms > 0:
            self._debounce_timer = QTimer(self)
            self._debounce_timer.setSingleShot(True)
            self._debounce_timer.setInterval(debounce_ms)
            self._debounce_timer.timeout.connect(
                lambda: self.search_changed.emit(self.input_field.text())
            )
            self.input_field.textChanged.connect(lambda _: self._debounce_timer.start())

        self.update_theme(self.theme)

    def update_theme(self, theme: dict):
        super().update_theme(theme)
        self.setStyleSheet("QFrame { background: transparent; border: none; }")
        self.input_field.setStyleSheet(self.input_style())
        for button in self.action_buttons:
            button.setStyleSheet(self.button_style())
        if self.title_label:
            self.title_label.setStyleSheet(f"color: {self.theme.get('text_main')};")


class BaseDialog(QDialog, CompatThemedMixin):
    """
    Base class for all secondary Papyrus windows.

    Differences from plain QDialog
    --------------------------------
    * Automatically resolves the nearest QMainWindow as parent so the OS
      can apply the transient-for hint (prevents orphaned taskbar entries).
    * Uses Qt.Tool window flags by default: no separate taskbar/dock entry,
      stays in front of the main window, and does not reveal the desktop
      panel when the main window is fullscreen (most Linux WMs).
    * Non-modal by default.  Pass ``modal=True`` to opt into WindowModal
      blocking (preferred over ApplicationModal).
    * Sets WA_DeleteOnClose for non-modal instances so the C++ object is
      freed when the user closes the window.

    Subclasses that call exec() directly (blocking, value-returning dialogs)
    should pass ``modal=True`` and NOT use WA_DeleteOnClose so they can read
    dialog state after exec() returns.  Or better, route them through
    ``app_context.dialog_manager.exec_modal()``.
    """

    def __init__(
        self,
        title: str = "",
        theme: dict | None = None,
        parent: QWidget | None = None,
        *,
        modal: bool = False,
        delete_on_close: bool = True,
    ) -> None:
        # Resolve the nearest QMainWindow so the OS can apply transient-for.
        # Import here to avoid circular import at module level.
        from gui.managers.dialog_manager import resolve_main_window
        resolved_parent = resolve_main_window(parent)
        super().__init__(resolved_parent)

        # Apply window flags that prevent a separate taskbar entry.
        # Modal dialogs get the same flags; modality is controlled separately.
        self.setWindowFlags(_DIALOG_MODELESS_FLAGS)

        if modal:
            self.setWindowModality(Qt.WindowModality.WindowModal)
        else:
            self.setWindowModality(Qt.WindowModality.NonModal)
            if delete_on_close:
                self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self.theme = {**DEFAULT_THEME, **(theme or {})}
        if title:
            self.setWindowTitle(title)

    def update_theme(self, theme: dict):
        CompatThemedMixin.update_theme(self, theme)
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


# Keep ThemedMixin pointing to CompatThemedMixin (matches old gui/components/base/__init__.py behaviour)
ThemedMixin = CompatThemedMixin

__all__ = [
    "BaseDock",
    "BaseCard",
    "BaseDialog",
    "BasePanel",
    "BasePromptWorkspace",
    "BaseSearchBar",
    "BaseToolDock",
    "CompatThemedMixin",
    "DEFAULT_THEME",
    "DetailPane",
    "FALLBACK_THEME",
    "ItemCardList",
    "ModernThemedMixin",
    "SOLARIZED_THEME",
    "SchemaFormBuilder",
    "ThemedMixin",
    "UnifiedThemedMixin",
    "make_transparent_scroll_area",
]
