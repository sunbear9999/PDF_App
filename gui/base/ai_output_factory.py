# gui/base/ai_output_factory.py
"""
Registry that maps AI output payload type keys to widget factory functions.

Plugins register custom renderers via api.gui_extensions.add_ai_renderer().
The main window reads those specs and calls AIOutputWidgetFactory.register()
so BlueprintUIRouter can instantiate the right widget for each payload type.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class AIOutputWidgetFactory:
    _registry: Dict[str, Callable] = {}

    @classmethod
    def register(cls, payload_type: str, factory: Callable) -> None:
        """Register a factory callable for a named payload type."""
        cls._registry[payload_type] = factory

    @classmethod
    def create(cls, payload_type: str, payload: Any, parent=None):
        """
        Create a widget for the given payload type and data.

        Returns None if no factory is registered for this type.
        """
        factory = cls._registry.get(payload_type)
        if factory is None:
            return None
        try:
            return factory(payload, parent)
        except Exception as exc:
            print(f"[AIOutputWidgetFactory] Factory for '{payload_type}' failed: {exc}")
            return None

    @classmethod
    def has_factory(cls, payload_type: str) -> bool:
        return payload_type in cls._registry

    @classmethod
    def registered_types(cls) -> list:
        return list(cls._registry.keys())
