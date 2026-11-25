"""Local sentence encoder (bge-small, ONNX): CLS pooling, L2-normalised vectors.

Used as features for the argument-role head and for comparing entity
contexts; it never labels anything on its own.
"""
from __future__ import annotations

from typing import List, Sequence

from core.services.intelligence.onnx_text_runtime import LazyOnnxSession

BATCH_SIZE = 32
MAX_TOKENS = 256


class SentenceEncoder:
    def __init__(self, model_dir: str) -> None:
        self.runtime = LazyOnnxSession(model_dir, max_length=MAX_TOKENS)

    def is_available(self) -> bool:
        return self.runtime.is_installed()

    def reload(self) -> None:
        self.runtime.reset()

    def encode(self, texts: Sequence[str]):
        """``[len(texts), dim]`` float32 unit vectors, or None when unavailable."""
        import numpy as np
        if not texts or not self.runtime.ensure():
            return None
        out: List = []
        for first in range(0, len(texts), BATCH_SIZE):
            out.append(self._batch(list(texts[first:first + BATCH_SIZE])))
        return np.concatenate(out, axis=0)

    def _batch(self, texts: List[str]):
        import numpy as np
        session, tokenizer = self.runtime.session, self.runtime.tokenizer
        encoded = tokenizer.encode_batch([t or " " for t in texts])
        feeds = {
            "input_ids": np.asarray([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.asarray([e.attention_mask for e in encoded], dtype=np.int64),
            "token_type_ids": np.asarray([e.type_ids for e in encoded], dtype=np.int64),
        }
        names = {item.name for item in session.get_inputs()}
        hidden = session.run(None, {k: v for k, v in feeds.items() if k in names})[0]
        vectors = hidden[:, 0, :].astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-12)
