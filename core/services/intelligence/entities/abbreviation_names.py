"""Short forms named by the document's abbreviation key (S15).

Every whole-word, case-exact use of a key ("TI", "AGN-T", "BDNF") becomes a mention of the
entity the key stands for, named by its full name ("Testaments of Ixil") and typed from it:
organisation or work by its head words (``abbreviation_type_<TYPE>`` packs), concept when
the full name is a lower-case term, otherwise left untyped. Keys are authoritative: their
mentions skip the name filters and usage typing, win over overlapping spans ("ANEY 1826ii"
-> "ANEY"), and occurrences of the full name itself join the same entity.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from core.models.entity_categories import TYPE_LABELS
from core.models.intelligence_models import EntityMention
from core.services.intelligence.entity_extractor import normalize_key

EXTRACTOR_ID = "abbreviation_key"
TYPE_PACK = "abbreviation_type_"
KEY_CONFIDENCE = 0.9
LOWERCASE_SHARE = 0.5


def expansion_type(name: str, registry: Any = None) -> str:
    for kind in TYPE_LABELS:
        for pack in registry.get_packs_for_type(TYPE_PACK + kind) if registry else []:
            if any(p.search(name) for p in pack.compiled):
                return kind
    if registry is None:
        from core.models.extraction_patterns import ABBREVIATION_TYPE_CUES
        for kind, pattern in ABBREVIATION_TYPE_CUES.items():
            if re.search(pattern, name):
                return kind
    words = re.findall(r"[^\W\d_]+", name)
    lower = sum(word[:1].islower() for word in words)
    return "CONCEPT" if words and lower / len(words) > LOWERCASE_SHARE else "UNKNOWN"


class KeyExtractor:
    """Finds the document's keys in each sentence (built per run from its key)."""
    id = EXTRACTOR_ID
    layer = 0

    def __init__(self, rows: List[Dict[str, Any]], registry: Any = None) -> None:
        self.keys = []
        for row in rows:
            meta = row.get("metadata") or {}
            name = meta.get("name") or row.get("normalized_text") or ""
            key = row.get("exact_text") or ""
            if key and name:
                parent = meta.get("parent_key")
                parent_row = next((r for r in rows if parent and r.get("exact_text") == parent), None)
                # A section of an archive has its archive's type; its own head word would mislead.
                kind = expansion_type(((parent_row or {}).get("metadata") or {}).get("name") or name, registry)
                self.keys.append((key, name, kind, row["artifact_id"],
                                  re.compile(r"(?<![\w-])" + re.escape(key) + r"(?![\w]|-\w)")))
        self.keys.sort(key=lambda item: -len(item[0]))           # "AGN-T" before "AGN"
        self.entity_types = sorted({kind for _, _, kind, _, _ in self.keys})

    def extract(self, text: str, context: Any = None) -> List[EntityMention]:
        found, taken = [], []
        for key, name, kind, artifact_id, pattern in self.keys:
            for match in pattern.finditer(text):
                if any(match.start() < end and start < match.end() for start, end in taken):
                    continue
                taken.append((match.start(), match.end()))
                found.append(EntityMention(key, kind, char_start=match.start(), char_end=match.end(),
                                           normalized_key=normalize_key(name, kind), extractor_id=EXTRACTOR_ID,
                                           confidence=KEY_CONFIDENCE,
                                           metadata={"abbreviation_of": name, "abbreviation_key": key,
                                                     "abbreviation_artifact_id": artifact_id}))
        return found


def authoritative(mention: EntityMention) -> bool:
    """Keys, and plugin extractors that mark their mentions ``metadata["authoritative"]``
    (e.g. a legal citation "Herrera v. Wyoming"), decide their own span and type."""
    return mention.extractor_id == EXTRACTOR_ID or bool(mention.metadata.get("authoritative"))


def split(mentions: List[EntityMention]) -> Tuple[List[EntityMention], List[EntityMention]]:
    """(other mentions, authoritative mentions): the latter skip filters and usage typing."""
    keyed = [m for m in mentions if authoritative(m)]
    return [m for m in mentions if not authoritative(m)], keyed


def merge(mentions: List[EntityMention], keyed: List[EntityMention]) -> List[EntityMention]:
    """Put key mentions back: overlapping spans give way, and the full name joins its key's entity."""
    if not keyed:
        return mentions
    spans: Dict[str, List[Tuple[int, int]]] = {}
    for mention in keyed:
        spans.setdefault(mention.sentence_id, []).append((mention.char_start, mention.char_end))
    names = {_fold(m.metadata["abbreviation_of"]): m for m in keyed if m.metadata.get("abbreviation_of")}
    kept = []
    for mention in mentions:
        if any(mention.char_start is not None and mention.char_start < end and start < mention.char_end
               for start, end in spans.get(mention.sentence_id, [])):
            continue
        owner = names.get(_fold(mention.surface_text))
        if owner is not None:
            mention.entity_type, mention.normalized_key = owner.entity_type, owner.normalized_key
            mention.metadata["abbreviation_key"] = owner.metadata["abbreviation_key"]
        kept.append(mention)
    # "suppress_only" spans (a case name, a code citation) are not entities themselves: they only
    # stop the names inside them ("Herrera" in "Herrera v. Wyoming") from becoming mentions.
    return kept + [m for m in keyed if not m.metadata.get("suppress_only")]


def _fold(text: str) -> str:
    return " ".join((text or "").casefold().split())
