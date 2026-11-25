"""
LanguageFeatureService — centralised spaCy access for all extractors.

Previously each service that needed NER (DocumentIngestionService,
DeterministicExtractorService) loaded spaCy independently.  This service
provides a single lazy-loaded NLP pipeline that all downstream extractors
share, preventing duplicate model loads.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import time

from typing import Dict, Iterable, List, Optional, Tuple
from core.services.extraction.sentence_boundaries import split_sentences

# Resident size of a loaded pipeline (vectors dominate), for the memory manager.
_MODEL_BYTES = {"lg": 800 * 1024 ** 2, "md": 220 * 1024 ** 2, "sm": 60 * 1024 ** 2}
_BUSY_WINDOW_S = 10.0
_shared: Optional["LanguageFeatureService"] = None


def shared_language_service() -> "LanguageFeatureService":
    """The one process-wide pipeline (PapyrusCore installs its instance), so
    module-level extractors never load a second copy of spaCy."""
    global _shared
    if _shared is None:
        _shared = LanguageFeatureService()
    return _shared


def set_shared_language_service(service: "LanguageFeatureService") -> None:
    global _shared
    _shared = service


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
        self._last_used = 0.0

    # ------------------------------------------------------------------
    # Lazy NLP loader
    # ------------------------------------------------------------------

    def _ensure_nlp(self):
        self._last_used = time.monotonic()
        if self._load_attempted:
            return
        self._load_attempted = True
        try:
            import spacy  # type: ignore
            candidates = [self._model_name] if self._model_name else list(self._NLP_MODELS)
            for name in candidates:
                try:
                    # Load once. The prior implementation loaded every
                    # candidate once to inspect it and a second time to use it.
                    self._nlp = spacy.load(
                        name, disable=list(self._DISABLED_PIPES)
                    )
                    if (
                        "senter" not in self._nlp.pipe_names
                        and "sentencizer" not in self._nlp.pipe_names
                    ):
                        self._nlp.add_pipe("sentencizer")
                    return
                except (OSError, ValueError):
                    continue
        except ImportError:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        self._ensure_nlp()
        return self._nlp is not None

    def get_nlp(self):
        """The loaded pipeline (or None) for callers that need spaCy directly."""
        self._ensure_nlp()
        return self._nlp

    # -- memory manager hooks ------------------------------------------------

    def memory_bytes(self) -> int:
        if self._nlp is None:
            return 0
        name = str((getattr(self._nlp, "meta", {}) or {}).get("name") or "")
        return next((size for suffix, size in _MODEL_BYTES.items() if name.endswith(suffix)),
                    _MODEL_BYTES["md"])

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used if self._last_used else 0.0

    def busy(self) -> bool:
        return self._nlp is not None and self.idle_seconds() < _BUSY_WINDOW_S

    def release(self, _level=None) -> int:
        """Drop the pipeline; the next call loads it again."""
        freed = self.memory_bytes()
        self._nlp = None
        self._load_attempted = False
        return freed

    def is_known_word(self, word: str) -> Optional[bool]:
        """Whether the model's vocabulary (its vector table, ~680k entries for
        md/lg) knows the word; None when no model with vectors is loaded."""
        self._ensure_nlp()
        if self._nlp is None or not len(self._nlp.vocab.vectors):
            return None
        return bool(self._nlp.vocab.has_vector(word.casefold()) or self._nlp.vocab.has_vector(word))

    def model_language(self) -> str:
        """ISO code of the loaded NER model's language ("en"), or "" when none loads."""
        self._ensure_nlp()
        return str((getattr(self._nlp, "meta", {}) or {}).get("lang") or "") if self._nlp is not None else ""

    def model_info(self) -> Dict[str, str]:
        """Name/version of the spaCy pipeline in use, or that will load.

        Uses package metadata when the pipeline is not loaded yet, so asking
        for identity never pays the cost of loading spaCy.
        """
        if self._nlp is not None:
            meta = getattr(self._nlp, "meta", {}) or {}
            name = f"{meta.get('lang', '')}_{meta.get('name', '')}".strip("_")
            return {"name": name, "version": str(meta.get("version") or "")}
        if self._load_attempted:
            return {}
        candidates = [self._model_name] if self._model_name else list(self._NLP_MODELS)
        for name in candidates:
            try:
                if importlib.util.find_spec(name) is not None:
                    return {"name": name, "version": importlib.metadata.version(name)}
            except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
                continue
        return {}

    def get_entities(self, text: str) -> List[Tuple[str, str, int, int]]:
        """Return [(text, label, start_char, end_char)] for NER entities."""
        return self.get_entities_batch([text])[0]

    def get_entities_batch(
        self, texts: Iterable[str], *, batch_size: int = 32
    ) -> List[List[Tuple[str, str, int, int]]]:
        """Preserve input order and offsets; propagate actual inference failures.

        An unavailable optional model produces empty rows. A loaded model that
        fails must not masquerade as a successful extraction with no entities.
        """
        values = [str(text or "") for text in texts]
        self._ensure_nlp()
        if self._nlp is None:
            return [[] for _ in values]
        from core.services.extraction.ner_windows import entities_batch
        return entities_batch(self._nlp, values, max(1, min(int(batch_size), 128)))

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
    """Use the same abbreviation-aware fallback as source segmentation."""
    return split_sentences(text)
