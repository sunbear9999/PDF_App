"""
EntityExtractor plugin interface + registry + built-in layer extractors.

Replaces the case-heuristic person/org logic (core/extraction/deterministic.py)
with a real layered, pluggable extractor. Each layer is an EntityExtractor
that produces typed EntityMentions from a block of text; the
EntityExtractionPipeline runs them in order, then resolves + relates.

Mirrors the DeterministicPassRegistry convention (instance-based registry,
plugin_id cleanup, ordered by a numeric ``layer``). Plugins register their own
extractors via ``api.intelligence.register_entity_extractor(extractor)``.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

from core.models.intelligence_models import EntityMention
from core.models.producer_models import MethodKind, ProducerIdentity


# spaCy NER label → canonical entity type. Distinct types are the whole point:
# PERSON and ORG are never collapsed (the bug the spec calls out).
from core.models.entity_categories import SPACY_LABEL_MAP


_NOISE_WORDS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "introduction",
    "conclusion", "abstract", "references", "appendix", "figure", "table",
    "chapter", "section", "part", "volume", "publication", "et al",
}
_TITLE_PREFIX_RE = re.compile(r"^(?:dr|prof|mr|mrs|ms|sir|hon|justice|judge)\.?\s+", re.I)

# ---- Surface-form sanitation ----------------------------------------------
# NER runs on text that still carries PDF artifacts. Without this pass the
# canonical entity list filled up with "¹ Institute for Environmental Studies",
# "Table 3", "respec-" and bare docket numbers.

# Reference markers glued to an entity by the PDF's superscript layer.
_MARKER_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹*†‡§¶"
# Digits are NOT stripped from the front: for QUANTITY and MONEY the number is
# the entity ("23 gigawatts"). Only markers and stray punctuation come off.
_EDGE_NOISE_RE = re.compile(
    rf"^[\s{_MARKER_CHARS}.,;:()\[\]]+|[\s{_MARKER_CHARS}.,;:]+$"
)
# "Table 3", "Figure 2b", "Equation 4", "Section 5" — document furniture that
# spaCy frequently labels LAW, PRODUCT or WORK.
_DOCUMENT_FURNITURE_RE = re.compile(
    r"^(?:table|tbl|figure|fig|eq|equation|section|sec|appendix|chapter|"
    r"part|volume|vol|note|footnote|step|panel|exhibit)"
    # A boundary after the word is required: without it the roman-numeral class
    # let "Volvo" read as "vol" + "vo".
    r"(?:\.|\b)(?:\s*[\divxlc]+[a-z]?)?$",
    re.I,
)
# A word broken across a line ("respec-", "Micro-") is a fragment, not a name.
_HYPHEN_FRAGMENT_RE = re.compile(r"[-–]\s*$")
# "The dataset" and "s generative artificial intelligence" are grammar, not
# names; a phrase must carry its own content words to be an entity.
_LEADING_FILLER_RE = re.compile(
    r"^(?:[\u2019']?s\s+|(?:the|this|that|these|those|an|a|our|its|their|his|"
    r"her|such|each|both|all|some|any|one|other|another)\s+)+",
    re.I,
)
_HAS_LETTERS_RE = re.compile(r"[^\W\d_]")
_MIN_SURFACE_CHARS = 2


def clean_surface(surface: str) -> str:
    """Strip PDF reference markers and collapse the line wraps PDFs leave behind."""
    return _EDGE_NOISE_RE.sub("", " ".join((surface or "").split()))


def strip_leading_filler(phrase: str) -> str:
    """Drop leading determiners and possessive fragments from a noun phrase."""
    return _LEADING_FILLER_RE.sub("", phrase or "").strip()


def is_noise_surface(surface: str) -> bool:
    """True when a surface form is a PDF artifact rather than a real entity."""
    if len(surface) < _MIN_SURFACE_CHARS or not _HAS_LETTERS_RE.search(surface):
        return True
    if _HYPHEN_FRAGMENT_RE.search(surface):
        return True
    if _DOCUMENT_FURNITURE_RE.match(surface):
        return True
    return surface.casefold().strip(".") in _NOISE_WORDS


@dataclass
class ExtractContext:
    """Minimal per-block context handed to each extractor layer."""
    doc_id: str = ""
    block_id: str = ""
    page_num: Optional[int] = None
    section_path: List[str] = field(default_factory=list)
    project_glossary: Dict[str, List[str]] = field(default_factory=dict)


@runtime_checkable
class EntityExtractor(Protocol):
    """Contract every entity-extraction layer implements (structural typing)."""
    id: str
    entity_types: List[str]
    layer: int

    def extract(self, text: str, context: ExtractContext) -> List[EntityMention]:
        ...


class EntityExtractorRegistry:
    """Registry of ordered entity-extraction layers."""

    def __init__(self) -> None:
        self._extractors: Dict[str, EntityExtractor] = {}
        self._plugin_ids: Dict[str, Optional[str]] = {}

    def register(self, extractor: EntityExtractor, plugin_id: Optional[str] = None) -> None:
        eid = getattr(extractor, "id", None)
        if not eid:
            raise ValueError("EntityExtractor requires a stable 'id'.")
        self._extractors[eid] = extractor
        self._plugin_ids[eid] = plugin_id

    def unregister(self, extractor_id: str) -> None:
        self._extractors.pop(extractor_id, None)
        self._plugin_ids.pop(extractor_id, None)

    def get(self, extractor_id: str) -> Optional[EntityExtractor]:
        return self._extractors.get(extractor_id)

    def all(self) -> List[EntityExtractor]:
        """Extractors in ascending layer order (NER=1, pattern=2, ...)."""
        return sorted(self._extractors.values(), key=lambda e: getattr(e, "layer", 100))

    def remove_by_plugin(self, plugin_id: str) -> None:
        for eid in [k for k, v in self._plugin_ids.items() if v == plugin_id]:
            self.unregister(eid)


# --------------------------------------------------------------------------
# Built-in layer 1: general NER
# --------------------------------------------------------------------------

class GeneralNERExtractor:
    """spaCy NER across all trained labels — separates PERSON/ORG/GPE/etc.

    This is a trained classifier distinguishing entity types, not a
    capitalization rule, which is what fixes people/org conflation.
    """
    id = "general_ner"
    version = "1.1"
    layer = 1
    entity_types = list(dict.fromkeys(SPACY_LABEL_MAP.values()))

    def __init__(self, language_feature_service=None) -> None:
        self._lang = language_feature_service

    def producer_identity(self) -> ProducerIdentity:
        """The spaCy pipeline that actually ran, or why none did."""
        info = self._lang.model_info() if self._lang is not None else {}
        if not info:
            return ProducerIdentity(
                producer_id=self.id, producer_version=self.version, method_kind=MethodKind.LEARNED.value,
                backend="spacy", fallback_reason="no spaCy model is installed",
            )
        return ProducerIdentity(
            producer_id=self.id, producer_version=self.version, method_kind=MethodKind.LEARNED.value,
            backend="spacy", model_id=f"spacy/{info['name']}",
            model_version=info.get("version", ""),
        )

    def extract(self, text: str, context: ExtractContext) -> List[EntityMention]:
        if not text or not text.strip() or self._lang is None:
            return []
        raw = self._lang.get_entities(text)
        return self._mentions_from_raw(raw)

    def extract_batch(
        self, texts: List[str], contexts: List[ExtractContext]
    ) -> List[List[EntityMention]]:
        if self._lang is None:
            return [[] for _ in texts]
        if not hasattr(self._lang, "get_entities_batch"):
            return [self.extract(text, context) for text, context in zip(texts, contexts)]
        batches = self._lang.get_entities_batch(texts)
        return [self._mentions_from_raw(raw) for raw in batches]

    def _mentions_from_raw(
        self, raw: List[Tuple[str, str, int, int]]
    ) -> List[EntityMention]:
        mentions: List[EntityMention] = []
        for surface, label, start, end in raw:
            entity_type = SPACY_LABEL_MAP.get(label)
            if not entity_type:
                continue
            clean = clean_surface(surface)
            if is_noise_surface(clean):
                continue
            # Keep offsets aligned with the trimmed surface so provenance still
            # points at the exact characters this mention covers.
            start += (surface or "").find(clean) if clean in (surface or "") else 0
            end = start + len(clean)
            mentions.append(EntityMention(
                surface_text=clean,
                entity_type=entity_type,
                char_start=start,
                char_end=end,
                normalized_key=normalize_key(clean, entity_type),
                extractor_id=self.id,
                confidence=0.85,
            ))
        return mentions


# --------------------------------------------------------------------------
# Built-in layer 2: domain patterns (METHOD/DATASET/METRIC/CONCEPT/WORK)
# --------------------------------------------------------------------------

# METRIC = a value + unit measured together, not a bare number.
_METRIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:%|percent|ms|sec(?:onds)?|GB|MB|KB|TB|kg|"
    r"accuracy|precision|recall|F1|BLEU|ROUGE|AUC|mAP|points?)(?!\w)"
    r"(?:\s+(?:accuracy|precision|recall|score|error|rate))?",
    re.I,
)


# Head nouns that name a research artifact. The capitalised noun phrase in
# front of the head is the entity: "Adam optimizer" → METHOD, "ImageNet corpus"
# → DATASET. Requiring a capitalised qualifier keeps "the proposed method" out.
_HEAD_NOUN_TYPES: Dict[str, Tuple[str, ...]] = {
    "METHOD": (
        "algorithm", "method", "methodology", "model", "framework",
        "architecture", "network", "approach", "technique", "classifier",
        "regression", "transformer", "estimator", "protocol", "procedure",
        "optimizer", "heuristic", "solver", "simulation",
    ),
    "DATASET": (
        "dataset", "data set", "corpus", "benchmark", "database", "registry",
        "survey", "census", "repository", "collection",
    ),
}
_HEAD_NOUN_RES: Dict[str, Any] = {
    # The scoped (?i:…) keeps the head noun case-insensitive ("ImageNet Dataset")
    # while the qualifier in front of it must still be capitalised.
    etype: re.compile(
        r"\b((?:[A-Z][\w\-]*\s+){1,4}(?i:" + "|".join(heads) + r"))\b"
    )
    for etype, heads in _HEAD_NOUN_TYPES.items()
}

# "Indian Gaming Regulatory Act (IGRA)" — a defined term and its acronym. The
# acronym's letters must be the initials of the words in front of it, which is
# what keeps this off every parenthetical in the document.
# "Indian Civil Rights Act (“ICRA”)", "major depressive disorder (MDD)".
ACRONYM_DEF_RE = re.compile(r"((?:[A-Za-z][\w\-]*\s+){1,8})\(\s*[“\"']?([A-Z]{2,8}s?)[”\"']?\s*\)")
_ACRONYM_DEF_RE = ACRONYM_DEF_RE
_ACRONYM_STOPWORDS = {"of", "for", "the", "and", "on", "in", "to", "a", "an", "&"}
_MAX_GLOSSARY_TERM_HITS = 200


def acronym_expansion(phrase: str, acronym: str) -> str:
    """Return the trailing words of *phrase* whose initials spell *acronym*.

    Trimming to exactly those words is what keeps the leading prose out of the
    term: "Under the Indian Gaming Regulatory Act (IGRA)" yields
    "Indian Gaming Regulatory Act". Function words may sit inside the
    expansion ("… Profiling for Alternative Sanction") but never start it, and
    hyphenated compounds count each part ("blood-brain barrier (BBB)"). An
    empty result means no match.
    """
    tokens = [(m.start(), m.group(0)) for m in re.finditer(r"[A-Za-z][A-Za-z0-9]*", phrase)]
    target = re.sub(r"[^a-z0-9]", "", acronym.rstrip("s").casefold())
    if len(target) < 2:
        return ""
    for size in range(1, min(len(tokens), len(target) + 4) + 1):
        tail = tokens[-size:]
        if tail[0][1].casefold() in _ACRONYM_STOPWORDS:
            continue
        initials = "".join(t[0] for _p, t in tail if t.casefold() not in _ACRONYM_STOPWORDS).casefold()
        if initials == target:
            return phrase[tail[0][0]:].strip()
    return _letter_sequence_expansion(phrase, tokens, target)


def _letter_sequence_expansion(phrase: str, tokens, target: str) -> str:
    """"lipopolysaccharides (LPS)", "microbeassociated molecular patterns
    (MAMPs)": the letters occur in order in ≤3 tail words that start with the
    acronym's first letter and are long enough to carry it."""
    if not 3 <= len(target) <= 6:
        return ""
    for size in range(1, min(3, len(tokens)) + 1):
        tail = tokens[-size:]
        word = "".join(t for _p, t in tail).casefold()
        if tail[0][1][0].casefold() != target[0] or len(word) < 3 * len(target):
            continue
        cursor = 0
        for letter in target:
            cursor = word.find(letter, cursor) + 1
            if cursor == 0:
                break
        else:
            return phrase[tail[0][0]:].strip()
    return ""


_acronym_expansion = acronym_expansion


def phrase_offset(container: str, phrase: str) -> int:
    """Start of *phrase* inside *container*, tolerating line-wrap whitespace."""
    pattern = r"\s+".join(re.escape(word) for word in phrase.split())
    found = None
    for found in re.finditer(pattern, container):
        pass
    return found.start() if found else -1


class DomainPatternExtractor:
    """EntityRuler/glossary-seeded extractor for research-domain types.

    Types generic NER under-serves: METHOD, DATASET, METRIC, CONCEPT, WORK.
    METRIC uses a value+unit pattern, METHOD and DATASET use head-noun patterns,
    and CONCEPT picks up terms the document defines alongside an acronym. The
    per-project glossary (loaded data, never hardcoded) extends every type
    without a code change.
    """
    id = "domain_pattern"
    layer = 2
    entity_types = ["METHOD", "DATASET", "METRIC", "CONCEPT", "WORK"]

    def extract(self, text: str, context: ExtractContext) -> List[EntityMention]:
        if not text or not text.strip():
            return []
        mentions: List[EntityMention] = []

        for m in _METRIC_RE.finditer(text):
            mentions.append(self._mention(m.group(0).strip(), "METRIC", m.start(), 0.7))

        for etype, pattern in _HEAD_NOUN_RES.items():
            for m in pattern.finditer(text):
                surface = strip_leading_filler(clean_surface(m.group(1)))
                # A bare head noun ("The dataset") names nothing; keep only
                # phrases that still carry a qualifier in front of the head.
                if len(surface.split()) < 2 or is_noise_surface(surface):
                    continue
                offset = m.group(1).find(surface)
                mentions.append(self._mention(
                    surface, etype,
                    m.start(1) + offset if offset >= 0 else m.start(1), 0.65,
                ))

        # A defined term plus its acronym become one canonical CONCEPT: both
        # surface forms share the expansion's normalized key.
        for m in _ACRONYM_DEF_RE.finditer(text):
            acronym = m.group(2)
            phrase = _acronym_expansion(clean_surface(m.group(1)), acronym)
            if not phrase or is_noise_surface(phrase):
                continue
            key = normalize_key(phrase, "CONCEPT")
            # The expansion is the *tail* of the captured words: anchor it
            # there, not at the start of the capture ("We argue that ranked…").
            offset = phrase_offset(m.group(1), phrase)
            start = m.start(1) + offset if offset >= 0 else m.start(1)
            mentions.append(self._mention(phrase, "CONCEPT", start, 0.8, key))
            mentions.append(self._mention(acronym, "CONCEPT", m.start(2), 0.8, key))

        glossary = context.project_glossary or {}
        for etype, terms in glossary.items():
            etype_up = str(etype).upper()
            if etype_up not in self.entity_types:
                continue
            for term in terms or []:
                if not term:
                    continue
                pattern = re.compile(r"\b" + re.escape(str(term)) + r"\b")
                for hits, m in enumerate(pattern.finditer(text)):
                    if hits >= _MAX_GLOSSARY_TERM_HITS:
                        break
                    mentions.append(
                        self._mention(m.group(0), etype_up, m.start(), 0.75)
                    )
        return mentions

    def _mention(
        self, surface: str, entity_type: str, start: int,
        confidence: float, normalized_key: Optional[str] = None,
    ) -> EntityMention:
        return EntityMention(
            surface_text=surface,
            entity_type=entity_type,
            char_start=start,
            char_end=start + len(surface),
            normalized_key=normalized_key or normalize_key(surface, entity_type),
            extractor_id=self.id,
            confidence=confidence,
        )


# --------------------------------------------------------------------------
# Shared normalization (the resolution key)
# --------------------------------------------------------------------------

def normalize_key(surface: str, entity_type: str = "") -> str:
    """Normalized clustering key: lowercased, title/punct-stripped, collapsed.

    Two mentions with the same key (within a type) resolve to one canonical
    entity — the basis for both intra- and cross-document resolution.
    """
    text = (surface or "").strip()
    text = _TITLE_PREFIX_RE.sub("", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"^the\s+", "", text, flags=re.I)
    if entity_type in {"NORP", "GROUP"} and len(text) > 4 and text.endswith("s") and not text.endswith("ss"):
        text = text[:-1]
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text
