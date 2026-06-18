"""
core/help/tutorial_registry.py

Registry for TutorialDefinition objects. Mirrors HelpRegistry's pattern exactly.
"""
from __future__ import annotations

from typing import Optional

from core.help.models import TutorialDefinition


class TutorialRegistry:
    """
    Central store for all TutorialDefinition objects.

    Plugin content is tagged with plugin_id so it can be removed cleanly on unload.
    """

    def __init__(self) -> None:
        self._tutorials: dict[str, TutorialDefinition] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tutorial: TutorialDefinition) -> None:
        """Register or overwrite a tutorial. Raises ValueError on ID conflict from a different owner."""
        existing = self._tutorials.get(tutorial.id)
        if existing and existing.plugin_id != tutorial.plugin_id:
            raise ValueError(
                f"TutorialDefinition '{tutorial.id}' is already owned by plugin "
                f"'{existing.plugin_id}'; cannot overwrite from '{tutorial.plugin_id}'"
            )
        self._tutorials[tutorial.id] = tutorial

    def register_many(self, tutorials: list[TutorialDefinition]) -> None:
        for t in tutorials:
            self.register(t)

    def unregister(self, tutorial_id: str) -> None:
        self._tutorials.pop(tutorial_id, None)

    def remove_plugin_tutorials(self, plugin_id: str) -> None:
        """Remove all tutorials owned by the given plugin. Called on plugin unload."""
        stale = [tid for tid, t in self._tutorials.items() if t.plugin_id == plugin_id]
        for tid in stale:
            del self._tutorials[tid]

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, tutorial_id: str) -> Optional[TutorialDefinition]:
        return self._tutorials.get(tutorial_id)

    def get_by_category(self, category: str) -> list[TutorialDefinition]:
        return [t for t in self._tutorials.values() if t.category == category]

    def get_categories(self) -> list[str]:
        seen: set[str] = set()
        cats: list[str] = []
        for t in self._tutorials.values():
            if t.category not in seen:
                seen.add(t.category)
                cats.append(t.category)
        return cats

    def all_tutorials(self) -> list[TutorialDefinition]:
        return list(self._tutorials.values())

    def __len__(self) -> int:
        return len(self._tutorials)
