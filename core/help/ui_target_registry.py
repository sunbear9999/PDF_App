"""
core/help/ui_target_registry.py

Maps stable string target IDs to live UI widgets, using weak references or
resolver callables to avoid keeping destroyed widgets alive.

Target IDs are code-level constants (e.g. "toolbar.add_pdf_btn"), never
visible text, translated strings, or widget hierarchy positions.
"""
from __future__ import annotations

import weakref
from typing import Callable, Optional, Union

from PySide6.QtWidgets import QWidget


# A stored ref is either a weakref to an existing widget, or a callable
# that returns one (for lazily created dock inner widgets).
_StoredRef = Union["weakref.ref[QWidget]", Callable[[], Optional[QWidget]]]


class UITargetRegistry:
    """
    Central registry mapping stable target IDs to QWidget instances.

    Widgets are stored as weakrefs; resolver callables are stored directly.
    Dead weakrefs are pruned on first access to prevent unbounded accumulation.
    """

    def __init__(self) -> None:
        self._targets: dict[str, _StoredRef] = {}
        self._topic_map: dict[str, str] = {}   # target_id → help topic_id

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_widget(
        self,
        target_id: str,
        widget: QWidget,
        topic_id: str = "",
    ) -> None:
        """Register a live widget by stable ID. Stores a weakref so the registry
        cannot prevent the widget from being garbage-collected."""
        self._targets[target_id] = weakref.ref(widget)
        if topic_id:
            self._topic_map[target_id] = topic_id

    def register_resolver(
        self,
        target_id: str,
        resolver: Callable[[], Optional[QWidget]],
        topic_id: str = "",
    ) -> None:
        """Register a callable that returns the widget on demand.
        Use this for dock inner widgets that may not exist yet at registration time."""
        self._targets[target_id] = resolver
        if topic_id:
            self._topic_map[target_id] = topic_id

    def unregister(self, target_id: str) -> None:
        self._targets.pop(target_id, None)
        self._topic_map.pop(target_id, None)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, target_id: str) -> Optional[QWidget]:
        """Return the live widget for target_id, or None if gone or unregistered."""
        ref = self._targets.get(target_id)
        if ref is None:
            return None
        if isinstance(ref, weakref.ref):
            widget = ref()
            if widget is None:
                del self._targets[target_id]  # prune dead ref
                self._topic_map.pop(target_id, None)
            return widget
        # callable resolver
        return ref()

    def get_topic_id(self, target_id: str) -> Optional[str]:
        """Return the help topic ID associated with the given target, if any."""
        return self._topic_map.get(target_id)

    def resolve_nearest_ancestor(self, widget: QWidget) -> Optional[str]:
        """
        Walk up the widget's parent chain and return the first registered target_id found.

        Used by F1 and What's This? to map a focused/clicked widget to the nearest
        registered feature region. Returns None if no registered ancestor is found.
        """
        # Build a set of currently live resolved widgets for fast lookup
        live: dict[int, str] = {}   # id(widget) → target_id
        for tid, ref in list(self._targets.items()):
            w = self.resolve(tid)
            if w is not None:
                live[id(w)] = tid

        w: Optional[QWidget] = widget
        while w is not None:
            if id(w) in live:
                return live[id(w)]
            parent = w.parent()
            w = parent if isinstance(parent, QWidget) else None
        return None

    def all_target_ids(self) -> list[str]:
        return list(self._targets.keys())
