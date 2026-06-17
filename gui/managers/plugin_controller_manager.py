"""
gui/managers/plugin_controller_manager.py

Manages the QTimer lifecycle for ActiveController instances registered by plugins.

Created by MainWindow after plugins are loaded.  For each registered active
controller it creates a QTimer that calls controller.tick() on the requested
interval.  Wires into workspace_node_updated events to route on_data_updated()
calls.  Responds to plugin_unloaded to stop and remove controllers cleanly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer

if TYPE_CHECKING:
    from core.ontology.registry import OntologyRegistry
    from core.events.event_bus import EventBus


class PluginControllerManager(QObject):
    """Creates and owns QTimers for all registered active node controllers."""

    def __init__(
        self,
        ontology_registry: "OntologyRegistry",
        bus: "EventBus",
        parent: QObject = None,
    ) -> None:
        super().__init__(parent)
        self._registry = ontology_registry
        self._bus = bus
        self._start_all()
        bus.workspace_node_updated.connect(self._on_node_updated)

    def _start_all(self) -> None:
        """Create timers for all controllers already in the registry."""
        for type_key, meta in self._registry._active_controllers.items():
            self._start_controller_timer(type_key, meta)

    def _start_controller_timer(self, type_key: str, meta: dict) -> None:
        if meta.get("timer") is not None:
            return
        interval = getattr(meta["controller"], "tick_interval_ms", 5000)
        timer = QTimer(self)
        timer.timeout.connect(meta["controller"].tick)
        timer.start(interval)
        meta["timer"] = timer

    def on_plugin_loaded(self, plugin_id: str) -> None:
        """Start timers for any controllers registered by the newly loaded plugin."""
        for type_key, meta in self._registry._active_controllers.items():
            if meta.get("plugin_id") == plugin_id and meta.get("timer") is None:
                self._start_controller_timer(type_key, meta)

    def teardown_plugin(self, plugin_id: str) -> None:
        """Stop timers for a plugin being unloaded."""
        for meta in self._registry._active_controllers.values():
            if meta.get("plugin_id") == plugin_id:
                timer = meta.get("timer")
                if timer is not None:
                    timer.stop()
                    meta["timer"] = None

    def _on_node_updated(self, event: object, payload: object) -> None:
        entity_type = getattr(payload, "entity_type", None)
        if entity_type is None:
            return
        meta = self._registry.get_active_controller(entity_type)
        if meta is None:
            return
        node_data = getattr(payload, "node_data", {}) or {}
        try:
            meta["controller"].on_data_updated(node_data)
        except Exception as exc:
            print(f"[PluginControllerManager] on_data_updated error for '{entity_type}': {exc}")
