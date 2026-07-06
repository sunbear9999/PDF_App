"""
LanguageFeatureService — centralised spaCy access for all extractors.

Previously each service that needed NER (DocumentIngestionService,
DeterministicExtractorService) loaded spaCy independently.  This service
provides a single lazy-loaded NLP pipeline that all downstream extractors
share, preventing duplicate model loads.
"""
from __future__ import annotations

from typing import List, Optional, Tuple


class LanguageFeatureService:
    """
    Thin wrapper around spaCy providing sentence splitting and NER.

    All methods gracefully return empty results if spaCy or a model is
    unavailable, so the rest of the extraction pipeline degrades cleanly.
    """

    _NLP_MODELS = ("en_core_web_lg", "en_core_web_md", "en_core_web_sm")
    _DISABLED_PIPES = ("tagger", "parser", "lemmatizer", "attribute_ruler")

    def __init__(self, model_name: Optional[str] = None) -> None:
        self._model_name = model_name
        self._nlp = None
        self._load_attempted = False

    # ------------------------------------------------------------------
    # Lazy NLP loader
    # ------------------------------------------------------------------

    def _ensure_nlp(self):
        if self._load_attempted:
            return
        self._load_attempted = True
        try:
            import spacy  # type: ignore
            candidates = [self._model_name] if self._model_name else list(self._NLP_MODELS)
            for name in candidates:
                try:
                    disable = [p for p in self._DISABLED_PIPES
                               if p in spacy.load(name).pipe_names]
                    self._nlp = spacy.load(name, disable=disable)
                    return
                except OSError:
                    continue
        except ImportError:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        self._ensure_nlp()
        return self._nlp is not None

    def get_entities(self, text: str) -> List[Tuple[str, str, int, int]]:
        """Return [(text, label, start_char, end_char)] for NER entities."""
        self._ensure_nlp()
        if self._nlp is None or not text:
            return []
        try:
            doc = self._nlp(text[:100_000])   # cap to avoid memory issues
            return [(ent.text, ent.label_, ent.start_char, ent.end_char)
                    for ent in doc.ents]
        except Exception:
            return []

    def split_sentences(self, text: str) -> List[str]:
        """Return a list of sentence strings."""
        self._ensure_nlp()
        if self._nlp is None or not text:
            return _naive_sentence_split(text)
        try:
            nlp = self._nlp
            # Use sentencizer if parser is disabled
            if "senter" in nlp.pipe_names or "sentencizer" in nlp.pipe_names:
                doc = nlp(text[:50_000])
                return [sent.text.strip() for sent in doc.sents if sent.text.strip()]
        except Exception:
            pass
        return _naive_sentence_split(text)

    def filter_person_org_entities(
        self, entities: List[Tuple[str, str, int, int]]
    ) -> List[Tuple[str, str, int, int]]:
        """Keep only PERSON and ORG labels."""
        return [(t, l, s, e) for t, l, s, e in entities if l in ("PERSON", "ORG")]


def _naive_sentence_split(text: str) -> List[str]:
    """Fallback sentence splitter using simple punctuation rules."""
    import re
    raw = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in raw if s.strip()]
