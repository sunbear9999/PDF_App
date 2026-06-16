from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt

if TYPE_CHECKING:
    from gui.app_context import AppContext

# Switched to standard Solarized Dark palette
SOLARIZED_THEME = {
    "bg_main": "#002b36",      # Base03 (Main App)
    "bg_panel": "#073642",     # Base02 (Cards, Docks, Toolbars)
    "bg_input": "#00212b",     # Slightly darker for depth in inputs
    "border": "#586e75",       # Base01
    "accent": "#268bd2",       # Blue
    "success": "#859900",      # Green
    "warning": "#b58900",      # Yellow
    "error": "#dc322f",        # Red
    "text_main": "#93a1a1",    # Base1
    "text_muted": "#586e75",   # Base01
}

class BaseDock(QWidget):
    """
    Base class for all plugin-compatible docks.

    Accepts an ``AppContext`` so docks remain testable and decoupled from
    ``MainWindow``.  Automatically subscribes to project lifecycle events and
    ``theme_changed`` on the EventBus, calling the matching hook methods.

    Subclasses override:
    - ``apply_theme(theme: dict)`` — re-style when theme changes
    - ``on_project_loaded()`` — refresh data when a project opens
    - ``on_project_cleared()`` — clear state when a project closes
    """

    def __init__(self, app_context: "AppContext", parent=None):
        super().__init__(parent)
        self.app_context = app_context
        self.bus = app_context.bus

        self.bus.project_loaded.connect(self._on_project_loaded_event)
        self.bus.project_clearing_started.connect(self._on_project_clearing_event)
        self.bus.theme_changed.connect(self._on_theme_changed_event)

    # -- Hook implementations ------------------------------------------------

    def _on_project_loaded_event(self, event, payload):
        self.on_project_loaded()

    def _on_project_clearing_event(self, event, payload):
        self.on_project_cleared()

    def _on_theme_changed_event(self, event, theme):
        if isinstance(theme, dict):
            self.apply_theme(theme)

    # -- Subclass overrides --------------------------------------------------

    def apply_theme(self, theme: dict) -> None:
        """Called whenever the application theme changes. Override to re-style."""

    def on_project_loaded(self) -> None:
        """Called after a new project is loaded. Override to refresh data."""

    def on_project_cleared(self) -> None:
        """Called before the project is cleared. Override to reset state."""

    # Convenience alias so callers using update_theme() still work
    def update_theme(self, theme: dict) -> None:
        self.apply_theme(theme)


class ThemedMixin:
    """A mixin that provides unified, modern styling utilities."""
    
    def apply_base_theme(self, theme: dict = None):
        self.theme = {**SOLARIZED_THEME, **(theme or {})}
        self.update_theme(self.theme)

    def update_theme(self, theme: dict):
        self.theme = theme
        # Child classes will override this
        
    def get_input_style(self) -> str:
        # Softer radius, deeper background, better padding
        return (
            f"background-color: {self.theme['bg_input']}; "
            f"color: {self.theme['text_main']}; "
            f"border: 1px solid {self.theme['border']}; "
            f"border-radius: 6px; padding: 8px 10px; "
            f"font-size: 13px;"
        )

    def get_button_style(self, is_primary=False) -> str:
        # Modern, pill-like buttons with bold text
        bg = self.theme['accent'] if is_primary else self.theme['bg_panel']
        text = "#fdf6e3" if is_primary else self.theme['text_main']
        border = "none" if is_primary else f"1px solid {self.theme['border']}"
        return (
            f"background-color: {bg}; color: {text}; "
            f"border: {border}; border-radius: 6px; "
            f"padding: 8px 16px; font-weight: 600; "
            f"font-size: 13px;"
        )