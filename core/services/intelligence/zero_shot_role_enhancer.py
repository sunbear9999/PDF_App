"""Optional NLI proposals for eligible prose with no strong role cues.

Seven editable hypotheses include an explicit background role. Absolute
entailment and separation thresholds can abstain; low-confidence proposals
carry actual model identity. NLI is not a task-trained argument-role model.
Use the reproducible benchmarks rather than assuming it improves every genre.
"""
from __future__ import annotations

import json
from core.services.intelligence.sentence_role_model import encoder_model_identity
import os
import hashlib
from typing import Any, Dict, List, Optional

from core.models.producer_models import MethodKind, ProducerIdentity
from core.services.intelligence.argument_role_policy import ROLES

BACKEND = "zero_shot_nli"
MAX_CONFIDENCE = 0.55
MIN_ENTAILMENT = 0.5
MIN_ROLE_MARGIN = 0.1
DEFAULT_HYPOTHESES = {
    "claim": "The author argues that a proposition is true or a policy is right.",
    "evidence": "The author presents observations, measurements or quotations as evidence for a position.",
    "reasoning": "The author explains why a conclusion follows from its premises.",
    "methodology": "The author describes the methods used to conduct a study or experiment.",
    "counterclaim": "The author presents an opposing argument or rejects another position.",
    "limitation": "The author describes a weakness or uncertainty in their research or analysis.",
    "unknown": "The sentence is background information or a reference, without an argument.",
}
HYPOTHESES_FILE = "role_hypotheses.json"


class ZeroShotRoleEnhancer:
    def __init__(self, scorer: Any, model_dir: str) -> None:
        self._scorer = scorer
        self._model_dir = model_dir

    def is_installed(self) -> bool:
        return bool(self._scorer and self._scorer.is_installed())

    def is_available(self) -> bool:
        return bool(self._scorer and self._scorer.is_available())

    def hypotheses(self) -> Dict[str, str]:
        """Per-role hypotheses; a user-edited ``role_hypotheses.json`` wins."""
        path = os.path.join(self._model_dir, HYPOTHESES_FILE)
        try:
            with open(path, encoding="utf-8") as stream:
                loaded = json.load(stream)
            if isinstance(loaded, dict) and loaded:
                allowed = set(ROLES) | {"unknown"}
                return {**DEFAULT_HYPOTHESES, **{
                    str(key): str(value) for key, value in loaded.items()
                    if key in allowed and isinstance(value, str) and value.strip()
                }}
        except (OSError, ValueError):
            pass
        return dict(DEFAULT_HYPOTHESES)

    def identity(self) -> ProducerIdentity:
        identity = encoder_model_identity(self._model_dir)
        marker_reader = getattr(self._scorer, "model_marker", None)
        marker = marker_reader() if marker_reader else {}
        if marker.get("repository"):
            identity["model_id"] = str(marker["repository"])
        if marker.get("revision"):
            identity["model_version"] = str(marker["revision"])
        return ProducerIdentity(
            producer_id="sentence_role_enhancer",
            producer_version="2.0",
            method_kind=MethodKind.LEARNED.value,
            backend=BACKEND,
            **identity,
            configuration_digest=hashlib.sha256(json.dumps(
                self.hypotheses(), sort_keys=True, ensure_ascii=False,
            ).encode()).hexdigest(),
        )

    def label(self, texts: List[str]) -> List[Optional[Dict[str, Any]]]:
        """Best role per text, or None when the model cannot run."""
        if not texts or not self.is_available():
            return [None for _ in texts]
        roles = self.hypotheses()
        names = list(roles)
        pairs = [(text, roles[name]) for text in texts for name in names]
        try:
            scores = self._scorer.score_pairs(pairs)
        except Exception:
            return [None for _ in texts]
        if len(scores) != len(pairs):
            return [None for _ in texts]
        width = len(names)
        return [
            _choice(names, scores[index * width:(index + 1) * width])
            for index in range(len(texts))
        ]


def _choice(names: List[str], row: List[dict]) -> Dict[str, Any]:
    # Relative softmax over entailment logits always produced a winner, even
    # when every hypothesis was neutral. Require absolute support and margin.
    scores = {name: float(value.get("entailment", 0)) for name, value in zip(names, row)}
    ranked = sorted(scores, key=scores.get, reverse=True)
    best = ranked[0]
    margin = scores[best] - scores[ranked[1]] if len(ranked) > 1 else scores[best]
    accepted = best in ROLES and scores[best] >= MIN_ENTAILMENT and margin >= MIN_ROLE_MARGIN
    return {
        "label": best if accepted else "unknown",
        "confidence": round(min(MAX_CONFIDENCE, scores[best]), 3) if accepted else 0.25,
        "scores": {name: round(value, 4) for name, value in scores.items()},
        "score_kind": "nli_entailment", "role_margin": round(margin, 4),
        "abstention_reason": "" if accepted else "no_supported_distinct_role",
        "suggested_role": best if best in ROLES else None,
        "backend": BACKEND,
    }
