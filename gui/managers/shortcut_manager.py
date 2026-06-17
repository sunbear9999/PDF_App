"""
gui/managers/shortcut_manager.py

Registers plugin-contributed keyboard shortcuts (ShortcutSpec) with the main window.
Called once after all plugins are loaded in MainWindow.__init__.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QKeySequence, QShortcut

if TYPE_CHECKING:
    from gui.main_window import MainWindow


class ShortcutManager:
    def __init__(self, main_window: "MainWindow") -> None:
        self._main_window = main_window

    def register_core_shortcuts(self) -> None:
        """Register built-in application shortcuts (viewer zoom, save, fullscreen)."""
        w = self._main_window
        viewer = getattr(w, "viewer", None)
        if viewer:
            self._bind("Ctrl+=", viewer.zoom_in)
            self._bind("Ctrl++", viewer.zoom_in)
            self._bind("Ctrl+-", viewer.zoom_out)
            self._bind("Ctrl+0", viewer.zoom_reset)
            if hasattr(viewer, "annot_manager"):
                self._bind("Ctrl+F", viewer.annot_manager.toggle_search)
        from core.events.domains.project_events import ProjectIntent, ProjectPayload
        self._bind(
            "Ctrl+S",
            lambda: w.bus.project_action_requested.emit(ProjectIntent.SAVE, ProjectPayload()),
        )
        self._bind("F11", w.toggle_full_screen)

    def register_plugin_shortcuts(self) -> None:
        """Create QShortcuts for every ShortcutSpec in the plugin extension registry."""
        registry = getattr(
            getattr(self._main_window, "app_context", None),
            "plugin_extension_registry",
            None,
        )
        if not registry:
            return
        for spec in registry.get_shortcuts():
            if not spec.key_sequence or not spec.callback:
                continue
            try:
                self._bind(spec.key_sequence, spec.callback)
            except Exception as exc:
                print(f"[ShortcutManager] Failed to register shortcut '{spec.shortcut_id}': {exc}")

    def _bind(self, key: str, callback) -> None:
        shortcut = QShortcut(QKeySequence(key), self._main_window)
        shortcut.activated.connect(callback)
