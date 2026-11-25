"""Document-level consistency for entity mentions (part of refinement).

NER decides each mention from its own sentence, so one name can come out
PERSON in twenty sentences and PRODUCT or ORG in five, and noisy text (OCR,
typos) spells one name several ways. With the whole document in view:

* **Type consensus** — mentions of the same surface share the majority type
  when that majority is clear (≥ 60 % of at least three mentions). Types a
  rule assigned from explicit evidence (an acronym definition, a title) are
  kept.
* **Spelling variants** — a name token the vocabulary does not know
  ("Challonor", "Cltalloner") that is close to a name token the document uses
  often ("Challoner") resolves to the same entity (normalized key); the
  surface text stays exactly as printed.
* **Glued words** — an unknown token that is a function word fused to a known
  word ("Ifhe", "Ifyou", "Myyoungest") is an OCR/typesetting join, not a name.

``known_word`` is the language model's vocabulary lookup; without it the
vocabulary-based rules are skipped and only type consensus runs.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from typing import Callable, Dict, List, Optional

from core.models.intelligence_models import EntityMention
from core.services.intelligence.entity_extractor import normalize_key

KnownWord = Callable[[str], Optional[bool]]

_CONSENSUS_TYPES = {"PERSON", "ORG", "LOCATION", "NORP", "PRODUCT", "WORK", "EVENT", "FAC"}
_MIN_SHARE = 0.6
_MIN_MENTIONS = 3
_FUNCTION_WORDS = {"if", "he", "i", "my", "the", "to", "of", "in", "it", "is", "we", "me", "be", "so", "at",
                   "as", "on", "or", "an", "by", "do", "no", "up", "us", "and", "but", "for", "you", "she",
                   "her", "his", "was", "not", "all", "had", "has"}
_VARIANT_RATIO = 0.8
_MIN_VARIANT_LEN = 5
_FREQUENT = 3


_LOCATIVE_BEFORE = re.compile(r"\b(?:in|at|near|to|from|toward|towards|into|across|around|through|of)\s+(?:the\s+)?$", re.I)
_LOCATIVE_SHARE = 0.5
_PLACE_HEADS = re.compile(r"\s+(?:Valley|River|Mountains?|Mount|Lake|Bay|Island|Islands|County|Harbou?r|Beach|Point|"
                          r"Peninsula|Basin|Canyon|Creek|Falls|Forest|Desert|Coast|Head|Heights|Gulch|Plains?)$")


def place_evidence(mentions: List[EntityMention], texts: Dict[str, str], org_words) -> None:
    """Single-word names the document uses as places: mostly after a
    locative preposition ("in Waikiki", "to Kilauea"), or the stem of a
    place it names in full ("Nuuanu" beside "Nuuanu Valley")."""
    stems = {_PLACE_HEADS.sub("", m.surface_text.strip()) for m in mentions
             if m.entity_type == "LOCATION" and _PLACE_HEADS.search(m.surface_text.strip())}
    groups: Dict[str, List[EntityMention]] = defaultdict(list)
    for mention in mentions:
        surface = mention.surface_text.strip()
        if mention.entity_type in {"ORG", "PERSON"} and " " not in surface and not surface.isupper() \
                and not org_words.search(surface) and mention.metadata.get("type_evidence") is not True:
            groups[surface].append(mention)
    for surface, group in groups.items():
        locative = sum(1 for m in group if _LOCATIVE_BEFORE.search(texts.get(m.sentence_id, "")[:m.char_start or 0][-30:]))
        is_place = surface in stems or (len(group) >= _MIN_MENTIONS and locative / len(group) >= _LOCATIVE_SHARE
                                        and all(m.entity_type == "ORG" for m in group))
        if is_place:
            for mention in group:
                mention.metadata["retyped_from"] = mention.entity_type
                mention.metadata["retyped_by"] = "place_stem" if surface in stems else "locative_usage"
                mention.metadata["type_evidence"] = True
                mention.entity_type = "LOCATION"
                mention.normalized_key = normalize_key(mention.surface_text, "LOCATION")


def type_consensus(mentions: List[EntityMention]) -> None:
    groups: Dict[str, List[EntityMention]] = defaultdict(list)
    for mention in mentions:
        if mention.entity_type in _CONSENSUS_TYPES:
            groups[_surface_key(mention.surface_text)].append(mention)
    for group in groups.values():
        if len(group) < _MIN_MENTIONS:
            continue
        top, count = Counter(m.entity_type for m in group).most_common(1)[0]
        if count / len(group) < _MIN_SHARE:
            continue
        for mention in group:
            if mention.entity_type != top and mention.metadata.get("type_evidence") is not True:
                mention.metadata["retyped_from"] = mention.entity_type
                mention.metadata["retyped_by"] = "document_consensus"
                mention.entity_type = top
                mention.normalized_key = normalize_key(mention.surface_text, top)


def glued_words(surface: str, known_word: Optional[KnownWord]) -> bool:
    """"Ifhe" = "if" + "he": an unknown single token that splits into a
    function word and a known word."""
    token = surface.strip()
    if known_word is None or " " in token or len(token) < 4 or known_word(token) is not False:
        return False
    lower = token.casefold()
    return any(lower[:k] in _FUNCTION_WORDS and len(lower) - k >= 2 and known_word(lower[k:])
               for k in range(1, min(4, len(lower) - 1)))


class OcrNoise:
    """Recognised (OCR) text spells names it cannot read as plausible
    non-words ("Keallv", "Siie"). There, a one-off name made only of words the
    vocabulary does not know — and not a variant of a name the document uses
    often — is recognition noise. Clean text never applies this rule: a rare
    real surname in a born-digital source is kept."""

    def __init__(self, ocr_sentences, known_word: Optional[KnownWord], variants: Dict[str, str]) -> None:
        self._ocr, self._known, self._variants = set(ocr_sentences or ()), known_word, variants
        self._words: Dict[str, bool] = {}

    def unknown(self, word: str) -> bool:
        if word not in self._words:
            self._words[word] = self._known(word) is False
        return self._words[word]

    def unreliable(self, mention: EntityMention, occurrences: int) -> bool:
        if self._known is None or mention.sentence_id not in self._ocr or occurrences != 1:
            return False
        words = _tokens(mention.surface_text)
        return bool(words) and all(self.unknown(w) and w not in self._variants for w in words)


def spelling_variants(mentions: List[EntityMention], known_word: Optional[KnownWord]) -> Dict[str, str]:
    """Unknown name token → the frequent name token it misspells."""
    if known_word is None:
        return {}
    people = [m for m in mentions if m.entity_type == "PERSON"]
    tokens = Counter(token for m in people for token in _tokens(m.surface_text))
    frequent = [t for t, n in tokens.items() if n >= _FREQUENT and len(t) >= _MIN_VARIANT_LEN]
    mapping: Dict[str, str] = {}
    for token, count in tokens.items():
        if count >= _FREQUENT or len(token) < _MIN_VARIANT_LEN or known_word(token) is not False:
            continue
        close = [(difflib.SequenceMatcher(None, token.casefold(), f.casefold()).ratio(), f) for f in frequent]
        close = [pair for pair in close if pair[0] >= _VARIANT_RATIO]
        if close:
            mapping[token] = max(close)[1]
    return mapping


def merge_spelling_variants(mentions: List[EntityMention], mapping: Dict[str, str]) -> None:
    for mention in (m for m in mentions if m.entity_type == "PERSON"):
        words = _tokens(mention.surface_text)
        fixed = [mapping.get(word, word) for word in words]
        if fixed != words:
            mention.metadata["spelling_variant_of"] = " ".join(fixed)
            mention.normalized_key = normalize_key(" ".join(fixed), "PERSON")


def _tokens(surface: str) -> List[str]:
    return re.findall(r"[^\W\d_][\w’'\-]*", surface or "")


def _surface_key(surface: str) -> str:
    return re.sub(r"^the\s+", "", " ".join((surface or "").split()), flags=re.I).casefold()
