"""
core/plugins/active_controller.py

Base class for active ontology node controllers.

A plugin registers an active controller alongside a node type via::

    api.register_active_node("StockTicker", StockController, requires_internet=True)

Lifecycle:
- ``tick()`` is called by a per-controller QTimer (interval = tick_interval_ms).
- ``on_data_updated(node_data)`` is called when a node of this type changes in
  the database (wired by PluginControllerManager via workspace_node_updated).
- ``get_custom_ui(node_data)`` is called once when a workspace node of this type
  is first rendered; return a QWidget to embed inside the node via
  QGraphicsProxyWidget, or None for default rendering.

The controller is instantiated once per registered type (singleton per type, not
per node instance).  It must be thread-safe if tick() fires concurrently with
on_data_updated().
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class ActiveController(ABC):
    """Abstract base for active ontology node controllers.

    Subclass this in your plugin and register it with
    ``api.register_active_node(type_key, MyController)``.
    """

    tick_interval_ms: int = 5000

    @abstractmethod
    def tick(self) -> None:
        """Called periodically by the controller timer.

        Fetch external data, emit EventBus updates, or write to the project DB.
        Must not block the main thread — use api.tasks.run_background() for I/O.
        """

    def on_data_updated(self, node_data: Dict[str, Any]) -> None:
        """Called when a node of this type is updated in the database.

        :param node_data: The entity's current properties dict.
        """

    def get_custom_ui(self, node_data: Dict[str, Any]) -> "Optional[QWidget]":
        """Return a QWidget to embed inside the workspace node, or None.

        Called once at node creation time.  The returned widget must update its
        own internal state in response to signals; it is not recreated on data
        changes.

        :param node_data: The entity's current properties dict at creation time.
        """
        return None
