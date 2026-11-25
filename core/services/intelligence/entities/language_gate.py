"""Do not trust an English NER model's labels in text that is not English.

A sentence identified as another language, or a stretch of words the model's
vocabulary does not know (a quotation in Maya, OCR debris), cannot be typed by
that model. Its labels there are discarded; a name-shaped span the document
repeats is kept as an untyped candidate for later typing evidence. In a document
written in the model's language, a foreign stretch (an author address, a
quotation) keeps names of two or more capitalised words ("Instituto de
Psiquiatria") with their label, marked ``language_untrusted``, unless they start
with a function word; in a document written in another language the model's
labels are not trusted anywhere ("Puaa Momona" in a Hawaiian newspaper). Names the
document establishes elsewhere still reach those sentences through
propagation, and multilingual extractors are not gated.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from core.models.intelligence_models import EntityMention
from core.services.extraction.language_id import UNDETERMINED, KnownWord, LanguageIdentifier, foreign_spans
from core.services.intelligence.entities.recognize import sentence_id
from core.services.intelligence.entity_extractor import normalize_key

# Extractors whose labels come from a single-language statistical model.
LANGUAGE_BOUND_EXTRACTORS = frozenset({"general_ner"})
# Sentences too short or damaged to identify inherit the document's language
# when its identified sentences agree on one.
DOCUMENT_MIN_SENTENCES = 5
DOCUMENT_MIN_SHARE = 0.6
# Name-shaped spans in text the model cannot type become untyped candidates.
UNRESOLVED_TYPE = "UNKNOWN"
MIN_NAME_LETTERS = 3
MIN_CANDIDATE_REPEATS = 2
CANDIDATE_CONFIDENCE = 0.5
MIN_NAME_WORDS = 2


def gate(mentions: List[EntityMention], records: List[Dict[str, Any]], identifier: LanguageIdentifier,
         model_language: str, known_word: Optional[KnownWord]) -> List[EntityMention]:
    languages: Dict[str, str] = {}
    foreign: Dict[str, list] = {}
    for row in records:
        text = row.get("exact_text") or ""
        key = sentence_id(row)
        languages[key] = identifier.detect(text)[0]
        foreign[key] = foreign_spans(text, known_word)
    prior = document_language(languages.values())
    if prior != UNDETERMINED:
        languages = {key: prior if value == UNDETERMINED else value for key, value in languages.items()}
    kept, candidates = [], []
    for mention in mentions:
        language = languages.get(mention.sentence_id, UNDETERMINED)
        mention.metadata.setdefault("language", language)
        if mention.extractor_id in LANGUAGE_BOUND_EXTRACTORS and _untrusted(
                mention, language, model_language, foreign.get(mention.sentence_id, ())):
            if prior == model_language and _multiword_name(mention, identifier):
                mention.metadata["language_untrusted"] = True
                kept.append(mention)
            elif _name_shaped(mention, identifier):
                candidates.append(mention)
            continue
        kept.append(mention)
    texts = {sentence_id(row): row.get("exact_text") or "" for row in records}
    return kept + _repeated_candidates(candidates, texts)


def _name_shaped(mention: EntityMention, identifier: LanguageIdentifier) -> bool:
    """Title-case words that are not function words of any registered language."""
    words = mention.surface_text.split()
    return bool(words) and all(
        len(word) >= MIN_NAME_LETTERS and word[:1].isupper() and not word.isupper()
        and not identifier.vote(word.casefold()) for word in words)


def _multiword_name(mention: EntityMention, identifier: LanguageIdentifier) -> bool:
    """At least two capitalised name words; lowercase words only as function-word
    particles inside ("de", "van"); never starting with a function word ("Ka Poe")."""
    words = mention.surface_text.split()
    names = [w for w in words if w[:1].isupper() and not w.isupper() and len(w) >= MIN_NAME_LETTERS]
    if len(names) < MIN_NAME_WORDS or identifier.vote(words[0].casefold()):
        return False
    return all(w in names or (w[:1].islower() and identifier.vote(w.casefold())) for w in words)


def _repeated_candidates(candidates: List[EntityMention], texts: Dict[str, str]) -> List[EntityMention]:
    """A name the model cannot type is kept, untyped, only when the document repeats
    it and capitalises it somewhere other than the start of a sentence."""
    counts = Counter(mention.surface_text for mention in candidates)
    inside = {m.surface_text for m in candidates if not _sentence_initial(m, texts.get(m.sentence_id, ""))}
    kept = []
    for mention in candidates:
        if counts[mention.surface_text] >= MIN_CANDIDATE_REPEATS and mention.surface_text in inside:
            mention.metadata.update(ner_label=mention.entity_type, type_unresolved=True)
            mention.entity_type, mention.confidence = UNRESOLVED_TYPE, CANDIDATE_CONFIDENCE
            mention.normalized_key = normalize_key(mention.surface_text, UNRESOLVED_TYPE)
            kept.append(mention)
    return kept


def _untrusted(mention: EntityMention, language: str, model_language: str, spans) -> bool:
    if model_language and language not in {UNDETERMINED, model_language}:
        return True
    start, end = mention.char_start, mention.char_end
    if start is None or end is None:
        return False
    return any(left <= end and start <= right for left, right in spans)


def document_language(sentence_languages) -> str:
    """The language most identified sentences share, or "und"."""
    votes = Counter(language for language in sentence_languages if language != UNDETERMINED)
    total = sum(votes.values())
    if total < DOCUMENT_MIN_SENTENCES:
        return UNDETERMINED
    language, count = votes.most_common(1)[0]
    return language if count / total >= DOCUMENT_MIN_SHARE else UNDETERMINED


def _sentence_initial(mention: EntityMention, text: str) -> bool:
    before = text[:mention.char_start or 0].rstrip()
    return not before or before[-1] in ".!?:;\"“‘'([—–-"
