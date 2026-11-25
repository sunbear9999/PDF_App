"""Names a project has established, for finding them in its other documents.

Built only from *direct* mentions (a model or rule found the name; nothing
copied from elsewhere), so linking output can never feed back into the table
and reruns converge. A name may be applied to another document only when it is
strong enough and has a single owner across the project.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from core.services.intelligence.entities.surfaces import folded

MIN_DIRECT = 2                # or at least one mention whose type was settled from usage
MIN_LETTERS = 3
MIN_CAPS_LETTERS = 5          # shorter all-caps names are usually codes ("TI") with other meanings elsewhere
ARTICLES = frozenset({"the", "this", "that"})
# A name never ends in one of these ("APPEALS OF", "Department of"): a cut-off fragment.
TRAILING_FUNCTION_WORDS = frozenset({"of", "the", "and", "for", "in", "on", "to", "de", "del", "la", "v", "vs"})
# Types that name one thing wherever they appear. UNKNOWN is untyped; a bare
# surname ("Taylor") or first name may be a different person in another document.
APPLICABLE_TYPES = frozenset({"PERSON", "ORG", "LOCATION", "NORP", "LANGUAGE", "LAW", "FAC", "EVENT", "WORK", "PRODUCT",
                              "METHOD", "DATASET"})


@dataclass
class Owner:
    entity_type: str
    key: str
    canonical_id: str
    surface: str
    direct: int = 0
    settled: int = 0
    sources: Set[str] = field(default_factory=set)

    def strong(self) -> bool:
        return self.direct >= MIN_DIRECT or self.settled > 0


@dataclass
class ProjectLexicon:
    by_surface: Dict[str, List[Owner]] = field(default_factory=dict)

    def applicable(self, source_id: str) -> List[Owner]:
        """Names another document established that this document may receive."""
        return [owners[0] for owners in self.by_surface.values()
                if len(owners) == 1 and owners[0].sources - {source_id} and _applicable(owners[0])]

    def digest(self) -> str:
        table = sorted((surface, o.entity_type, o.key) for surface, owners in self.by_surface.items()
                       if len(owners) == 1 and _applicable(owners[0]) for o in owners)
        return hashlib.sha256(repr(table).encode("utf-8", "replace")).hexdigest()


def build_lexicon(rows) -> ProjectLexicon:
    found: Dict[Tuple[str, str, str, str], Owner] = {}
    for surface, kind, key, canonical_id, source_id, count, settled in rows:
        surface = str(surface or "").strip()
        if not surface or not kind or not key or not source_id:
            continue
        owner = found.setdefault((folded(surface), kind, key, canonical_id or ""),
                                 Owner(kind, key, canonical_id or "", surface))
        owner.direct += int(count or 0)
        owner.settled += int(settled or 0)
        owner.sources.add(source_id)
    lexicon = ProjectLexicon()
    for (surface, *_), owner in found.items():
        lexicon.by_surface.setdefault(surface, []).append(owner)
    return lexicon


def core_words(surface: str) -> List[str]:
    """The name without a leading article ("The Court" -> ["Court"])."""
    words = surface.split()
    while len(words) > 1 and words[0].casefold() in ARTICLES:
        words = words[1:]
    return words


def well_formed(owner: Owner) -> bool:
    """Strong, capitalised and not a fragment ("U ted States", "d") or an all-caps word."""
    words, letters = core_words(owner.surface), sum(c.isalpha() for c in owner.surface)
    if owner.entity_type not in APPLICABLE_TYPES or not owner.strong() or letters < MIN_LETTERS:
        return False
    if not words[0][:1].isupper() and not words[0][:1].isdigit():
        return False
    if words[-1].casefold().rstrip(".") in TRAILING_FUNCTION_WORDS:
        return False
    if any(len(w) == 1 and w.isalpha() for w in words):
        return False                 # a lone letter without a period is an OCR or layout fragment
    if owner.surface.isupper() and (letters < MIN_CAPS_LETTERS or len(words) == 1):
        return False
    return True


def _applicable(owner: Owner) -> bool:
    """Well formed and not a bare surname, which names different people in different documents."""
    return well_formed(owner) and not (owner.entity_type == "PERSON" and len(core_words(owner.surface)) < 2)


class LexiconDigest:
    """``StageSpec.project_inputs`` provider; recomputes only when the lexicon can have changed."""

    def __init__(self) -> None:
        self._seen = (None, "")

    def __call__(self, db) -> str:
        version = db.entity_lexicon_version()
        if version != self._seen[0]:
            self._seen = (version, build_lexicon(db.entity_lexicon_rows()).digest())
        return self._seen[1]
