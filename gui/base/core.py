from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt

if TYPE_CHECKING:
    from gui.app_context import AppContext

# Canonical fallback theme — matches every key ThemeManager can emit.
# Only used when no live theme is available (e.g., isolated tests).
FALLBACK_THEME: dict = {
    "bg_main": "#1e1e1e",
    "bg_panel": "#2b2b2b",
    "bg_input": "#333333",
    "text_main": "#ffffff",
    "text_muted": "#aaaaaa",
    "border": "#555555",
    "accent": "#0078D7",
    "accent_hover": "#0055ff",
    "canvas": "#1a1a1a",
    "success": "#00cc66",
    "warning": "#ffaa00",
    "error": "#ff4444",
    "ai_bubble": "#2d2238",
    "ai_bubble_border": "#b57edc",
    "ai_bubble_hover": "#38274a",
    "user_bubble": "#2b2b2b",
    "user_bubble_border": "#444444",
    "user_bubble_hover": "#333333",
}

# Legacy alias — keep so existing code that imports SOLARIZED_THEME still works.
SOLARIZED_THEME = FALLBACK_THEME


class UnifiedThemedMixin:
    """
    Single theming mixin for all GUI components.

    Usage in a widget:
        class MyWidget(QWidget, UnifiedThemedMixin):
            def apply_theme(self, theme: dict) -> None:
                self.setStyleSheet(f"background: {self._t('bg_main')};")

    Key helpers:
        _t(key, fallback)  — safe theme key lookup
        _input_style()     — QSS string for input fields
        _button_style(variant)  — QSS string for buttons
        _panel_style()     — QSS string for panel frames
    """

    _theme: dict = {}

    def _t(self, key: str, fallback: str = "#000000") -> str:
        """Safe theme key access with FALLBACK_THEME defaults."""
        theme = getattr(self, "_theme", {})
        return theme.get(key) or FALLBACK_THEME.get(key, fallback)

    def apply_theme(self, theme: dict) -> None:
        """Primary override point. Store theme and re-style the widget."""
        self._theme = theme

    def update_theme(self, theme: dict) -> None:
        """Alias for apply_theme — keeps old call sites working."""
        self.apply_theme(theme)

    # ----------------------------------------------------------------
    # Pre-built QSS helpers
    # ----------------------------------------------------------------

    def _input_style(self, padding: int = 8) -> str:
        return (
            f"background-color: {self._t('bg_input')}; "
            f"color: {self._t('text_main')}; "
            f"border: 1px solid {self._t('border')}; "
            f"border-radius: 6px; padding: {padding}px 10px; font-size: 13px;"
        )

    def _button_style(self, variant: str = "default") -> str:
        if variant == "primary":
            return (
                f"background-color: {self._t('accent')}; color: #ffffff; "
                f"border: none; border-radius: 6px; padding: 8px 16px; font-weight: 600;"
            )
        if variant == "danger":
            return (
                f"background-color: {self._t('error')}; color: #ffffff; "
                f"border: none; border-radius: 6px; padding: 8px 16px; font-weight: 600;"
            )
        if variant == "transparent":
            return (
                f"background: transparent; color: {self._t('text_muted')}; border: none; font-weight: bold;"
            )
        return (
            f"background-color: {self._t('bg_panel')}; "
            f"color: {self._t('text_main')}; "
            f"border: 1px solid {self._t('border')}; "
            f"border-radius: 6px; padding: 8px 16px; font-weight: bold;"
        )

    def _panel_style(self) -> str:
        return (
            f"background-color: {self._t('bg_panel')}; "
            f"color: {self._t('text_main')}; "
            f"border: 1px solid {self._t('border')}; border-radius: 6px;"
        )

    # ----------------------------------------------------------------
    # Legacy API shims — so old callers keep working without changes
    # ----------------------------------------------------------------

    def apply_base_theme(self, theme: dict | None = None) -> None:
        self.apply_theme({**FALLBACK_THEME, **(theme or {})})

    def get_input_style(self) -> str:
        return self._input_style()

    def get_button_style(self, is_primary: bool = False) -> str:
        return self._button_style("primary" if is_primary else "default")


# Canonical public name and legacy alias
ThemedMixin = UnifiedThemedMixin
ModernThemedMixin = UnifiedThemedMixin


class BaseDock(QWidget):
    """
    Base class for all plugin-compatible docks.

    Accepts an ``AppContext`` so docks remain testable and decoupled from
    ``MainWindow``.  Automatically subscribes to project lifecycle events and
    ``theme_changed`` on the EventBus, calling the matching hook methods.

    Subclasses override:
    - ``apply_theme(theme: dict)``  — re-style when theme changes
    - ``on_project_loaded()``       — refresh data when a project opens
    - ``on_project_cleared()``      — clear state when a project closes
    """

    def __init__(self, app_context: "AppContext", parent=None):
        super().__init__(parent)
        self.app_context = app_context
        self.bus = app_context.bus

        self.bus.project_loaded.connect(self._on_project_loaded_event)
        self.bus.project_clearing_started.connect(self._on_project_clearing_event)
        self.bus.theme_changed.connect(self._on_theme_changed_event)

    def _on_project_loaded_event(self, event, payload):
        self.on_project_loaded()

    def _on_project_clearing_event(self, event, payload):
        self.on_project_cleared()

    def _on_theme_changed_event(self, event, theme):
        if isinstance(theme, dict):
            self.apply_theme(theme)

    def apply_theme(self, theme: dict) -> None:
        """Called whenever the application theme changes. Override to re-style."""

    def on_project_loaded(self) -> None:
        """Called after a new project is loaded. Override to refresh data."""

    def on_project_cleared(self) -> None:
        """Called before the project is cleared. Override to reset state."""

    def update_theme(self, theme: dict) -> None:
        self.apply_theme(theme)
