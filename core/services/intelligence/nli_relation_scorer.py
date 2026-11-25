"""Optional batched ONNX entailment/contradiction scorer."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from core.services.intelligence.onnx_text_runtime import LazyOnnxSession


class NliRelationScorer:
    def __init__(self, model_dir: str) -> None:
        self._model_dir = model_dir
        self.runtime = LazyOnnxSession(model_dir)
        self._labels: Dict[int, str] = {
            0: "contradiction", 1: "neutral", 2: "entailment"
        }
        self._labels_read = False

    @property
    def _session(self):
        return self.runtime.session

    @property
    def _tokenizer(self):
        return self.runtime.tokenizer

    @property
    def _last_error(self) -> str:
        return self.runtime.last_error

    def reload(self) -> None:
        self.runtime.reset()
        self._labels_read = False

    def is_installed(self) -> bool:
        """Model files present (cheap; does not load an inference session)."""
        return self.runtime.is_installed()

    def is_available(self) -> bool:
        self._ensure()
        return self._session is not None

    def score_pairs(
        self, pairs: List[Tuple[str, str]], *, batch_size: int = 16
    ) -> List[Dict[str, float | str]]:
        results: List[Dict[str, float | str]] = []
        for logits in self._logit_batches(pairs, batch_size):
            for row in logits:
                probabilities = _softmax(row)
                scores = {
                    self._labels.get(index, str(index)).lower(): float(value)
                    for index, value in enumerate(probabilities)
                }
                label = max(scores, key=scores.get)
                results.append({
                    **scores,
                    "label": label,
                    "confidence": scores[label],
                })
        return results

    def entailment_logits(
        self, pairs: List[Tuple[str, str]], *, batch_size: int = 16
    ) -> List[float]:
        """Raw entailment logit per (premise, hypothesis) pair, for zero-shot
        ranking of several hypotheses against one premise."""
        index = next(
            (key for key, label in self._labels.items() if label == "entailment"), None
        )
        if index is None:
            return []
        return [
            float(row[index])
            for logits in self._logit_batches(pairs, batch_size)
            for row in logits
        ]

    def model_marker(self) -> dict:
        """Installed manifest marker (repository/revision), if present."""
        path = os.path.join(self._model_dir, ".papyrus-model.json")
        try:
            with open(path, encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, ValueError):
            return {}

    def _logit_batches(self, pairs, batch_size):
        self._ensure()
        if not pairs or self._session is None:
            return
        import numpy as np

        input_names = {item.name for item in self._session.get_inputs()}
        step = max(1, min(batch_size, 32))
        for start in range(0, len(pairs), step):
            encoded = self._tokenizer.encode_batch(pairs[start:start + step])
            feeds = {
                "input_ids": np.asarray([item.ids for item in encoded], dtype=np.int64),
                "attention_mask": np.asarray(
                    [item.attention_mask for item in encoded], dtype=np.int64
                ),
                "token_type_ids": np.asarray(
                    [item.type_ids for item in encoded], dtype=np.int64
                ),
            }
            yield self._session.run(
                None,
                {key: value for key, value in feeds.items() if key in input_names},
            )[0]

    def diagnostics(self) -> dict:
        return {
            "available": self.is_available(),
            "model_dir": os.path.abspath(self._model_dir),
            "last_error": self._last_error,
        }

    def _ensure(self) -> None:
        if self.runtime.ensure() and not self._labels_read:
            self._labels_read = True
            self._read_labels()

    def _read_labels(self) -> None:
        config_path = os.path.join(self._model_dir, "config.json")
        try:
            with open(config_path, encoding="utf-8") as stream:
                raw = json.load(stream).get("id2label") or {}
        except (OSError, ValueError):
            return
        if raw:
            self._labels = {int(index): str(label).lower() for index, label in raw.items()}


def _softmax(values):
    import numpy as np

    array = np.asarray(values, dtype=np.float64)
    array -= np.max(array)
    exp = np.exp(array)
    return exp / (np.sum(exp) or 1.0)
