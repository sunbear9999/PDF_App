"""Nominal coreference: "the Tribe" is a mention of the Crow Tribe named just before.

A definite description ("the"/"this" + a head noun from the ``nominal_heads``
pack) becomes a mention of the entity it refers back to only when exactly one
entity named within the recent window of the same section ends in that head.
Otherwise it abstains. When a document uses the bare description as a name in
its own right ("The Court" in an opinion means the authoring court), it is not
re-linked. Pronouns are never linked. The mention spans the head word, without
the determiner.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Tuple

from core.models.extraction_patterns import NOMINAL_HEADS
from core.models.intelligence_models import EntityMention
from core.services.intelligence.entities.recognize import sentence_id

COREF_WINDOW = 20            # sentences an antecedent stays available
COREF_CONFIDENCE = 0.6
MAX_HEAD_REFERENTS = 2      # more distinct names sharing a head: the description is ambiguous
EXTRACTOR_ID = "nominal_coref"
LINKABLE_TYPES = frozenset({"ORG", "LOCATION", "LAW", "NORP", "FAC", "EVENT", "WORK"})
_DETERMINER = r"\b(?:the|The|this|This)\s+"


def link_nominals(mentions: List[EntityMention], records: List[Dict[str, Any]],
                  templates: Dict[str, EntityMention], registry: Any = None) -> List[EntityMention]:
    heads = _heads(registry)
    if not heads or not records:
        return []
    pattern = re.compile(_DETERMINER + r"(" + "|".join(sorted(heads, key=len, reverse=True)) +
                         r")\b(?!\s+(?:of\s+)?[A-Z])(?!-)")
    standalone = {m.surface_text.split()[-1].casefold() for m in mentions
                  if re.fullmatch(_DETERMINER.strip() + r"?\s*\w+", m.surface_text) and m.surface_text.split()[-1] in heads}
    standalone |= _crowded_heads(mentions, heads)
    by_sentence: Dict[str, List[EntityMention]] = {}
    for mention in mentions:
        by_sentence.setdefault(mention.sentence_id, []).append(mention)
    recent: List[Tuple[int, str, EntityMention]] = []
    added: List[EntityMention] = []
    for index, row in enumerate(sorted(records, key=_reading_order)):
        key, text = sentence_id(row), row.get("exact_text") or ""
        section = " > ".join(row.get("section_path") or [])
        own = by_sentence.get(key, [])
        for match in pattern.finditer(text):
            head = match.group(1)
            if head.casefold() in standalone or _covered(own, match.start(1), match.end(1)):
                continue
            antecedent = _antecedent(recent, index, section, head)
            if antecedent is not None and key in templates:
                added.append(_mention(templates[key], antecedent, match.start(1), match.end(1), head))
        recent.extend((index, section, m) for m in own if m.entity_type in LINKABLE_TYPES)
        recent = [item for item in recent if index - item[0] < COREF_WINDOW]
    return added


def _antecedent(recent, index: int, section: str, head: str):
    """The single entity recently named in this section whose name ends in ``head``."""
    candidates = {(m.entity_type, m.normalized_key): m for seen, seen_section, m in recent
                  if seen_section == section and index - seen < COREF_WINDOW and _names_head(m, head)}
    return next(iter(candidates.values())) if len(candidates) == 1 else None


def _crowded_heads(mentions: List[EntityMention], heads: set) -> set:
    """Heads shared by many distinct names in the document ("Wyoming district court",
    "Race Horse court", ...): there the bare description ("the Court") usually means
    something else again, so it is never re-linked."""
    referents: Dict[str, set] = {}
    for mention in mentions:
        for head in heads:
            if _names_head(mention, head):
                referents.setdefault(head.casefold(), set()).add((mention.entity_type, mention.normalized_key))
    return {head for head, names in referents.items() if len(names) > MAX_HEAD_REFERENTS}


def _names_head(mention: EntityMention, head: str) -> bool:
    words = [w for w in mention.surface_text.split() if w.casefold() not in {"the", "this"}]
    return len(words) >= 2 and words[-1].casefold() == head.casefold()


def _mention(template: EntityMention, antecedent: EntityMention, start: int, end: int, head: str) -> EntityMention:
    mention = copy.copy(template)
    mention.surface_text, mention.char_start, mention.char_end = head, start, end
    mention.entity_type, mention.normalized_key = antecedent.entity_type, antecedent.normalized_key
    mention.extractor_id, mention.confidence = EXTRACTOR_ID, COREF_CONFIDENCE
    mention.metadata = {**template.metadata, "method": "nominal_coref",
                        "coref": {"method": "nominal", "antecedent_surface": antecedent.surface_text,
                                  "antecedent_sentence_id": antecedent.sentence_id}}
    return mention


def _covered(mentions: List[EntityMention], start: int, end: int) -> bool:
    return any(m.char_start is not None and m.char_end is not None and m.char_start < end and start < m.char_end
               for m in mentions)


def _heads(registry: Any) -> set:
    pack = registry.get_pack("nominal_heads") if registry else None
    if pack is not None:
        words = set()
        for pattern in pack.compiled:
            words |= set(re.findall(r"[A-Z][a-z]+", pattern.pattern))
        if words:
            return words
    return set(NOMINAL_HEADS.split())


def _reading_order(row: Dict[str, Any]):
    locator = row.get("locator") or {}
    block = re.search(r"_b(\d+)$", str(locator.get("block_id") or ""))
    page = locator.get("page")
    return (page if page is not None else -1, int(block[1]) if block else 0, locator.get("char_start") or 0)
