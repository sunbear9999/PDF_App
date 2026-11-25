"""Typing evidence from optional resources (gazetteer, GLiNER, plugins).

An evidence source adds weighted type votes to mentions; it never sets a
type. ``type_resolution`` turns the votes into per-name *rates* next to the
document's usage rates, so a source can tip a decision the text already
leans towards but cannot decide alone, and repetition cannot inflate it.

A vote is ``{"source", "type", "weight", "reason"}`` appended to
``mention.metadata["evidence_votes"]``; every mention a source examined is
listed under ``evidence_assessed`` (voted or not), so support is a rate over
what each source actually looked at. A source is active only when its
setting is on and its resource is installed; active sources' identities
enter the Entities producer identity so stages re-run when they change.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from core.models.intelligence_models import EntityMention

VOTES_KEY = "evidence_votes"
ASSESSED_KEY = "evidence_assessed"


@runtime_checkable
class EntityEvidenceSource(Protocol):
    id: str
    setting_key: str        # project setting that switches it on ("" = always on)

    def available(self) -> bool: ...

    def identity(self) -> str: ...

    def add_votes(self, mentions: List[EntityMention], texts: Dict[str, str]) -> None: ...


def add_vote(mention: EntityMention, source: str, entity_type: str, weight: float, reason: str) -> None:
    mark_assessed(mention, source)
    mention.metadata.setdefault(VOTES_KEY, []).append(
        {"source": source, "type": entity_type, "weight": weight, "reason": reason})


def mark_assessed(mention: EntityMention, source: str) -> None:
    assessed = mention.metadata.setdefault(ASSESSED_KEY, [])
    if source not in assessed:
        assessed.append(source)


class EntityEvidenceRegistry:
    def __init__(self) -> None:
        self._sources: Dict[str, EntityEvidenceSource] = {}
        self._owners: Dict[str, Optional[str]] = {}

    def register(self, source: EntityEvidenceSource, plugin_id: Optional[str] = None) -> None:
        if not isinstance(source, EntityEvidenceSource):
            raise TypeError("Evidence sources need id, setting_key, available(), identity() and add_votes().")
        self._sources[source.id] = source
        self._owners[source.id] = plugin_id

    def remove_by_plugin(self, plugin_id: str) -> None:
        for source_id in [k for k, owner in self._owners.items() if owner == plugin_id]:
            self._sources.pop(source_id, None)
            self._owners.pop(source_id, None)

    def all(self) -> List[EntityEvidenceSource]:
        return list(self._sources.values())

    def active(self, settings: Optional[Dict[str, Any]]) -> List[EntityEvidenceSource]:
        settings = settings or {}
        return [s for s in self._sources.values()
                if (not s.setting_key or settings.get(s.setting_key)) and _safe(s.available)]

    def identity(self, settings: Optional[Dict[str, Any]]) -> str:
        """Digest of the active sources (empty when none is active)."""
        parts = sorted(f"{s.id}={_safe(s.identity) or ''}" for s in self.active(settings))
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16] if parts else ""

    def apply(self, mentions: List[EntityMention], texts: Dict[str, str], settings: Optional[Dict[str, Any]]) -> None:
        for source in self.active(settings):
            source.add_votes(mentions, texts)


def _safe(call):
    try:
        return call()
    except Exception:
        return None
