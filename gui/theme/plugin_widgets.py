"""Automatic theme integration for widgets contributed by plugins."""
from __future__ import annotations

from typing import Any


def mark_plugin_widget(widget: Any, plugin_id: str) -> Any:
    """Mark a plugin-owned root so the inherited global QSS can scope it."""
    if widget is not None and plugin_id:
        widget.setProperty("papyrus_plugin_id", plugin_id)
        widget.setProperty("papyrus_plugin_root", True)
    return widget


def prepare_plugin_widget(widget: Any, plugin_id: str, theme: dict | None = None) -> Any:
    """Apply default plugin theming and then allow an optional custom hook."""
    mark_plugin_widget(widget, plugin_id)
    callback = (
        getattr(widget, "update_theme", None)
        or getattr(widget, "apply_theme", None)
    )
    if theme is not None and callable(callback):
        callback(theme)
    return widget
