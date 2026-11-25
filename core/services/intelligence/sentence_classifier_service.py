"""Shared argument eligibility and role classification without generation.

Every backend rejects structural noise. The built-in policy retains background
sentences without publishing argument units. A validated task-trained ONNX model
can replace role scoring; optional zero-shot NLI can propose supported roles for
unknown prose, with explicit abstention and separate model provenance.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from core.models.producer_models import MethodKind, ProducerIdentity
from core.services.intelligence.argument_role_policy import (
    ROLES, VERSION, heuristic_result, model_role_allowed,
)
from core.services.intelligence.zero_shot_role_enhancer import BACKEND as ENHANCER_BACKEND
from core.services.intelligence.sentence_role_model import read_role_labels, role_model_identity
from core.services.intelligence.onnx_text_runtime import LazyOnnxSession

from core.models.artifact_fields import ROLE_LABELS

LABELS = list(ROLE_LABELS)

# Optional ONNX model location (a directory holding model.onnx + tokenizer.json
# + labels.json). Absent by default; the deterministic backend is used instead.
DEFAULT_MODEL_DIR = os.environ.get(
    "PAPYRUS_SENTENCE_CLASSIFIER_DIR",
    os.path.join(
        os.path.expanduser("~"), ".local", "share", "Papyrus", "models",
        "sentence_role_classifier",
    ),
)

# Reusable policy owns eligibility and all role cues.
HEURISTIC_VERSION = VERSION
_DEFINITION_RE = re.compile(r"\b(is defined as|refers to|means that)\b", re.I)
_QUOTE_RE = re.compile(r'["“”].+?["“”]')
_CITATION_RE = re.compile(r"\[\d+\]|\([^()]*\b(?:19|20)\d{2}[a-z]?[^()]*\)")

_BACKEND_KIND = {
    "heuristic": MethodKind.HEURISTIC.value,
    "onnx": MethodKind.LEARNED.value,
    ENHANCER_BACKEND: MethodKind.LEARNED.value,
}


class SentenceClassifierService:
    def __init__(self, model_dir: Optional[str] = None, enhancer=None) -> None:
        self._model_dir = model_dir or DEFAULT_MODEL_DIR
        self.runtime = LazyOnnxSession(self._model_dir)
        self._onnx_labels = LABELS
        self._load_attempted = False   # labels/identity read (the session reloads on its own)
        self._model_ready = False
        self._load_error = ""
        self._model_identity = {}
        self._enhancer = enhancer

    # -- Public API --------------------------------------------------------

    @property
    def backend(self) -> str:
        self._ensure_model()
        return "onnx" if self._onnx is not None else "heuristic"

    def is_model_available(self) -> bool:
        self._ensure_model()
        return self._onnx is not None

    def status(self, use_enhancer: bool = True) -> Dict[str, Any]:
        """What will label sentence roles right now, in plain terms.

        The built-in heuristic always works; "no classifier installed" must
        only ever mean no *optional learned* model is installed. Reads files
        only — it never loads a model, so it is safe on the Qt thread.
        """
        custom = all(
            os.path.isfile(os.path.join(self._model_dir, name))
            for name in ("model.onnx", "tokenizer.json", "labels.json")
        ) and not self._load_error
        if custom:
            try:
                read_role_labels(self._model_dir)
            except (OSError, ValueError, KeyError, TypeError):
                custom = False
        enhancer = bool(self._enhancer and self._enhancer.is_installed())
        if custom:
            active = "Custom task-trained ONNX model"
        elif enhancer and use_enhancer:
            active = "Built-in heuristic + NLI proposals for uncertain prose (may abstain)"
        else:
            active = "Built-in heuristic (no model)"
        return {
            "active": active,
            "custom_model_installed": custom,
            "enhancer_installed": enhancer,
            "enhancer_enabled": enhancer and use_enhancer,
            "custom_model_error": self._load_error,
        }

    def identity_for(self, result: Dict[str, Any]) -> ProducerIdentity:
        """Truthful identity of the method that produced one result."""
        backend = str(result.get("backend") or "heuristic")
        if backend == ENHANCER_BACKEND and self._enhancer:
            return self._enhancer.identity()
        if backend == "onnx":
            return ProducerIdentity(
                producer_id="sentence_role_classifier", producer_version=HEURISTIC_VERSION,
                method_kind=MethodKind.LEARNED.value, backend="onnx",
                **(self._model_identity or role_model_identity(self._model_dir)),
            )
        return ProducerIdentity(
            producer_id="sentence_role_heuristic", producer_version=HEURISTIC_VERSION,
            method_kind=_BACKEND_KIND["heuristic"], backend="heuristic",
            fallback_reason=str(result.get("fallback_reason") or ""),
        )

    def classify(
        self, sentences: List[str], contexts: Optional[List[Dict[str, Any]]] = None,
        *, use_enhancer: bool = True,
    ) -> List[Dict[str, Any]]:
        if not sentences:
            return []
        self._ensure_model()
        contexts = list(contexts or [])
        results = [self._classify_heuristic(text, contexts[i] if i < len(contexts) else None)
                   for i, text in enumerate(sentences)]
        fallback = self._load_error
        if self._onnx is not None:
            try:
                learned = self._classify_onnx(sentences)
                for i, result in enumerate(learned):
                    if results[i].get("argument_status") == "excluded":
                        continue
                    valid = result["label"] in ROLES
                    results[i] = {**results[i], **result,
                                  "argument_eligible": valid,
                                  "argument_status": "classified" if valid else "background",
                                  "eligibility_reason": "custom_classifier"}
            except Exception as exc:
                fallback = f"custom ONNX model failed: {exc}"[:200]
        if use_enhancer and self._onnx is None:
            self._enhance_unknowns(sentences, contexts, results)
        if fallback:
            for result in results:
                result["fallback_reason"] = fallback
        return results

    def _enhance_unknowns(self, sentences, contexts, results) -> None:
        targets = [
            index for index, result in enumerate(results)
            if result.get("label") == "unknown"
            and result.get("argument_status") != "excluded"
        ]
        if not targets or not self._enhancer or not self._enhancer.is_available():
            return
        labels = self._enhancer.label([sentences[index] for index in targets])
        for index, proposed in zip(targets, labels):
            if proposed:
                proposed = dict(proposed)
                role = proposed.get("label", "unknown")
                context = contexts[index] if index < len(contexts) else None
                if role in ROLES and not model_role_allowed(role, sentences[index], context):
                    proposed.update(label="unknown", confidence=0.25,
                                    suggested_role=role, abstention_reason="unsupported_role_cues")
                results[index] = {
                    **results[index], **proposed,
                    "argument_eligible": proposed["label"] in ROLES,
                    "argument_status": "classified" if proposed["label"] in ROLES else "background",
                    "eligibility_reason": "nli_role_with_abstention",
                    "aux": results[index].get("aux", []),
                    "heuristic_label": "unknown",
                }

    def reload(self) -> None:
        self._load_error = ""
        self.runtime.reset()
        self._model_identity = {}
        self._load_attempted = False
        self._model_ready = False

    # -- ONNX backend ------------------------------------------------------

    @property
    def _onnx(self):
        return self.runtime.session

    @property
    def _tokenizer(self):
        return self.runtime.tokenizer

    def _ensure_model(self) -> None:
        if not self._load_attempted:
            self._load_attempted = True
            if not self.runtime.is_installed():
                return
            try:
                self._onnx_labels = read_role_labels(self._model_dir)
                self._model_identity = role_model_identity(self._model_dir)
                self._model_ready = True
                self._load_error = ""
            except Exception as exc:
                self._load_error = str(exc)[:200]
                return
        if self._model_ready and not self.runtime.ensure():
            self._load_error = self.runtime.last_error[:200]

    def _classify_onnx(self, sentences: List[str]) -> List[Dict[str, Any]]:
        import numpy as np
        results = []
        input_names = {item.name for item in self._onnx.get_inputs()}
        for start in range(0, len(sentences), 64):
            texts = sentences[start:start + 64]
            encodings = self._tokenizer.encode_batch(texts)
            feeds = {
                "input_ids": np.asarray([item.ids for item in encodings], dtype=np.int64),
                "attention_mask": np.asarray(
                    [item.attention_mask for item in encodings], dtype=np.int64
                ),
            }
            if "token_type_ids" in input_names:
                feeds["token_type_ids"] = np.asarray(
                    [item.type_ids for item in encodings], dtype=np.int64
                )
            logits_batch = self._onnx.run(None, feeds)[0]
            if len(logits_batch) != len(texts):
                raise ValueError("Classifier returned a different number of sentences")
            for text, logits in zip(texts, logits_batch):
                results.append(self._onnx_prediction(text, logits))
        return results

    def _onnx_prediction(self, text, logits):
        import numpy as np
        if len(logits) != len(self._onnx_labels) or not np.isfinite(logits).all():
            raise ValueError("Classifier scores do not match its declared role labels")
        probs = _softmax(logits)
        idx = int(np.argmax(probs))
        ordered = sorted(probs, reverse=True)
        margin = float(ordered[0] - ordered[1]) if len(ordered) > 1 else 0.0
        proposed = self._onnx_labels[idx]
        uncertain = float(probs[idx]) < 0.55 or margin < 0.10
        return {
            "label": "unknown" if uncertain else proposed,
            "confidence": float(probs[idx]),
            "scores": dict(zip(self._onnx_labels, map(float, probs))),
            "aux": self._aux_tags(text), "backend": "onnx",
            "score_kind": "model_probability", "role_margin": margin,
            "suggested_role": proposed if uncertain else None,
            "abstention_reason": "low_probability_or_margin" if uncertain else "",
        }

    # -- Deterministic backend --------------------------------------------

    def _classify_heuristic(self, text: str, context=None) -> Dict[str, Any]:
        return {**heuristic_result(text, context), "aux": self._aux_tags(text)}

    def _aux_tags(self, text: str) -> List[str]:
        tags = []
        if _DEFINITION_RE.search(text):
            tags.append("definition")
        if _CITATION_RE.search(text):
            tags.append("citation_dense")
        if _QUOTE_RE.search(text):
            tags.append("quote")
        return tags


def _softmax(logits):
    import numpy as np
    x = np.asarray(logits, dtype=np.float64)
    x = x - np.max(x)
    e = np.exp(x)
    return e / (np.sum(e) or 1.0)
