"""Keep only names in the entity stream.

Dates, times, money and quantities belong to the Dates stage and tables;
document labels and citation short forms ("Table 7.4", "Appendix E", "Ibid.")
are cross-reference targets. Neither is an entity. In OCR text, a digit read in
place of a letter ("Tih6", "L6pez") joins the name it misspells, but only when
that name already occurs in the same document.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from core.models.entity_categories import VALUE_TYPES
from core.models.extraction_patterns import CROSS_REF_LABEL, LABELLED_CAPTION
from core.models.intelligence_models import EntityMention
from core.services.intelligence.entity_extractor import normalize_key

LABEL_PACK = "cross_ref_label"
# Digits OCR commonly reads for letters; tried only inside alphabetic words.
OCR_DIGIT_LETTERS = {"0": "oó", "1": "lí", "5": "s", "6": "oó", "8": "B"}
_DIGIT_IN_WORD = re.compile(r"(?<=[^\W\d_])\d|\d(?=[^\W\d_])")


def label_patterns(registry: Any = None) -> List[re.Pattern]:
    packs = registry.get_packs_for_type(LABEL_PACK) if registry else []
    return [pattern for pack in packs for pattern in pack.compiled] or [re.compile(CROSS_REF_LABEL)]


_CAPTION = re.compile(LABELLED_CAPTION)


def is_label(surface: str, patterns: Iterable[re.Pattern]) -> bool:
    text = " ".join(surface.split())
    return bool(_CAPTION.match(text)) or any(pattern.match(text) for pattern in patterns)


# A citation short form glued to the next name: "Ibid. The District Court".
_LEADING_SHORT_FORM = re.compile(r"(?i)^(?:ibid|id)\.\s+(?=\S)")


# An author-date reference read as one name: "Farriss 1984", "Lockhart 1992: 176".
_AUTHOR_DATE = re.compile(r"^(?P<name>[^\W\d_][^\d]*?[^\W\d_])[\s,]+(?:1[0-9]{3}|20[0-9]{2})[a-z]?(?P<page>\s*:\s*[\d\-–, ]+)?$")


def keep_names(mentions: List[EntityMention], registry: Any = None,
               texts: Optional[Dict[str, str]] = None) -> List[EntityMention]:
    patterns = label_patterns(registry)
    for mention in mentions:
        _strip_short_form(mention)
        _split_author_date(mention, (texts or {}).get(mention.sentence_id, ""))
        _trim_name(mention)
    return [mention for mention in mentions
            if mention.entity_type not in VALUE_TYPES and mention.surface_text.strip()
            and not is_label(mention.surface_text, patterns) and not _is_headline(mention.surface_text)
            and not is_fragment(mention.surface_text)]


_LETTER_RUN = re.compile(r"[^\W\d_]+")
_VOWEL = re.compile(r"(?i)[aeiouyáéíóúàèìòùäëïöüâêîôû]")


def is_fragment(surface: str) -> bool:
    """OCR or layout debris, not a name. Only names with no word of three letters are judged:
    one short word ("Vv", "E"), or words that repeat or have no vowel ("Vv Vv", "V Vv"). Capitals
    like an initialism ("TI", "DT") stay for the abbreviation key; "Li Po" stays a name."""
    runs = _LETTER_RUN.findall(surface)
    if not runs or max(len(run) for run in runs) >= 3:
        return False
    if surface.replace(" ", "").isupper() and sum(len(run) for run in runs) >= 2:
        return False
    if len(runs) == 1:
        return True
    return len(set(run.casefold() for run in runs)) < len(runs) or any(not _VOWEL.search(run) for run in runs)


def _strip_short_form(mention: EntityMention) -> None:
    match = _LEADING_SHORT_FORM.match(mention.surface_text)
    if not match or mention.char_start is None:
        return
    mention.surface_text = mention.surface_text[match.end():]
    mention.char_start += match.end()
    mention.normalized_key = normalize_key(mention.surface_text, mention.entity_type)


def merge_ocr_confusions(mentions: List[EntityMention], ocr_sentences: Iterable[str]) -> None:
    """Re-key a digit-for-letter misreading onto an existing clean name of the document."""
    ocr = set(ocr_sentences)
    if not ocr:
        return
    known: Dict[str, EntityMention] = {}
    for mention in mentions:
        if not _DIGIT_IN_WORD.search(mention.surface_text):
            known.setdefault(normalize_key(mention.surface_text), mention)
    for mention in mentions:
        if mention.sentence_id not in ocr or not _DIGIT_IN_WORD.search(mention.surface_text):
            continue
        target = _clean_match(mention.surface_text, known)
        if target is not None:
            mention.metadata["ocr_variant_of"] = target.surface_text
            mention.entity_type, mention.normalized_key = target.entity_type, target.normalized_key


def _clean_match(surface: str, known: Dict[str, EntityMention]) -> Optional[EntityMention]:
    candidates = {""}
    for index, char in enumerate(surface):
        replace = _DIGIT_IN_WORD.match(surface, index) and OCR_DIGIT_LETTERS.get(char)
        options = list(replace) if replace else [char]
        candidates = {prefix + option for prefix in candidates for option in options}
        if len(candidates) > 64:
            return None
    matches = {id(known[key]): known[key] for key in map(normalize_key, candidates) if key in known}
    return next(iter(matches.values())) if len(matches) == 1 else None


def _split_author_date(mention: EntityMention, text: str) -> None:
    """In citation context, keep the author: the year (and page) belong to the
    citation. "Windows 2000" in running prose is left alone."""
    match = _AUTHOR_DATE.match(mention.surface_text)
    if not match or mention.char_start is None or not _citation_context(mention, match, text):
        return
    mention.surface_text = match["name"]
    mention.char_end = mention.char_start + len(match["name"])
    mention.entity_type = "PERSON"
    mention.metadata["role"] = "cited_author"
    mention.normalized_key = normalize_key(mention.surface_text, mention.entity_type)


def _citation_context(mention: EntityMention, match: re.Match, text: str) -> bool:
    if mention.metadata.get("region") == "note" or match["page"]:
        return True
    before = text[:mention.char_start].rstrip()[-1:]
    after = text[mention.char_end or 0:].lstrip()[:1]
    return before in {"(", ";", "["} or after in {")", ";", ":", "]"}


# Proper names never span a colon ("The Court: The maximum") and never end in
# lowercase words ("Court al-lowed"); particles inside names ("de la") stay.
_PROPER_NAME_TYPES = frozenset({"PERSON", "ORG", "LOCATION", "NORP", "PRODUCT", "EVENT", "WORK", "LAW", "FAC", "UNKNOWN"})
_TRAILING_LOWER = re.compile(r"(?:\s+[^\W\d_A-Z][\w’'-]*)+$")
_HEADLINE_FUNCTION_WORDS = frozenset({"ON", "OF", "THE", "TO", "IN", "FOR", "AND", "BY", "AT", "FROM", "WITH"})
MIN_HEADLINE_WORDS = 4
MIN_HEADLINE_FUNCTION_WORDS = 2


def _trim_name(mention: EntityMention) -> None:
    if mention.entity_type not in _PROPER_NAME_TYPES or mention.char_start is None:
        return
    surface = mention.surface_text
    if ":" in surface:
        surface = surface.split(":", 1)[0].rstrip()
    trailing = _TRAILING_LOWER.search(surface)
    if trailing and trailing.start() > 0 and any(c.isupper() for c in surface[:trailing.start()]):
        surface = surface[:trailing.start()]
    if surface != mention.surface_text:
        mention.surface_text = surface
        mention.char_end = mention.char_start + len(surface)
        mention.normalized_key = normalize_key(surface, mention.entity_type)


def _is_headline(surface: str) -> bool:
    """An ALL-CAPS run with several function words is a headline, not a name."""
    words = surface.split()
    return (len(words) >= MIN_HEADLINE_WORDS and surface.isupper()
            and sum(word in _HEADLINE_FUNCTION_WORDS for word in words) >= MIN_HEADLINE_FUNCTION_WORDS)
