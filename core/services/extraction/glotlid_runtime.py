"""GlotLID language identification through its ONNX export (no fastText package).

The graph holds only the numeric part of fastText inference (mean of
embedding rows, output matrix, softmax). This module reproduces fastText's
feature ids: whitespace tokens plus ``</s>``, each known word's id and the
FNV-1a hashed character n-grams (``minn``..``maxn``) of ``<word>``, with
bytes sign-extended as fastText does.
"""
from __future__ import annotations

import json
import os
import threading
import time
from functools import lru_cache
from typing import List, Optional, Tuple

EOS = "</s>"
LOADED_SIZE_FACTOR = 1.1
VOCABULARY_BYTES = 120 * 1024 * 1024   # the 634k-word lookup table in Python
FEATURE_CACHE = 30_000                 # words whose n-gram ids are kept (~25 MB)
BUSY_WINDOW_S = 10.0


def fnv1a(text: str) -> int:
    value = 2166136261
    for byte in text.encode("utf-8"):
        if byte >= 0x80:
            byte |= 0xFFFFFF00
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value


def char_ngrams(word: str, minn: int, maxn: int, bucket: int, offset: int) -> List[int]:
    chars = "<" + word + ">"
    out = []
    for i in range(len(chars)):
        for j in range(i + minn, min(len(chars), i + maxn) + 1):
            out.append(offset + fnv1a(chars[i:j]) % bucket)
    return out


class GlotLidRuntime:
    """Lazy session + featuriser; ``detect`` returns (iso639-3, probability)."""

    def __init__(self, model_dir: str) -> None:
        self.model_dir = model_dir
        self._session = None
        self._labels: List[str] = []
        self._vocab = {}
        self._meta = {}
        self._attempted = False
        self._last_used = 0.0
        self._lock = threading.Lock()
        self.last_error = ""
        self._features = lru_cache(maxsize=FEATURE_CACHE)(self._word_features)

    def is_available(self) -> bool:
        return all(os.path.isfile(os.path.join(self.model_dir, name))
                   for name in ("model.onnx", "vocab.txt", "labels.json", "config.json"))

    def ensure(self) -> bool:
        self._last_used = time.monotonic()
        with self._lock:
            if self._attempted:
                return self._session is not None
            self._attempted = True
            if not self.is_available():
                return False
            try:
                import onnxruntime  # type: ignore
                with open(os.path.join(self.model_dir, "config.json"), encoding="utf-8") as stream:
                    self._meta = json.load(stream)
                with open(os.path.join(self.model_dir, "labels.json"), encoding="utf-8") as stream:
                    self._labels = [label.replace("__label__", "") for label in json.load(stream)]
                with open(os.path.join(self.model_dir, "vocab.txt"), encoding="utf-8") as stream:
                    self._vocab = {word: index for index, word in enumerate(stream.read().split("\n")) if word}
                self._session = onnxruntime.InferenceSession(
                    os.path.join(self.model_dir, "model.onnx"), providers=["CPUExecutionProvider"])
            except Exception as exc:
                self._session, self.last_error = None, str(exc)
        return self._session is not None

    def _word_features(self, word: str) -> Tuple[int, ...]:
        meta = self._meta
        index = self._vocab.get(word)
        if word == EOS:
            return (index,) if index is not None else ()
        grams = char_ngrams(word, meta["minn"], meta["maxn"], meta["bucket"], meta["nwords"])
        return tuple(([index] if index is not None else []) + grams)

    def features(self, text: str):
        import numpy as np
        tokens = text.replace("\n", " ").replace("\t", " ").split() + [EOS]
        ids: List[int] = []
        for token in tokens:
            ids.extend(self._features(token))
        return np.asarray(ids, dtype=np.int64)

    def detect(self, text: str) -> Optional[Tuple[str, float]]:
        """Best (language code without script, probability), or None when unavailable."""
        if not text or not text.strip() or not self.ensure():
            return None
        import numpy as np
        self._last_used = time.monotonic()
        probs = self._session.run(None, {"input_ids": self.features(text)})[0]
        best = int(np.argmax(probs))
        return self._labels[best].split("_")[0], float(probs[best])

    def reload(self) -> None:
        with self._lock:
            self._session, self._attempted, self.last_error = None, False, ""
            self._vocab = {}
            self._features.cache_clear()

    def release(self, _level=None) -> int:
        freed = self.memory_bytes()
        if freed:
            self.reload()
        return freed

    def memory_bytes(self) -> int:
        if self._session is None:
            return 0
        try:
            return int(os.path.getsize(os.path.join(self.model_dir, "model.onnx")) * LOADED_SIZE_FACTOR) + VOCABULARY_BYTES
        except OSError:
            return 0

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used if self._last_used else 0.0

    def busy(self) -> bool:
        return self._session is not None and self.idle_seconds() < BUSY_WINDOW_S
