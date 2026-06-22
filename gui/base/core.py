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

    Also mixes in ``DockActionMixin`` so any subclass can call
    ``build_context_menu_from_mount()``, ``build_toolbar_actions()``, and
    ``_make_dock_context()`` without additional imports.  Set
    ``self._dock_id`` in the subclass ``__init__`` to the dock's logical ID
    string (e.g. ``"data_dock"``) for convenience methods that auto-build the
    ``DockActionContext``.

    Subclasses override:
    - ``apply_theme(theme: dict)``  — re-style when theme changes
    - ``on_project_loaded()``       — refresh data when a project opens
    - ``on_project_cleared()``      — clear state when a project closes
    """

    #: Subclasses may set this to their logical dock id (e.g. "data_dock")
    _dock_id: str = ""

    def __init__(self, app_context: "AppContext", parent=None):
        super().__init__(parent)
        self.app_context = app_context
        self.bus = app_context.bus
        # Wire DockActionMixin — provides build_context_menu_from_mount() etc.
        self._dock_action_app_context = app_context

        self.bus.project_loaded.connect(self._on_project_loaded_event)
        self.bus.project_clearing_started.connect(self._on_project_clearing_event)
        self.bus.theme_changed.connect(self._on_theme_changed_event)
        self.bus.ai_availability_changed.connect(self._on_ai_availability_changed_event)

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

    def _on_ai_availability_changed_event(self, available: bool) -> None:
        self.on_ai_availability_changed(available)

    def on_ai_availability_changed(self, available: bool) -> None:
        """Called when AI backend availability changes. Override in AI-dependent docks."""

    def update_theme(self, theme: dict) -> None:
        self.apply_theme(theme)

    # ----------------------------------------------------------------
    # Dock Action helpers (DockActionMixin functionality built in)
    # ----------------------------------------------------------------

    def _make_dock_context(self, zone: str, *, selection=None, source_path=None, extra=None):
        """Build a DockActionContext for this dock's current state."""
        from core.plugins.extension_registry import DockActionContext
        return DockActionContext(
            dock_id=self._dock_id,
            zone=zone,
            selection=selection,
            source_path=source_path,
            extra=extra or {},
            app_context=self.app_context,
        )

    def build_context_menu_from_mount(self, mount: str, dock_ctx, parent_menu=None):
        """Return a QMenu populated with registered DockActionSpecs for *mount*."""
        from PySide6.QtWidgets import QMenu
        from gui.components.dock_action_mixin import inject_dock_actions_into_menu, DockActionMixin
        inst = DockActionMixin()
        inst._dock_action_app_context = self.app_context
        return inst.build_context_menu_from_mount(mount, dock_ctx, parent_menu)

    def build_toolbar_actions(self, mount: str, dock_ctx):
        """Return a list of QAction objects for a dock toolbar at *mount*."""
        from gui.components.dock_action_mixin import DockActionMixin
        inst = DockActionMixin()
        inst._dock_action_app_context = self.app_context
        return inst.build_toolbar_actions(mount, dock_ctx)

    def inject_dock_actions(self, menu, mount: str, zone: str, **ctx_kwargs):
        """Append DockActionSpec items for *mount* into an existing QMenu.

        Convenience wrapper — no need to import ``inject_dock_actions_into_menu``
        in every dock subclass::

            def _on_right_click(self, pos):
                menu = QMenu(self)
                menu.addAction("Existing Action", self._do_thing)
                self.inject_dock_actions(menu, DATA_DOCK_CONTEXT_MENU_CELL,
                                         "context_menu:cell", row=row, col=col)
                menu.exec(QCursor.pos())
        """
        from gui.components.dock_action_mixin import inject_dock_actions_into_menu
        inject_dock_actions_into_menu(
            menu,
            mount,
            dock_id=self._dock_id,
            zone=zone,
            app_context=self.app_context,
            **ctx_kwargs,
        )
