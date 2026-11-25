"""Type votes from the multilingual GLiNER recogniser (AI Setup, optional).

GLiNER reads each name in context against a list of plain-language labels
(``entity_labels.json`` in the model folder, editable; each label maps to a
Papyrus type). It is an *arbiter*: it scores sentences that contain names the
other layers found and votes on those spans; it does not add new names.

Cost stays bounded for any length (about 0.13 s per sentence on a laptop
CPU): each name is scored in at most ``SAMPLES_PER_NAME`` of its sentences
(first and middle occurrence) and a document in at most ``MAX_SENTENCES``
sentences, packed several per model call. Names that most need a second
opinion come first: those the sentence-level labels disagree on or leave
untyped, then the most frequent. Because type support is a rate over the mentions a source assessed,
sampling does not dilute the vote of a frequent name.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Dict, List, Optional

from core.models.intelligence_models import EntityMention
from core.services.intelligence.entities.evidence_sources import add_vote, mark_assessed

SOURCE_ID = "gliner"
WEIGHT = 2.0                   # x model probability
SAMPLES_PER_NAME = 2
MAX_SENTENCES = 400
MIN_OVERLAP = 0.6              # span / mention overlap (intersection over union)
VOTED_TYPES = frozenset({"PERSON", "ORG", "LOCATION", "NORP", "UNKNOWN", "PRODUCT", "WORK", "EVENT", "LAW",
                         "LANGUAGE", "FAC", "METHOD", "DATASET", "CONCEPT"})
LABELS_FILE = "entity_labels.json"
DEFAULT_LABELS = [
    {"label": "person", "type": "PERSON"},
    {"label": "organization", "type": "ORG"},
    {"label": "location", "type": "LOCATION"},
    {"label": "ethnic or national group", "type": "NORP"},
    # Nationality words ("Chinese", "Asian") also read as languages: demand more.
    {"label": "language", "type": "LANGUAGE", "threshold": 0.75},
    {"label": "law, treaty or court case", "type": "LAW"},
    {"label": "historical event", "type": "EVENT"},
    {"label": "book, article or other written work", "type": "WORK"},
    {"label": "software or machine learning model", "type": "PRODUCT"},
    {"label": "dataset or benchmark", "type": "DATASET"},
    {"label": "method or algorithm", "type": "METHOD"},
    {"label": "disease or medical condition", "type": "CONCEPT"},
    {"label": "organism or taxon", "type": "CONCEPT"},
]


def default_labels_json() -> str:
    return json.dumps(DEFAULT_LABELS, indent=2, ensure_ascii=False)


class GlinerEvidenceSource:
    id = SOURCE_ID
    setting_key = "use_gliner"

    def __init__(self, runtime, digest=lambda: "") -> None:
        self.runtime = runtime
        self._digest = digest

    def available(self) -> bool:
        return self.runtime.is_available()

    def identity(self) -> str:
        return f"gliner#{(self._digest() or '')[:16]}+labels:{_labels_digest(self.labels())}"

    def labels(self) -> List[Dict]:
        try:
            with open(os.path.join(self.runtime.model_dir, LABELS_FILE), encoding="utf-8") as stream:
                labels = [row for row in json.load(stream) if row.get("label") and row.get("type")]
            return labels or DEFAULT_LABELS
        except (OSError, ValueError, AttributeError, TypeError):
            return DEFAULT_LABELS

    def add_votes(self, mentions: List[EntityMention], texts: Dict[str, str]) -> None:
        labels = self.labels()
        by_label = {row["label"]: row for row in labels}
        sentences = sample_sentences(mentions, texts)
        if not sentences:
            return
        thresholds = {row["label"]: float(row["threshold"]) for row in labels if row.get("threshold")}
        spans = self.runtime.predict_many([texts[key] for key in sentences], list(by_label), thresholds)
        found = dict(zip(sentences, spans))
        for mention in mentions:
            if mention.sentence_id not in found or mention.entity_type not in VOTED_TYPES:
                continue
            mark_assessed(mention, SOURCE_ID)
            span = _best_overlap(mention, found[mention.sentence_id])
            if span is not None:
                kind = by_label[span.label]["type"]
                add_vote(mention, SOURCE_ID, kind, round(WEIGHT * span.score, 3),
                         f"GLiNER read “{texts[mention.sentence_id][span.start:span.end]}” as {span.label} "
                         f"({span.score:.2f})")
                mention.metadata.setdefault("gliner_label", span.label)


def sample_sentences(mentions: List[EntityMention], texts: Dict[str, str]) -> List[str]:
    """Up to ``SAMPLES_PER_NAME`` sentences per name, most frequent names first,
    within the document budget."""
    occurrences: Dict[str, List[str]] = defaultdict(list)
    labels: Dict[str, set] = defaultdict(set)
    for mention in mentions:
        if mention.entity_type in VOTED_TYPES and mention.sentence_id in texts:
            key = mention.surface_text.casefold()
            labels[key].add(mention.entity_type)
            if not occurrences[key] or occurrences[key][-1] != mention.sentence_id:
                occurrences[key].append(mention.sentence_id)

    def priority(key):
        contested = len(labels[key]) > 1 or "UNKNOWN" in labels[key]
        return (not contested, -len(occurrences[key]), key)

    chosen: Dict[str, None] = {}
    for key in sorted(occurrences, key=priority):
        seen = occurrences[key]
        for sentence in dict.fromkeys([seen[0], seen[len(seen) // 2]][:SAMPLES_PER_NAME]):
            if len(chosen) >= MAX_SENTENCES:
                return list(chosen)
            chosen[sentence] = None
    return list(chosen)


def _best_overlap(mention: EntityMention, spans) -> Optional[object]:
    start, end = mention.char_start, mention.char_end
    if start is None or end is None:
        return None
    best, best_score = None, MIN_OVERLAP
    for span in spans:
        inter = min(end, span.end) - max(start, span.start)
        if inter <= 0:
            continue
        union = max(end, span.end) - min(start, span.start)
        if inter / union >= best_score:
            best, best_score = span, inter / union
    return best


def _labels_digest(labels: List[Dict]) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(labels, sort_keys=True).encode()).hexdigest()[:12]
