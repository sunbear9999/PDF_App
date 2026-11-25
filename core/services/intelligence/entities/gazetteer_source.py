"""Place evidence from the GeoNames gazetteer.

A name in the gazetteer is not yet a place: "Maya", "Pedro" and "Court" are
all towns somewhere. A mention earns a LOCATION vote only when the document
supports that reading:

* the mention follows a locative cue ("in", "near", "from" ...), or
* one of the places carrying the name lies in a region (country + first-level
  area) where the document's other unambiguous gazetteer names cluster
  ("Tekanto" among Mérida, Izamal, Motul -> Yucatán).

A one-word name the document also writes as an ordinary lowercase word
("court", "reading") gets no vote, and person cues still decide over place
evidence in ``type_resolution``. The source only votes; usage decides.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Callable, Dict, List, Set, Tuple

from core.models.intelligence_models import EntityMention
from core.services.intelligence.entities.evidence_sources import add_vote, mark_assessed

SOURCE_ID = "gazetteer"
VOTE_WEIGHT = 2.0
CANDIDATE_TYPES = frozenset({"LOCATION", "PERSON", "ORG", "NORP", "UNKNOWN", "FAC", "PRODUCT", "WORK"})
MAX_UNAMBIGUOUS = 2          # a name with at most this many regions anchors the document's regions
MIN_REGION_SUPPORT = 2       # distinct anchor names a region needs before it supports others

_WORD = re.compile(r"[^\W\d_]+")
MIN_LETTERS = 3
Region = Tuple[str, str]


class GazetteerEvidenceSource:
    id = SOURCE_ID
    setting_key = "use_gazetteer"

    def __init__(self, gazetteer, digest: Callable[[], str], pattern_registry=None) -> None:
        from core.services.intelligence.entities.type_resolution import usage_cues
        self.gazetteer = gazetteer
        cues = usage_cues(pattern_registry)
        self._locative, self._citation = cues["locative_before"], cues["citation_after"]
        self._digest = digest

    def available(self) -> bool:
        return self.gazetteer.is_available()

    def identity(self) -> str:
        return "geonames#" + (self._digest() or "")[:16]

    def add_votes(self, mentions: List[EntityMention], texts: Dict[str, str]) -> None:
        words = {w for text in texts.values() for w in _WORD.findall(text)}
        regions = {surface: self._regions(surface) for surface in {m.surface_text for m in mentions
                                                                    if self._candidate(m, words)}}
        anchors = _document_regions(regions)
        for mention in mentions:
            found = regions.get(mention.surface_text)
            if not found or not self._candidate(mention, words):
                continue
            mark_assessed(mention, SOURCE_ID)
            text = texts.get(mention.sentence_id, "")
            before = text[max(0, (mention.char_start or 0) - 40):mention.char_start or 0]
            after = text[mention.char_end or 0:(mention.char_end or 0) + 40]
            if any(p.search(after) for p in self._citation):
                continue           # "in Lockhart 1992": a cited work, not a place
            shared = found & anchors
            if any(p.search(before) for p in self._locative):
                add_vote(mention, SOURCE_ID, "LOCATION", VOTE_WEIGHT, "GeoNames place after a locative word")
            elif shared:
                region = sorted(shared)[0]
                add_vote(mention, SOURCE_ID, "LOCATION", VOTE_WEIGHT,
                         f"GeoNames place in {region[0]}-{region[1]}, where other places named here lie")

    @staticmethod
    def _candidate(mention: EntityMention, words: Set[str]) -> bool:
        """Capitalised, not also an ordinary lowercase word here, and not a code:
        an all-caps name ("TI", "ALBERT") counts only when the document also
        writes it as a name ("HILO" beside "Hilo")."""
        surface = mention.surface_text.strip()
        if mention.entity_type not in CANDIDATE_TYPES or not surface[:1].isupper() or len(surface) < MIN_LETTERS:
            return False
        if " " not in surface and surface.casefold() in words:
            return False
        if surface.isupper():
            return any(w.casefold() == surface.casefold() and not w.isupper() for w in words)
        return True

    def _regions(self, surface: str) -> Set[Region]:
        return {(p.country, p.admin1) for p in self.gazetteer.lookup(surface)}


def _document_regions(regions: Dict[str, Set[Region]]) -> Set[Region]:
    """Regions supported by several distinct names that are themselves unambiguous."""
    support: Counter = Counter()
    for found in regions.values():
        if 0 < len(found) <= MAX_UNAMBIGUOUS:
            support.update(found)
    return {region for region, count in support.items() if count >= MIN_REGION_SUPPORT}
