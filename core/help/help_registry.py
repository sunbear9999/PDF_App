"""
core/help/help_registry.py

Registry for HelpTopic objects. Provides registration, lookup, and full-text search.
IDs are namespaced ("core.X", "plugin.<id>.X", "pack.X") to avoid collisions.
"""
from __future__ import annotations

from typing import Optional

from core.help.models import HelpTopic


class HelpRegistry:
    """
    Central store for all HelpTopic objects.

    Thread-safety: not required — all mutations happen at startup or in the GUI thread.
    Plugin content is tagged with plugin_id so it can be removed cleanly on unload.
    """

    def __init__(self) -> None:
        self._topics: dict[str, HelpTopic] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, topic: HelpTopic) -> None:
        """Register or overwrite a topic. Raises ValueError on ID conflict from a different owner."""
        existing = self._topics.get(topic.id)
        if existing and existing.plugin_id != topic.plugin_id:
            raise ValueError(
                f"HelpTopic '{topic.id}' is already owned by plugin '{existing.plugin_id}'; "
                f"cannot overwrite from '{topic.plugin_id}'"
            )
        self._topics[topic.id] = topic

    def register_many(self, topics: list[HelpTopic]) -> None:
        for t in topics:
            self.register(t)

    def unregister(self, topic_id: str) -> None:
        self._topics.pop(topic_id, None)

    def remove_plugin_topics(self, plugin_id: str) -> None:
        """Remove all topics owned by the given plugin. Called on plugin unload."""
        stale = [tid for tid, t in self._topics.items() if t.plugin_id == plugin_id]
        for tid in stale:
            del self._topics[tid]

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, topic_id: str) -> Optional[HelpTopic]:
        return self._topics.get(topic_id)

    def lookup_by_feature(self, feature_id: str) -> Optional[HelpTopic]:
        """Return the first topic whose feature_id matches (used by UITargetRegistry → F1)."""
        for t in self._topics.values():
            if t.feature_id == feature_id:
                return t
        return None

    def get_by_category(self, category: str) -> list[HelpTopic]:
        return [t for t in self._topics.values() if t.category == category]

    def get_categories(self) -> list[str]:
        seen: set[str] = set()
        cats: list[str] = []
        for t in self._topics.values():
            if t.category not in seen:
                seen.add(t.category)
                cats.append(t.category)
        return cats

    def all_topics(self) -> list[HelpTopic]:
        return list(self._topics.values())

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, max_results: int = 20) -> list[HelpTopic]:
        """
        Simple weighted substring search across title, keywords, summary, and body.
        Returns results ranked by descending score.
        """
        q = query.lower().strip()
        if not q:
            return list(self._topics.values())[:max_results]

        scored: list[tuple[int, HelpTopic]] = []
        for t in self._topics.values():
            score = 0
            if q in t.title.lower():
                score += 10
            if any(q in kw.lower() for kw in t.keywords):
                score += 8
            if q in t.summary.lower():
                score += 5
            if q in t.body.lower():
                score += 2
            if q in t.category.lower():
                score += 3
            if score > 0:
                scored.append((score, t))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:max_results]]

    def __len__(self) -> int:
        return len(self._topics)
