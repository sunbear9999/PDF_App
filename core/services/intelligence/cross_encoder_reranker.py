"""
CrossEncoderReranker — optional ONNX cross-encoder for Stage-C reranking.

A small quantized MS-MARCO MiniLM-class cross-encoder scores (query, passage)
pairs for final precision ordering. This is a classifier pass, not a generative
call — cheap even on CPU. Runs via onnxruntime + tokenizers (no torch). If no
model is installed it reports unavailable and retrieval falls back to the
deterministic lexical rerank already in HybridRetrievalService.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple
from core.services.intelligence.onnx_text_runtime import LazyOnnxSession

DEFAULT_MODEL_DIR = os.environ.get(
    "PAPYRUS_RERANKER_DIR",
    os.path.join(
        os.path.expanduser("~"), ".local", "share", "Papyrus", "models",
        "cross_encoder",
    ),
)

class CrossEncoderReranker:
    def __init__(self, model_dir: Optional[str] = None) -> None:
        self._model_dir = model_dir or DEFAULT_MODEL_DIR
        self.runtime = LazyOnnxSession(self._model_dir)
        self._last_error = ""

    @property
    def _session(self):
        return self.runtime.session

    @property
    def _tokenizer(self):
        return self.runtime.tokenizer

    def is_available(self) -> bool:
        self._ensure()
        return self._session is not None

    def score(self, query: str, passages: List[str]) -> List[float]:
        """Return one relevance score per passage (higher = more relevant)."""
        self._ensure()
        if self._session is None or not passages:
            return [0.0] * len(passages)
        try:
            return self._score_onnx(query, passages)
        except Exception as exc:
            self._last_error = str(exc)
            return [0.0] * len(passages)

    def rerank(self, query: str, items: List[Tuple[str, object]], top_k: int = 20):
        """Rerank (passage_text, payload) pairs; returns payloads best-first.

        Only the top_k candidates by incoming order are scored (bounded cost).
        """
        if not items or not self.is_available():
            return None
        head = items[:top_k]
        scores = self.score(query, [text for text, _ in head])
        ranked = sorted(zip(head, scores), key=lambda kv: kv[1], reverse=True)
        return [(payload, score) for (_, payload), score in ranked]

    # -- internals ---------------------------------------------------------

    def reload(self) -> None:
        """Forget a failed/old session so a newly installed model is live now."""
        self.runtime.reset()
        self._last_error = ""

    def diagnostics(self) -> dict:
        return {
            "available": self.is_available(),
            "model_dir": os.path.abspath(self._model_dir),
            "last_error": self._last_error or self.runtime.last_error,
        }

    def _ensure(self) -> None:
        self.runtime.ensure()

    def _score_onnx(self, query: str, passages: List[str]) -> List[float]:
        import numpy as np
        scores = []
        batch_size = 16
        for start in range(0, len(passages), batch_size):
            batch = passages[start:start + batch_size]
            encoded = self._tokenizer.encode_batch([
                (query, passage) for passage in batch
            ])
            feeds = {
                "input_ids": np.asarray([enc.ids for enc in encoded], dtype=np.int64),
                "attention_mask": np.asarray(
                    [enc.attention_mask for enc in encoded], dtype=np.int64
                ),
                "token_type_ids": np.asarray(
                    [enc.type_ids for enc in encoded], dtype=np.int64
                ),
            }
            inputs = {i.name for i in self._session.get_inputs()}
            feeds = {k: v for k, v in feeds.items() if k in inputs}
            logits = self._session.run(None, feeds)[0]
            for row in logits:
                flat = np.asarray(row).reshape(-1)
                scores.append(
                    float(flat[0]) if len(flat) == 1 else float(np.max(flat))
                )
        return scores
