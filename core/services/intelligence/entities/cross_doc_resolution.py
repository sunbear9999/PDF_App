"""Suggest that entities in different documents are the same thing.

Identical names of one type already share a canonical entity. Everything less
certain is only *proposed*, as a ``same_as_candidate`` edge with its reason, for
the owner to accept or reject: the same spelling typed differently, a surname
and the one full name it belongs to, an acronym and its expansion, and spelling
variants. Each proposal needs a unique partner; ambiguous ones abstain, an
explicit never-merge rule wins, and proposals stay few.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Set

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from core.services.intelligence.entities.project_lexicon import Owner, ProjectLexicon, core_words, well_formed

EDGE_TYPE = "same_as_candidate"
SPELLING_RATIO = 90            # whole-string similarity; word-set measures would pair "United States" with its courts
MIN_SPELLING_LENGTH = 7
MIN_ACRONYM = 2
MAX_ACRONYM = 6
MAX_SHARE = 0.05               # of a document's names
MIN_PROPOSALS = 5
MIN_SINGLE_WORD = 5            # one short word ("Act", "Supp") names too many things to propose on
_STOP = frozenset({"of", "the", "and", "for", "in", "on", "de", "la", "del"})
_DIGITS = re.compile(r"\d+")


@dataclass
class Proposal:
    first: Owner
    second: Owner
    rule: str
    score: float
    reason: str


def propose(lexicon: ProjectLexicon, source_id: str, blocked: Iterable = ()) -> List[Proposal]:
    """Proposals linking a name in ``source_id`` to one seen only in another document."""
    owners = [o for group in lexicon.by_surface.values() for o in group if _proposable(o)]
    found: Dict[tuple, Proposal] = {}
    for proposal in (*_type_conflicts(lexicon), *_surnames(owners), *_acronyms(owners), *_spellings(owners)):
        first, second = proposal.first, proposal.second
        if first.canonical_id == second.canonical_id or not _crosses(first, second, source_id):
            continue
        if frozenset((first.key, second.key)) in blocked:
            continue
        pair = tuple(sorted((first.canonical_id, second.canonical_id)))
        if pair not in found or found[pair].score < proposal.score:
            found[pair] = proposal
    mine = sum(source_id in o.sources for o in owners)
    budget = max(MIN_PROPOSALS, int(mine * MAX_SHARE))
    return sorted(found.values(), key=lambda p: (-p.score, p.first.key, p.second.key))[:budget]


def _proposable(owner: Owner) -> bool:
    """Well formed and not a short generic word; an acronym only for the acronym rule."""
    words = core_words(owner.surface)
    if not owner.canonical_id or not owner.strong():
        return False
    return _is_acronym(owner.surface) or well_formed(owner) and (len(words) > 1 or len(words[0]) >= MIN_SINGLE_WORD)


def _is_acronym(text: str) -> bool:
    return text.isupper() and text.isalpha() and MIN_ACRONYM <= len(text) <= MAX_ACRONYM


def _crosses(first: Owner, second: Owner, source_id: str) -> bool:
    """One of the pair is in this document and the pair spans more than one document."""
    both = first.sources | second.sources
    return source_id in both and len(both) > 1


def _type_conflicts(lexicon: ProjectLexicon) -> Iterable[Proposal]:
    for owners in lexicon.by_surface.values():
        strong = [o for o in owners if _proposable(o) and not _is_acronym(o.surface)]
        for index, first in enumerate(strong):
            for second in strong[index + 1:]:
                if first.entity_type != second.entity_type:
                    yield Proposal(first, second, "same_spelling", 0.9,
                                   f"Same spelling “{first.surface}”, typed {first.entity_type} in "
                                   f"{len(first.sources)} source(s) and {second.entity_type} in {len(second.sources)}.")


def _surnames(owners: List[Owner]) -> Iterable[Proposal]:
    people = [o for o in owners if o.entity_type == "PERSON"]
    full_by_last: Dict[str, Set[str]] = defaultdict(set)
    named: Dict[str, Owner] = {}
    for person in people:
        words = person.key.split()
        if len(words) > 1:
            full_by_last[words[-1]].add(person.key)
            named[person.key] = person
    for person in people:
        if " " not in person.key and len(full_by_last.get(person.key, ())) == 1:
            full = named[next(iter(full_by_last[person.key]))]
            yield Proposal(person, full, "surname", 0.8,
                           f"“{person.surface}” is the surname of the only {full.surface} named in this project.")


def _acronyms(owners: List[Owner]) -> Iterable[Proposal]:
    expansions: Dict[tuple, List[Owner]] = defaultdict(list)
    for owner in owners:
        words = [w for w in owner.surface.split() if w.casefold() not in _STOP]
        if len(words) >= MIN_ACRONYM and all(w[:1].isupper() for w in words):
            expansions[(owner.entity_type, "".join(w[0] for w in words))].append(owner)
    for owner in owners:
        text = owner.surface
        if _is_acronym(text):
            matches = expansions.get((owner.entity_type, text), [])
            if len(matches) == 1:
                yield Proposal(owner, matches[0], "acronym", 0.75,
                               f"“{text}” matches the initials of “{matches[0].surface}”.")


def _spellings(owners: List[Owner]) -> Iterable[Proposal]:
    blocks: Dict[tuple, List[Owner]] = defaultdict(list)
    for owner in owners:
        if len(owner.key) >= MIN_SPELLING_LENGTH:
            blocks[(owner.entity_type, owner.key[:2])].append(owner)
    for block in blocks.values():
        for index, first in enumerate(block):
            for second in block[index + 1:]:
                if _DIGITS.findall(first.key) != _DIGITS.findall(second.key):
                    continue             # "Title 25" and "Title 26" are different things
                score = fuzz.ratio(first.key, second.key)
                if score >= SPELLING_RATIO or Levenshtein.distance(first.key, second.key) <= 1:  # one OCR error
                    yield Proposal(first, second, "spelling", score / 100 * 0.7,
                                   f"“{first.surface}” and “{second.surface}” differ only slightly in spelling.")
