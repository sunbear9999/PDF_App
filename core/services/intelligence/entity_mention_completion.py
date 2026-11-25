"""Document-level completion of entity mentions (after refinement).

Statistical NER misses entities it cannot see from one sentence: an acronym
the document defined pages earlier, an acronym listed beside one it did
recognise, an institution whose name it cut into pieces. These rules add or
merge mentions with the document in view; each added mention records the
rule that produced it in ``metadata["completed_by"]``.

* **Defined acronyms** — once the text defines ``artificial intelligence
  (AI)``, every later word-bounded ``AI`` is a mention of that entity, and for
  concepts every later use of the expansion too (same normalized key).
* **Coordinated acronyms** — in ``US FDA, UK MHRA or EU MDA`` the unknown
  heads take the type of the recognised one; the leading place codes stay
  places.
* **Institutions** — court names (``United States Court of Appeals for the
  Ninth Circuit``) and ``Department of X`` names replace the fragments NER
  produced inside them (``UNITED STATES COURT OF`` + ``APPEALS``).
"""
from __future__ import annotations

import copy
import re
from typing import Dict, List, Optional, Tuple

from core.models.intelligence_models import EntityMention
from core.services.intelligence.entity_extractor import normalize_key

_SMALL = r"(?:of|for|the|and)"
_CAP = r"(?:[A-Z][A-Za-z.'’\-]*)"
_COURT_RE = re.compile(
    # the "of X" tail stays on one line: a caption's next line is another name
    rf"\b(?:{_CAP}[ \t]+){{0,4}}COURT\b(?:[ \t]+OF[ \t]+(?:THE[ \t]+UNITED[ \t]+STATES|(?:CRIMINAL[ \t]+)?APPEALS|"
    rf"FEDERAL[ \t]+CLAIMS|{_CAP}(?:[ \t]+{_CAP})?))?"
    rf"(?:\s+(?:FOR|OF)\s+THE\s+(?:{_CAP}\s+){{0,3}}(?:CIRCUIT|DISTRICT)(?:\s+OF\s+{_CAP}(?:\s+{_CAP})?)?)?",
    re.I,
)
_DEPARTMENT_RE = re.compile(
    rf"\b(?:Dept\.|Department|Bureau|Ministry|Office)\s+of\s+(?:the\s+)?{_CAP}(?:\s+(?:and\s+)?{_CAP}){{0,3}}")
_COORDINATED = re.compile(r"(?:\b[A-Z]{2,6}\s+)?\b[A-Z]{2,6}\b(?:\s*(?:,|\bor\b|\band\b)\s*(?:[A-Z]{2,6}\s+)?[A-Z]{2,6}\b)+")
_ORG_HEAD = re.compile(r"\b(?:Inc|Corp|Company|University|Court|Department|Association|Tribe|Nation|Band)\b")
_PLACE_CODES = {"US", "USA", "UK", "EU", "UN"}
_ABSORBABLE = {"ORG", "LOCATION", "PERSON", "NORP", "WORK", "EVENT", "LAW"}


def complete(mentions: List[EntityMention], texts: Dict[str, str],
             acronyms: Dict[str, Tuple[str, str]],
             templates: Optional[Dict[str, EntityMention]] = None) -> List[EntityMention]:
    """``acronyms``: acronym → (type, expansion) the document defines.
    ``templates``: sentence id → a placeholder mention carrying that
    sentence's block/page/section, so sentences without any mention can
    still receive one."""
    by_sentence: Dict[str, List[EntityMention]] = {}
    for mention in mentions:
        by_sentence.setdefault(mention.sentence_id, []).append(mention)
    output: List[EntityMention] = []
    people = {m.surface_text.strip() for m in mentions if m.entity_type == "PERSON"}
    for sentence_id, found in by_sentence.items():
        text = texts.get(sentence_id, "")
        found = _absorb(found, text, _institutions(text))
        found = _split_pairs(found, text, people)
        found += _coordinated(found, text)
        output += found
    templates = {**(templates or {}), **{sid: found[0] for sid, found in by_sentence.items()}}
    output += _defined_acronym_mentions(templates, texts, acronyms, output)
    return output


def _institutions(text: str) -> List[Tuple[int, int]]:
    spans = []
    for match in _COURT_RE.finditer(text):
        start, end = _trim_court(text, match.start(), match.end())
        if end > start and _court_like(text[start:end]):
            spans.append((start, end))
    spans += [m.span() for m in _DEPARTMENT_RE.finditer(text)]
    return spans


def _trim_court(text: str, start: int, end: int) -> Tuple[int, int]:
    """Drop a leading article/sentence word: "The Wyoming courts" is no court name."""
    words = list(re.finditer(r"\S+", text[start:end]))
    while words and not words[0].group(0)[:1].isupper():
        words.pop(0)
    while words and words[0].group(0).casefold() in {"the", "a", "an", "this", "that", "in", "see"}:
        words.pop(0)
    return (start + words[0].start(), end) if words else (end, end)


def _court_like(surface: str) -> bool:
    """Capitalised words throughout and a qualifier besides "Court"."""
    words = surface.split()
    if len(words) < 2 or not words[-1][:1].isupper():
        return False
    return all(w[:1].isupper() or re.fullmatch(_SMALL, w, re.I) for w in words)


def _absorb(found: List[EntityMention], text: str, spans) -> List[EntityMention]:
    for start, end in spans:
        inside = [m for m in found if m.entity_type in _ABSORBABLE and m.char_start is not None
                  and m.char_start < end and start < (m.char_end or 0)]
        if not inside or any((m.char_start or 0) < start or (m.char_end or 0) > end for m in inside):
            continue            # nothing to replace, or a mention crosses the name's edge
        merged = copy.copy(inside[0])
        merged.metadata = {**inside[0].metadata, "completed_by": "institution_name",
                           "merged_from": [m.surface_text for m in inside]}
        _place(merged, text, start, end, "ORG")
        found = [m for m in found if m not in inside] + [merged]
    return found


_PAIR = re.compile(r"^([A-Z][\w’'\-]+)\s+(?:and|&)\s+([A-Z][\w’'\-]+)$")


def _split_pairs(found: List[EntityMention], text: str, people) -> List[EntityMention]:
    """"Wakea and Papa" read as one ORG is two people when either name is a
    person elsewhere in the document."""
    output = []
    for mention in found:
        pair = _PAIR.match(mention.surface_text.strip()) if mention.entity_type in {"ORG", "WORK"} else None
        if not pair or not (pair.group(1) in people or pair.group(2) in people) or mention.char_start is None:
            output.append(mention)
            continue
        for index in (1, 2):
            part = copy.copy(mention)
            part.metadata = {**mention.metadata, "completed_by": "split_pair"}
            start = mention.char_start + pair.start(index)
            _place(part, text, start, start + len(pair.group(index)), "PERSON")
            output.append(part)
    return output


def join_split_names(found: List[EntityMention], text: str) -> List[EntityMention]:
    """NER sometimes cuts one name in two ("James Many" PERSON + "White
    Horses" ORG). Two capitalised mentions separated by a single space, the
    first a PERSON, are one person."""
    ordered = sorted((m for m in found if m.char_start is not None), key=lambda m: m.char_start)
    for left, right in zip(ordered, ordered[1:]):
        if left.entity_type != "PERSON" or right.entity_type not in {"PERSON", "ORG", "LOCATION"}:
            continue
        if text[left.char_end:right.char_start] not in {" ", "\n"} or not right.surface_text[:1].isupper() \
                or _ORG_HEAD.search(right.surface_text):
            continue
        if left not in found or right not in found:
            continue
        merged = copy.copy(left)
        merged.metadata = {**left.metadata, "completed_by": "split_name",
                           "merged_from": [left.surface_text, right.surface_text]}
        _place(merged, text, left.char_start, right.char_end, "PERSON")
        found = [m for m in found if m is not left and m is not right] + [merged]
    return found


def _coordinated(found: List[EntityMention], text: str) -> List[EntityMention]:
    added = []
    for run in _COORDINATED.finditer(text):
        heads = _heads(run)
        typed = [m for m in found if m.char_start is not None and (m.char_start, m.char_end) in heads]
        kinds = {m.entity_type for m in typed if m.surface_text.isupper() and m.entity_type in {"ORG", "LAW"}}
        if len(kinds) != 1 or not typed:
            continue
        covered = {(m.char_start, m.char_end) for m in found}
        for start, end in heads:
            if (start, end) not in covered:
                mention = copy.copy(typed[0])
                mention.metadata = {**typed[0].metadata, "completed_by": "coordinated_acronym"}
                _place(mention, text, start, end, kinds.pop() if len(kinds) == 1 else typed[0].entity_type)
                kinds.add(mention.entity_type)
                added.append(mention)
    return added


def _heads(run) -> List[Tuple[int, int]]:
    """Acronyms in the run that are not a leading place code ("US" in "US FDA")."""
    tokens = [(m.start() + run.start(), m.end() + run.start(), m.group(0))
              for m in re.finditer(r"[A-Z]{2,6}", run.group(0))]
    heads = []
    for index, (start, end, token) in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        modifier = token in _PLACE_CODES and following and following[0] - end == 1
        if not modifier:
            heads.append((start, end))
    return heads


def _defined_acronym_mentions(templates, texts, acronyms, existing) -> List[EntityMention]:
    """Every use of a defined acronym (and of a concept's expansion)."""
    if not acronyms:
        return []
    covered: Dict[str, List[Tuple[int, int]]] = {}
    for mention in existing:
        if mention.char_start is not None:
            covered.setdefault(mention.sentence_id, []).append((mention.char_start, mention.char_end or 0))
    patterns = _acronym_patterns(acronyms)
    added = []
    for sentence_id, template in templates.items():
        text = texts.get(sentence_id, "")
        for pattern, etype, key in patterns:
            for match in pattern.finditer(text):
                if any(a < match.end() and match.start() < b for a, b in covered.get(sentence_id, [])):
                    continue
                mention = copy.copy(template)
                mention.metadata = {"evidence_artifact_id": template.metadata.get("evidence_artifact_id"),
                                    "completed_by": "defined_acronym"}
                mention.extractor_id = "entity_refinement"
                mention.confidence = 0.75
                _place(mention, text, match.start(), match.end(), etype, key)
                covered.setdefault(sentence_id, []).append(match.span())
                added.append(mention)
    return added


def _acronym_patterns(acronyms) -> List[Tuple[re.Pattern, str, str]]:
    patterns = []
    for acronym, (etype, expansion) in acronyms.items():
        key = normalize_key(expansion, etype) if expansion else normalize_key(acronym, etype)
        patterns.append((re.compile(rf"(?<![\w\-]){re.escape(acronym)}s?(?![\w\-])"), etype, key))
        if etype == "CONCEPT" and expansion and len(expansion.split()) >= 2:
            patterns.append((re.compile(rf"\b{re.escape(expansion)}\b", re.I), etype, key))
    return patterns


def _place(mention: EntityMention, text: str, start: int, end: int, etype: str,
           key: Optional[str] = None) -> None:
    mention.surface_text = text[start:end]
    mention.char_start, mention.char_end = start, end
    mention.entity_type = etype
    mention.normalized_key = key or normalize_key(mention.surface_text, etype)

