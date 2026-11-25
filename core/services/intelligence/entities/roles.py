"""Titles and roles are not people: "Justice", "Chief Justice", "the Governor" are TITLE.

A name made only of title words (``role_title`` and ``role_modifier`` packs; anything may
follow "of": "Secretary of the Interior", "Bishop of Yucatán") becomes a TITLE. A person
name that opens with a title ("Justice Gorsuch", "JUSTICE THOMAS", "Governor Pech") splits
into a TITLE mention for the title and a PERSON mention for the name, which keeps the
title in its metadata. Only capitalised titles count; "justice" in prose is not a name.
"""
from __future__ import annotations

import copy
import re
from typing import Any, List, Tuple

from core.models.extraction_patterns import ROLE_MODIFIER, ROLE_TITLE
from core.models.intelligence_models import EntityMention
from core.services.intelligence.entity_extractor import normalize_key

TITLE = "TITLE"
_WORD = re.compile(r"[^\W\d_][\w’'.-]*")
_ARTICLES = {"the", "a", "an"}
SPLIT_TYPES = {"PERSON", "UNKNOWN"}


def _patterns(registry: Any, kind: str, default: str):
    packs = registry.get_packs_for_type(kind) if registry else []
    return [p for pack in packs for p in pack.compiled] or [re.compile(default)]


class RoleWords:
    def __init__(self, registry: Any = None) -> None:
        self.titles = _patterns(registry, "role_title", ROLE_TITLE)
        self.modifiers = _patterns(registry, "role_modifier", ROLE_MODIFIER)

    def is_title(self, word: str) -> bool:
        word = word.strip(".’'").rstrip("s’'") if word.lower().endswith("’s") else word.strip(".")
        return any(p.match(word) for p in self.titles)

    def is_role_word(self, word: str) -> bool:
        return self.is_title(word) or any(p.match(word.strip(".")) for p in self.modifiers)


def leading_title(words: List[Tuple[int, int, str]], roles: RoleWords) -> int:
    """How many opening words form a title (0 when none): "Chief Justice Roberts" -> 2."""
    count = 0
    for _, _, word in words:
        if not word[:1].isupper() or not roles.is_role_word(word):
            break
        count += 1
    while count and not roles.is_title(words[count - 1][2]):
        count -= 1                      # "Associate" alone is not a title
    return count


def apply(mentions: List[EntityMention], registry: Any = None) -> List[EntityMention]:
    roles, out = RoleWords(registry), []
    for mention in mentions:
        words = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(mention.surface_text)]
        while words and words[0][2].lower() in _ARTICLES:
            words = words[1:]
        head = [w for w in words if w[2].lower() != "of"]
        before_of = words[:next((i for i, w in enumerate(words) if w[2].lower() == "of"), len(words))]
        if not words:
            out.append(mention)
            continue
        count = leading_title(before_of, roles)
        if count and count == len(before_of):              # only a title (plus "of ..." )
            _retype(mention)
            out.append(mention)
        elif (count and mention.entity_type in SPLIT_TYPES and count < len(head) and words[count][2][:1].isupper()
              and not any(c.isdigit() for c in mention.surface_text)):     # "Duke L. J. 999" is a citation
            out.extend(_split(mention, words, count))
        else:
            out.append(mention)
    return out


def _retype(mention: EntityMention) -> None:
    mention.entity_type = TITLE
    mention.normalized_key = normalize_key(mention.surface_text, TITLE)
    mention.metadata["retyped_by"] = "role_title"


def _split(mention: EntityMention, words, count) -> List[EntityMention]:
    """'Justice Gorsuch' -> TITLE 'Justice' + PERSON 'Gorsuch' (offsets stay exact)."""
    title_end, name_start = words[count - 1][1], words[count][0]
    if mention.char_start is None:
        return [mention]
    title = copy.deepcopy(mention)
    title.surface_text = mention.surface_text[:title_end]
    title.char_end = mention.char_start + title_end
    _retype(title)
    title_text = title.surface_text.strip()
    mention.surface_text = mention.surface_text[name_start:]
    mention.char_start += name_start
    mention.normalized_key = normalize_key(mention.surface_text, mention.entity_type)
    mention.metadata["title"] = title_text
    return [title, mention]
