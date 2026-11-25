"""One lazy ONNX session + tokenizer, shared by every local text model.

The cross-encoder reranker, NLI scorer(s) and sentence classifier each kept
their own copy of this load/forget code and never let go of a session. This
loads on first use, records when it was last used, and can be released by the
memory manager; the next call simply loads it again.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

# Loaded ONNX + tokenizer memory relative to the model file on disk.
LOADED_SIZE_FACTOR = 1.6
BUSY_WINDOW_S = 10.0     # used this recently → treated as in use, never released


class LazyOnnxSession:
    def __init__(self, model_dir: str, *, max_length: int = 512) -> None:
        self.model_dir = model_dir
        self.max_length = max_length
        self._session = None
        self.tokenizer = None
        self.last_error = ""
        self._attempted = False
        self._last_used = 0.0
        self._lock = threading.Lock()

    @property
    def session(self):
        """Every use (each scoring batch) keeps the model marked busy, so the
        memory manager never releases it in the middle of a long loop."""
        if self._session is not None:
            self._last_used = time.monotonic()
        return self._session

    @session.setter
    def session(self, value) -> None:
        self._session = value

    @property
    def model_path(self) -> str:
        return os.path.join(self.model_dir, "model.onnx")

    @property
    def tokenizer_path(self) -> str:
        return os.path.join(self.model_dir, "tokenizer.json")

    def is_installed(self) -> bool:
        return os.path.isfile(self.model_path) and os.path.isfile(self.tokenizer_path)

    def ensure(self) -> bool:
        """Load once (thread-safe); True when a session is ready."""
        self._last_used = time.monotonic()
        with self._lock:
            if self._attempted:
                return self.session is not None
            self._attempted = True
            if not self.is_installed():
                return False
            try:
                import onnxruntime  # type: ignore
                from tokenizers import Tokenizer  # type: ignore
                self.session = onnxruntime.InferenceSession(
                    self.model_path, providers=["CPUExecutionProvider"])
                self.tokenizer = Tokenizer.from_file(self.tokenizer_path)
                self.tokenizer.enable_truncation(max_length=self.max_length)
                self.tokenizer.enable_padding()
                self.last_error = ""
            except Exception as exc:
                self.session = self.tokenizer = None
                self.last_error = str(exc)
        return self.session is not None

    def reset(self) -> None:
        """Forget everything, including a failed load (e.g. after a model install)."""
        with self._lock:
            self.session = self.tokenizer = None
            self._attempted = False
            self.last_error = ""

    def release(self, _level=None) -> int:
        """Free the session for the memory manager; it reloads on next use."""
        freed = self.memory_bytes()
        if freed:
            self.reset()
        return freed

    def memory_bytes(self) -> int:
        if self._session is None:
            return 0
        try:
            size = os.path.getsize(self.model_path)
            external = self.model_path + ".data"     # weights stored as external data
            if os.path.isfile(external):
                size += os.path.getsize(external)
            return int(size * LOADED_SIZE_FACTOR)
        except OSError:
            return 0

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used if self._last_used else 0.0

    def busy(self) -> bool:
        return self._session is not None and self.idle_seconds() < BUSY_WINDOW_S
