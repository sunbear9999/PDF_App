"""Which language a sentence is written in, and which stretches are foreign.

Two general signals, no per-document rules:

* function words of registered languages vote on the sentence language
  (packs ``language_stopwords_<iso>``; plugins add languages);
* runs of lowercase words the NER model's vocabulary does not know mark
  foreign or unreadable stretches even for languages without a pack (a Maya
  quotation inside English prose, OCR debris from a rotated map).

Both abstain ("und", no runs) when the evidence is thin. When the optional
language model (GlotLID, AI Setup) is switched on, its confident answer is used
for sentences long enough to identify; codes are reported as ISO 639-1 where
one exists so they compare with the NER model's language.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.models.extraction_patterns import LANGUAGE_STOPWORDS

UNDETERMINED = "und"
PACK_PREFIX = "language_stopwords_"
MIN_WORDS = 6
MIN_SHARE = 0.12        # stopword hits / words for a confident vote
MIN_LEAD = 1.5          # best language's hits must exceed the runner-up by this factor
RUN_WINDOW = 4          # words per window when looking for foreign stretches
RUN_MIN_UNKNOWN = 3     # unknown lowercase words a window needs to be foreign
MODEL_MIN_PROBABILITY = 0.7
# ISO 639-3 -> 639-1 for the languages NER models and stopword packs name by two letters.
ISO_639_1 = {"eng": "en", "spa": "es", "fra": "fr", "deu": "de", "por": "pt", "ita": "it", "lat": "la",
             "nld": "nl", "rus": "ru", "zho": "zh", "cmn": "zh", "jpn": "ja", "kor": "ko", "arb": "ar",
             "pol": "pl", "swe": "sv", "dan": "da", "nob": "nb", "fin": "fi", "ell": "el", "tur": "tr"}
_WORD = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)*")

KnownWord = Callable[[str], Optional[bool]]


class LanguageIdentifier:
    def __init__(self, pattern_registry: Any = None) -> None:
        self._languages = self._load(pattern_registry)
        self.vote = lru_cache(maxsize=65536)(self._vote)
        self.model = None       # optional GlotLidRuntime, set per run when switched on

    def model_identity(self) -> str:
        return "glotlid" if self.model is not None else ""

    @staticmethod
    def _load(registry: Any) -> Dict[str, List[re.Pattern]]:
        languages: Dict[str, List[re.Pattern]] = {}
        for pack in (registry.all_packs() if registry else []):
            if pack.pack_id.startswith(PACK_PREFIX):
                languages[pack.pack_id[len(PACK_PREFIX):]] = list(pack.compiled)
        for iso, words in LANGUAGE_STOPWORDS.items():
            languages.setdefault(iso, [re.compile(r"(?i)^(?:" + "|".join(words.split()) + r")$")])
        return languages

    def _vote(self, word: str) -> Tuple[str, ...]:
        return tuple(iso for iso, patterns in self._languages.items() if any(p.match(word) for p in patterns))

    def detect(self, text: str) -> Tuple[str, float]:
        """(ISO code, share of words that voted for it), or ("und", 0.0)."""
        words = [w.casefold() for w in _WORD.findall(text or "")]
        if len(words) < MIN_WORDS:
            return UNDETERMINED, 0.0
        if self.model is not None:
            found = self.model.detect(text)
            if found and found[1] >= MODEL_MIN_PROBABILITY:
                return ISO_639_1.get(found[0], found[0]), round(found[1], 3)
        hits: Dict[str, int] = {}
        for word in words:
            for iso in self.vote(word):
                hits[iso] = hits.get(iso, 0) + 1
        ranked = sorted(hits.items(), key=lambda item: -item[1])
        if not ranked:
            return UNDETERMINED, 0.0
        best, count = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0
        if count < 2 or count / len(words) < MIN_SHARE or count < MIN_LEAD * runner_up:
            return UNDETERMINED, 0.0
        return best, round(count / len(words), 3)


def foreign_spans(text: str, known_word: Optional[KnownWord]) -> List[Tuple[int, int]]:
    """Character ranges where most lowercase words are unknown to the NER model."""
    if known_word is None:
        return []
    words = list(_WORD.finditer(text or ""))
    unknown = [w[0][:1].islower() and known_word(w[0]) is False for w in words]
    spans: List[Tuple[int, int]] = []
    for start in range(0, max(0, len(words) - RUN_WINDOW + 1)):
        window = range(start, start + RUN_WINDOW)
        if sum(unknown[i] for i in window) >= RUN_MIN_UNKNOWN:
            # The stretch runs from its first to its last unknown word.
            flagged = [i for i in window if unknown[i]]
            left, right = words[flagged[0]].start(), words[flagged[-1]].end()
            if spans and left <= spans[-1][1] + 1:
                spans[-1] = (spans[-1][0], right)
            else:
                spans.append((left, right))
    return spans
