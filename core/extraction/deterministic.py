"""
core/extraction/deterministic.py

Deterministic (regex/rule-based) entity extraction engine.

Exposes:
  - ExtractedMention      — one occurrence of an entity in the text
  - ExtractedEntityGroup  — all occurrences of a single deduplicated entity
  - ExtractorSpec         — descriptor for one extraction type (plugin-ready)
  - DeterministicExtractorRegistry — singleton registry; plugins call .register()

Built-in extractors registered at module import:
  - PersonOrgExtractor  →  entity.person_org
  - DateEventExtractor  →  entity.timeline_event
  - CitationExtractor   →  entity.source (with citation metadata)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from core.models.extraction_models import DocumentBlock
from core.services.extraction.date_extraction_service import DateExtractionService
from core.services.extraction.citation_text_adapter import citation_index_from_pages, bibliography_entries_from_text
from core.services.extraction.semantic_block_service import BIBLIOGRAPHY_HEADING_RE
from typing import Any, Callable, ClassVar, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ExtractedMention:
    text: str          # exact matched text in the document
    context: str       # full sentence containing the match
    page_num: int      # 0-based page index


@dataclass
class ExtractedEntityGroup:
    canonical_text: str              # normalised / deduplicated key shown in UI
    entity_type: str                 # e.g. "entity.person_org"
    mentions: List[ExtractedMention]
    extra_properties: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass
class ExtractorSpec:
    entity_type: str      # must match an OntologyRegistry key
    display_name: str     # shown in the UI dropdown
    extractor: Callable[  # fn(full_text, page_texts) -> list[ExtractedEntityGroup]
        [str, List[Tuple[int, str]]],
        List[ExtractedEntityGroup],
    ]
    plugin_id: str = ""   # set by plugins for bulk-cleanup on unload


class DeterministicExtractorRegistry:
    _extractors: ClassVar[Dict[str, ExtractorSpec]] = {}

    @classmethod
    def register(cls, spec: ExtractorSpec) -> None:
        cls._extractors[spec.entity_type] = spec

    @classmethod
    def unregister(cls, entity_type: str) -> None:
        cls._extractors.pop(entity_type, None)

    @classmethod
    def remove_by_plugin(cls, plugin_id: str) -> None:
        cls._extractors = {k: v for k, v in cls._extractors.items() if v.plugin_id != plugin_id}

    @classmethod
    def all_specs(cls) -> List[ExtractorSpec]:
        return list(cls._extractors.values())

    @classmethod
    def get(cls, entity_type: str) -> Optional[ExtractorSpec]:
        return cls._extractors.get(entity_type)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _sentence_for_span(text: str, start: int, end: int, max_chars: int = 600) -> str:
    """Return a 2–3 sentence window centred on the character span [start, end).

    Looks back up to one sentence boundary and forward up to two sentence
    boundaries so citations, people, and events all have enough surrounding
    context for both the UI and the LLM analysis prompt.
    """
    # Walk backward to find start of the sentence that contains `start`
    look_back = max(0, start - 500)
    pre = text[look_back:start]
    last_boundary = max(pre.rfind(". "), pre.rfind(".\n"), pre.rfind("! "), pre.rfind("? "))
    sent_start = look_back + last_boundary + 2 if last_boundary >= 0 else look_back

    # Walk forward: grab up to two sentence boundaries after the entity ends
    remaining = text[end: min(len(text), end + max_chars)]
    boundaries = []
    for pat in (". ", ".\n", "! ", "? "):
        idx = 0
        while True:
            pos = remaining.find(pat, idx)
            if pos == -1:
                break
            boundaries.append(pos + len(pat))
            idx = pos + 1
    boundaries.sort()
    # Use the second boundary when available (two sentences of trailing context)
    if len(boundaries) >= 2:
        sent_end = end + boundaries[1]
    elif len(boundaries) == 1:
        sent_end = end + boundaries[0]
    else:
        sent_end = min(end + max_chars, len(text))

    return text[sent_start:sent_end].strip()


def _page_for_offset(offset: int, page_offsets: List[Tuple[int, int, int]]) -> int:
    """Binary-search page_offsets ([(page_num, start, end), ...]) for a char offset."""
    lo, hi = 0, len(page_offsets) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        _, s, e = page_offsets[mid]
        if offset < s:
            hi = mid - 1
        elif offset >= e:
            lo = mid + 1
        else:
            return page_offsets[mid][0]
    return page_offsets[-1][0] if page_offsets else 0


def _build_full_text(page_texts: List[Tuple[int, str]]) -> Tuple[str, List[Tuple[int, int, int]]]:
    """Join page texts with newlines; return (full_text, page_offsets)."""
    parts = []
    offsets = []
    cursor = 0
    for page_num, page_text in page_texts:
        start = cursor
        parts.append(page_text)
        cursor += len(page_text) + 1  # +1 for the joining newline
        offsets.append((page_num, start, cursor))
    return "\n".join(p for _, p in page_texts), offsets


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# PersonOrgExtractor
# ---------------------------------------------------------------------------

_TITLE_PREFIXES = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sir|Lady|Lord|Senator|President|Captain|General|"
    r"Colonel|Major|Lieutenant|Sergeant|Rev|Reverend|Bishop|Pope|Sheikh|Saint|St)\b\.?"
)
_ORG_SUFFIXES = re.compile(
    r"\b(?:Inc|Corp|LLC|Ltd|GmbH|Co|Group|Foundation|Institute|University|College|"
    r"Hospital|Center|Centre|Bureau|Department|Ministry|Committee|Association|"
    r"Agency|Authority|Council|Commission|Society|Organization|Organisation)\b\.?"
)
_CAPITALIZED_WORD = re.compile(r"[A-Z][a-zéàèùâêîôûëïüç\-']+")
_NOISE_WORDS = frozenset({
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "The", "This", "That", "These", "Those", "There", "Their", "They",
    "Abstract", "Introduction", "Conclusion", "Discussion", "Results", "Methods",
    "References", "Bibliography", "Chapter", "Section", "Figure", "Table",
    "Appendix", "Index", "Preface", "Acknowledgements",
    "North", "South", "East", "West", "United", "States", "Kingdom",
    "American", "European", "Asian", "African", "Australian",
})


def _extract_persons(full_text: str, page_texts: List[Tuple[int, str]],
                     author_exclusions: frozenset) -> List[ExtractedEntityGroup]:
    _, page_offsets = _build_full_text(page_texts)

    # Pattern: optional title + 1-4 capitalized words
    pattern = re.compile(
        r"(?:(?:" + _TITLE_PREFIXES.pattern + r")\s+)?"
        r"(?:[A-Z][a-zéàèùâêîôûëïüç\-']+\.?\s+){1,3}"
        r"[A-Z][a-zéàèùâêîôûëïüç\-']+",
        re.VERBOSE,
    )

    raw_hits: List[Tuple[str, int, int]] = []  # (text, start, end)
    for m in pattern.finditer(full_text):
        name = m.group().strip()
        words = name.split()
        if len(words) < 2:
            continue
        if any(w in _NOISE_WORDS for w in words):
            continue
        # Skip names that are pure bibliography author strings (already in exclusions)
        norm = name.lower().strip(".,;")
        if any(_similar(norm, ex) > 0.85 for ex in author_exclusions):
            continue
        raw_hits.append((name, m.start(), m.end()))

    # Deduplicate: cluster by canonical form (lowercase, strip punctuation)
    groups: Dict[str, List[Tuple[str, int, int]]] = {}
    for name, start, end in raw_hits:
        key = re.sub(r"[^\w\s]", "", name.lower()).strip()
        # Fuzzy merge: find existing key within edit distance
        merged = False
        for existing_key in list(groups.keys()):
            if _similar(key, existing_key) > 0.88:
                groups[existing_key].append((name, start, end))
                merged = True
                break
        if not merged:
            groups[key] = [(name, start, end)]

    result = []
    for key, hits in groups.items():
        # Canonical text = most common surface form
        counts: Dict[str, int] = {}
        for name, _, _ in hits:
            counts[name] = counts.get(name, 0) + 1
        canonical = max(counts, key=counts.__getitem__)

        mentions = []
        seen_ctx: set = set()
        for name, start, end in hits:
            ctx = _sentence_for_span(full_text, start, end)
            if ctx in seen_ctx:
                continue
            seen_ctx.add(ctx)
            page_num = _page_for_offset(start, page_offsets)
            mentions.append(ExtractedMention(text=name, context=ctx, page_num=page_num))

        if mentions:
            # Auto-summary: first two unique context sentences joined, for the editable description field
            auto_desc = " ".join(dict.fromkeys(m.context[:200] for m in mentions[:2]))[:400]
            result.append(ExtractedEntityGroup(
                canonical_text=canonical,
                entity_type="entity.person_org",
                mentions=mentions,
                extra_properties={"role": "", "description": auto_desc},
            ))

    return sorted(result, key=lambda g: -len(g.mentions))


# Lazily-shared spaCy wrapper so Discovery person/org extraction uses trained
# NER (which distinguishes PERSON vs ORG and rejects non-entities) instead of a
# capitalization rule. Falls back to the regex extractor when spaCy is absent.
def _get_lang_service():
    # The shared pipeline: a private copy here loaded spaCy a second time.
    try:
        from core.services.extraction.language_feature_service import shared_language_service
        return shared_language_service()
    except Exception:
        return None


def _extract_persons_ner(full_text: str, page_texts: List[Tuple[int, str]],
                         author_exclusions: frozenset) -> List[ExtractedEntityGroup]:
    """spaCy-NER person/org extraction — same output shape as _extract_persons.

    Uses a trained classifier to separate PERSON/ORG and reject non-entities,
    fixing the case-heuristic conflation while preserving the Discovery save path.
    """
    lang = _get_lang_service()
    if not lang or not lang.is_available():
        return _extract_persons(full_text, page_texts, author_exclusions)
    _, page_offsets = _build_full_text(page_texts)
    entities = lang.filter_person_org_entities(lang.get_entities(full_text))

    groups: Dict[str, List[Tuple[str, str, int, int]]] = {}
    for surface, label, start, end in entities:
        name = (surface or "").strip()
        if len(name) < 3 or any(w in _NOISE_WORDS for w in name.split()):
            continue
        norm = re.sub(r"[^\w\s]", "", name.lower()).strip()
        if not norm or any(_similar(norm, ex) > 0.85 for ex in author_exclusions):
            continue
        merged = False
        for key in list(groups.keys()):
            if _similar(norm, key) > 0.88:
                groups[key].append((name, label, start, end))
                merged = True
                break
        if not merged:
            groups[norm] = [(name, label, start, end)]

    result = []
    for hits in groups.values():
        counts: Dict[str, int] = {}
        labels: Dict[str, int] = {}
        for name, label, _, _ in hits:
            counts[name] = counts.get(name, 0) + 1
            labels[label] = labels.get(label, 0) + 1
        canonical = max(counts, key=counts.__getitem__)
        dominant_label = max(labels, key=labels.__getitem__)
        mentions = []
        seen_ctx: set = set()
        for name, _label, start, end in hits:
            ctx = _sentence_for_span(full_text, start, end)
            if ctx in seen_ctx:
                continue
            seen_ctx.add(ctx)
            mentions.append(ExtractedMention(
                text=name, context=ctx, page_num=_page_for_offset(start, page_offsets)))
        if mentions:
            auto_desc = " ".join(dict.fromkeys(m.context[:200] for m in mentions[:2]))[:400]
            result.append(ExtractedEntityGroup(
                canonical_text=canonical,
                entity_type="entity.person_org",
                mentions=mentions,
                extra_properties={"role": dominant_label, "description": auto_desc},
            ))
    return sorted(result, key=lambda g: -len(g.mentions))


def _run_person_org_extractor(
    full_text: str, page_texts: List[Tuple[int, str]]
) -> List[ExtractedEntityGroup]:
    # Pre-collect bibliography author names for exclusion
    bib_authors = _collect_bibliography_authors(full_text)
    # Prefer trained NER; the capitalization heuristic remains as a fallback.
    return _extract_persons_ner(full_text, page_texts, bib_authors)


# ---------------------------------------------------------------------------
# DateEventExtractor
# ---------------------------------------------------------------------------

def _run_date_extractor(
    full_text: str, page_texts: List[Tuple[int, str]]
) -> List[ExtractedEntityGroup]:
    """Legacy result shape over the canonical contextual date producer."""
    blocks = [DocumentBlock(f"legacy_p{page}_{index}", "legacy", page,
                            "paragraph", text, text)
              for index, (page, text) in enumerate(page_texts)]
    groups = {}
    for item in DateExtractionService().extract_dates(blocks, "legacy"):
        if not item.metadata.get("timeline_eligible"):
            continue
        surface = item.exact_text
        group = groups.setdefault(item.normalized_date, ExtractedEntityGroup(
            canonical_text=surface, entity_type="entity.timeline_event",
            mentions=[], extra_properties={
                "date": item.normalized_date, "certainty": item.certainty,
                **item.metadata,
            },
        ))
        group.mentions.append(ExtractedMention(
            text=surface, context=item.context_text, page_num=item.location.page_num,
        ))
    for group in groups.values():
        group.extra_properties["description"] = " ".join(
            dict.fromkeys(m.context[:200] for m in group.mentions[:2]))[:400]
    return sorted(groups.values(), key=lambda g: g.extra_properties["date"])


# ---------------------------------------------------------------------------
# CitationExtractor
# ---------------------------------------------------------------------------

def _collect_bibliography_authors(full_text: str) -> frozenset:
    """Reuse bibliography authors for PersonOrg exclusion, after the final heading."""
    lines = full_text.splitlines()
    headings = [i for i, line in enumerate(lines) if BIBLIOGRAPHY_HEADING_RE.fullmatch(line)]
    if not headings:
        return frozenset()
    entries = bibliography_entries_from_text("\n".join(lines[headings[-1] + 1:]))
    return frozenset(author.casefold() for entry in entries for author in entry.authors)


def _run_citation_extractor(
    full_text: str, page_texts: List[Tuple[int, str]]
) -> List[ExtractedEntityGroup]:
    """Discovery representation of the shared citation index; no second parser."""
    index = citation_index_from_pages(page_texts)
    by_id = {citation.citation_id: citation for citation in index.in_text_citations}
    grouped = {}
    for match in index.citation_matches:
        if not match.is_ambiguous:
            grouped.setdefault(match.bibliography_entry_id, []).append(by_id[match.in_text_citation_id])
    result = [_legacy_citation_group(entry, grouped.get(entry.entry_id, []))
              for entry in index.bibliography_entries]
    for citation in index.in_text_citations:
        if citation.citation_id in index.unmatched_in_text_ids or any(
                match.in_text_citation_id == citation.citation_id and match.is_ambiguous
                for match in index.citation_matches):
            result.append(ExtractedEntityGroup(
                canonical_text=citation.raw_text, entity_type="entity.source",
                mentions=[_legacy_citation_mention(citation)],
                extra_properties={"citation_key": citation.raw_text,
                                  "unresolved": True, "bibliography_raw": ""},
            ))
    return sorted(result, key=lambda group: -len(group.mentions))


def _legacy_citation_mention(citation):
    return ExtractedMention(text=citation.raw_text, context=citation.context_text,
                            page_num=citation.location.page_num if citation.location else -1)


def _legacy_citation_group(entry, citations):
    key = f"[{entry.reference_number}]" if entry.reference_number else (
        f"{entry.first_author_surname}{entry.year or ''}" if entry.first_author_surname
        else entry.normalized_keys.get("authority") or entry.raw_text)
    return ExtractedEntityGroup(
        canonical_text=entry.title or entry.raw_text or key,
        entity_type="entity.source",
        mentions=[_legacy_citation_mention(citation) for citation in citations],
        extra_properties={"citation_key": key, "authors": entry.authors,
                          "year": entry.year or "", "title": entry.title or "",
                          "journal": entry.source_title or "", "bibliography_raw": entry.raw_text,
                          "bib_page_num": entry.location.page_num if entry.location else -1},
    )


# ---------------------------------------------------------------------------
# Register built-in extractors at import time
# ---------------------------------------------------------------------------

DeterministicExtractorRegistry.register(ExtractorSpec(
    entity_type="entity.person_org",
    display_name="People & Organizations",
    extractor=_run_person_org_extractor,
))

DeterministicExtractorRegistry.register(ExtractorSpec(
    entity_type="entity.timeline_event",
    display_name="Dates & Events",
    extractor=_run_date_extractor,
))

DeterministicExtractorRegistry.register(ExtractorSpec(
    entity_type="entity.source",
    display_name="Citations",
    extractor=_run_citation_extractor,
))
