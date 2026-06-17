"""
gui/components/menu_manager.py

Utility for building/extending the application menu bar from plugin MenuItemSpecs.
Not currently called from main_window (everything goes through the existing toolbar).
Available for plugins or future consumers that need a programmatic menu bar.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMenu

if TYPE_CHECKING:
    from gui.main_window import MainWindow


class MenuManager:
    BUILTIN_MENUS = ["File", "Edit", "View", "Tools", "Plugins"]

    def __init__(self, main_window: "MainWindow") -> None:
        self._main_window = main_window

    def build(self) -> None:
        self._create_builtin_menus()
        self._inject_plugin_items()

    def _get_or_create_menu(self, name: str) -> QMenu:
        mw = self._main_window
        for action in mw.menuBar().actions():
            if action.text() == name and action.menu():
                return action.menu()
        return mw.menuBar().addMenu(name)

    def _create_builtin_menus(self) -> None:
        for name in self.BUILTIN_MENUS:
            self._get_or_create_menu(name)

    def _inject_plugin_items(self) -> None:
        registry = getattr(
            getattr(self._main_window, "app_context", None),
            "plugin_extension_registry",
            None,
        )
        if not registry:
            return
        for spec in sorted(registry.get_menu_items(), key=lambda s: s.position):
            menu = self._get_or_create_menu(spec.menu_name)
            if spec.separator_before:
                menu.addSeparator()
            label = f"{spec.icon} {spec.label}".strip() if spec.icon else spec.label
            action = menu.addAction(label)
            if spec.shortcut:
                action.setShortcut(QKeySequence(spec.shortcut))
            if spec.callback:
                action.triggered.connect(spec.callback)
