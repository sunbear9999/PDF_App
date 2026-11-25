# core/llm_manager.py
"""
LLM provider layer.

Architecture
============
LLMProvider (Protocol)
    └── OllamaProvider  ← default, talks to a local Ollama server

LocalLLMManager
    ├── _provider: LLMProvider  ← swappable at construction time
    └── ChromaDB operations (embedding index, search)

To swap to llama.cpp (or any other backend), implement LLMProvider and pass it::

    from core.llm_manager import LocalLLMManager
    manager = LocalLLMManager(provider=LlamaCppProvider(...))

All prompt assembly, RAG orchestration, and result parsing live in the step
classes (core/engine/steps/) and MasterActionRunner — not here.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
import platform
import base64
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol, runtime_checkable

import chromadb
import fitz
import requests
from chromadb.config import Settings


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMProvider(Protocol):
    """Minimal interface every LLM backend must satisfy."""

    @property
    def ai_enabled(self) -> bool: ...

    def generate(
        self,
        prompt: str,
        system: str,
        model: str,
        *,
        stream_callback: Optional[Callable[[str], None]] = None,
        abort_event=None,
        json_mode: bool = False,
        temperature: float = 0.7,
        num_predict: int = 2048,
        num_ctx: int = 16384,
        stop: Optional[List[str]] = None,
        images: Optional[List[dict]] = None,
        **kwargs,
    ) -> str: ...

    def embed(self, text: str) -> List[float]: ...

    def embed_batch(self, texts: List[str]) -> List[List[float]]: ...

    def available_models(self) -> List[str]: ...

    def supports_model_capability(self, model: str, capability: str) -> bool: ...

    def preload_model(self, model_name: str) -> None: ...

    def unload_all_models(self) -> None: ...

    def check_and_pull_embedding_model(
        self, progress_callback: Optional[Callable[[str], None]] = None
    ) -> None: ...

    def score_completion_logprobs(
        self, prefix: str, completion: str, model: str
    ) -> "CompletionLogprobResult": ...

    def calculate_completion_pll(self, prefix: str, completion: str, model: str) -> float: ...


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------

_LOGPROB_CONNECT_TIMEOUT = 10
_LOGPROB_READ_TIMEOUT = 120
_MEMORY_ERROR_MARKERS = (
    "requires more system memory",
    "not enough memory",
    "out of memory",
    "memory allocation",
)


@dataclass
class TokenLogprob:
    token: str
    logprob: float
    start: int = 0
    end: int = 0


@dataclass
class CompletionLogprobResult:
    score: float
    token_logprobs: list[TokenLogprob]
    mode: str
    exact: bool
    prompt: str
    completion: str
    diagnostics: dict = field(default_factory=dict)

    @property
    def token_count(self) -> int:
        return len(self.token_logprobs)

    @property
    def coverage(self) -> float:
        return 1.0 if self.exact and self.token_logprobs else 0.0


def _openai_completions_endpoint(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")]
    if base.endswith("/v1"):
        return f"{base}/completions"
    return f"{base}/v1/completions"


def _ollama_generate_endpoint(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/api"):
        return f"{base}/generate"
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/api/generate"


def normalize_top_logprobs(raw_top_logprobs) -> dict[str, float]:
    normalized: dict[str, float] = {}
    if isinstance(raw_top_logprobs, dict):
        iterable = raw_top_logprobs.items()
    elif isinstance(raw_top_logprobs, list):
        iterable = []
        for item in raw_top_logprobs:
            if not isinstance(item, dict):
                continue
            token = item.get("token") or item.get("tok_str") or item.get("text")
            value = item.get("logprob")
            if value is None and item.get("prob") is not None:
                value = math.log(float(item["prob"]))
            iterable.append((token, value))
    else:
        return normalized

    for token, value in iterable:
        if token is None:
            continue
        try:
            if isinstance(value, dict):
                value = value.get("logprob")
            normalized[str(token)] = float(value)
        except (TypeError, ValueError):
            continue
    return normalized


def _extract_openai_top_logprobs(data: dict, model: str) -> dict[str, float]:
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("No 'choices' returned in response.")

    lp_obj = choices[0].get("logprobs")
    if not lp_obj or not isinstance(lp_obj, dict):
        raise RuntimeError(
            f"LOGPROBS_UNAVAILABLE: generation logprobs returned null for model '{model}'."
        )

    candidate_payloads = []
    top_logprobs = lp_obj.get("top_logprobs")
    if top_logprobs:
        if isinstance(top_logprobs, list) and top_logprobs:
            candidate_payloads.append(top_logprobs[0])
        candidate_payloads.append(top_logprobs)

    content = lp_obj.get("content")
    if isinstance(content, list) and content:
        first_content = content[0]
        if isinstance(first_content, dict):
            nested_top_logprobs = first_content.get("top_logprobs")
            if nested_top_logprobs:
                candidate_payloads.append(nested_top_logprobs)
            candidate_payloads.append(first_content)

    for candidate in candidate_payloads:
        normalized = normalize_top_logprobs(candidate)
        if normalized:
            return normalized

    raise RuntimeError(f"LOGPROBS_UNAVAILABLE: could not parse top logprobs for '{model}'.")


def fetch_openai_next_token_logprobs(
    api_base: str,
    prefix: str,
    model: str,
    *,
    top_k: int = 100,
    provider_name: str = "LLMProvider",
) -> dict[str, float]:
    endpoint = _openai_completions_endpoint(api_base)
    requested_top_ks = [top_k, 50, 20, 10, 5, 1]
    requested_top_ks = list(dict.fromkeys(k for k in requested_top_ks if 0 < k <= top_k))
    last_bad_request: Optional[str] = None

    for requested_top_k in requested_top_ks:
        base_payload = {
            "model": model,
            "prompt": prefix,
            "max_tokens": 1,
            "temperature": 0.0,
            "stream": False,
        }
        schemas = [
            ("modern", {**base_payload, "logprobs": True, "top_logprobs": requested_top_k}),
            ("legacy", {**base_payload, "logprobs": requested_top_k}),
        ]

        try:
            for schema_name, payload in schemas:
                resp = requests.post(
                    endpoint,
                    json=payload,
                    timeout=(_LOGPROB_CONNECT_TIMEOUT, _LOGPROB_READ_TIMEOUT),
                )
                if resp.status_code in {400, 422}:
                    last_bad_request = resp.text
                    continue

                resp.raise_for_status()
                try:
                    return _extract_openai_top_logprobs(resp.json(), model)
                except RuntimeError as exc:
                    last_bad_request = f"{schema_name} schema parse failed: {exc}"
                    continue

        except requests.exceptions.ReadTimeout:
            raise RuntimeError(
                f"[{provider_name}] calculate_completion_pll timed out ({_LOGPROB_READ_TIMEOUT}s) "
                f"for model '{model}'. The model may be too large for available hardware."
            ) from None
        except requests.exceptions.RequestException as exc:
            detail = f"; last bad request: {last_bad_request}" if last_bad_request else ""
            raise RuntimeError(
                f"[{provider_name}] calculate_completion_pll HTTP error at {endpoint}: {exc}{detail}"
            ) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"[{provider_name}] calculate_completion_pll parse error: {exc}"
            ) from exc

        if requested_top_k == requested_top_ks[-1]:
            break

    raise RuntimeError(
        f"[{provider_name}] calculate_completion_pll could not request logprobs "
        f"from {endpoint}: {last_bad_request or 'unknown error'}"
    )


def _normalize_echo_logprobs(logprobs_obj: dict) -> tuple[list[str], list[Optional[float]]]:
    tokens = logprobs_obj.get("tokens") or []
    token_logprobs = logprobs_obj.get("token_logprobs") or []
    if tokens and token_logprobs:
        return [str(token) for token in tokens], [
            None if value is None else float(value) for value in token_logprobs
        ]

    content = logprobs_obj.get("content")
    if isinstance(content, list):
        tokens = []
        token_logprobs = []
        for item in content:
            if not isinstance(item, dict):
                continue
            token = item.get("token") or item.get("text") or item.get("tok_str")
            if token is None:
                continue
            tokens.append(str(token))
            value = item.get("logprob")
            token_logprobs.append(None if value is None else float(value))
        return tokens, token_logprobs

    return [], []


def _completion_token_span_from_echo(
    prefix: str,
    completion: str,
    tokens: list[str],
    token_logprobs: list[Optional[float]],
) -> list[TokenLogprob]:
    if len(tokens) != len(token_logprobs):
        raise RuntimeError("LOGPROBS_UNSUPPORTED: token/logprob count mismatch.")

    expected_text = prefix + completion
    cursor = 0
    completion_start = len(prefix)
    selected: list[TokenLogprob] = []

    for token, value in zip(tokens, token_logprobs):
        start = cursor
        end = start + len(token)
        cursor = end
        if end <= completion_start:
            continue
        if value is None:
            raise RuntimeError("LOGPROBS_UNSUPPORTED: completion token logprob is null.")
        selected.append(TokenLogprob(token=token, logprob=float(value), start=start, end=end))

    echoed_text = "".join(tokens)
    if echoed_text != expected_text:
        raise RuntimeError(
            "LOGPROBS_UNSUPPORTED: echoed tokens do not reconstruct prompt+completion."
        )
    if not selected:
        raise RuntimeError("LOGPROBS_UNSUPPORTED: no completion tokens were scored.")
    return selected


def fetch_openai_completion_logprobs(
    api_base: str,
    prefix: str,
    completion: str,
    model: str,
    *,
    provider_name: str = "LLMProvider",
) -> CompletionLogprobResult:
    endpoint = _openai_completions_endpoint(api_base)
    prompt = prefix + completion
    base_payload = {
        "model": model,
        "prompt": prompt,
        "max_tokens": 0,
        "temperature": 0.0,
        "stream": False,
        "echo": True,
    }
    schemas = [
        ("openai_echo_modern", {**base_payload, "logprobs": True, "top_logprobs": 0}),
        ("openai_echo_legacy", {**base_payload, "logprobs": 0}),
    ]
    last_error = ""

    for mode, payload in schemas:
        try:
            resp = requests.post(
                endpoint,
                json=payload,
                timeout=(_LOGPROB_CONNECT_TIMEOUT, _LOGPROB_READ_TIMEOUT),
            )
            if resp.status_code in {400, 422}:
                last_error = resp.text
                continue
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("response has no choices")
            logprobs_obj = choices[0].get("logprobs")
            if not isinstance(logprobs_obj, dict):
                raise RuntimeError("response has no logprobs object")
            tokens, token_logprobs = _normalize_echo_logprobs(logprobs_obj)
            completion_tokens = _completion_token_span_from_echo(
                prefix, completion, tokens, token_logprobs
            )
            score = sum(item.logprob for item in completion_tokens) / len(completion_tokens)
            return CompletionLogprobResult(
                score=score,
                token_logprobs=completion_tokens,
                mode=mode,
                exact=True,
                prompt=prefix,
                completion=completion,
                diagnostics={
                    "endpoint": endpoint,
                    "echoed_token_count": len(tokens),
                    "completion_token_count": len(completion_tokens),
                },
            )
        except requests.exceptions.ReadTimeout:
            raise RuntimeError(
                f"[{provider_name}] exact logprob scoring timed out ({_LOGPROB_READ_TIMEOUT}s) "
                f"for model '{model}'."
            ) from None
        except requests.exceptions.RequestException as exc:
            last_error = str(exc)
            continue
        except (RuntimeError, KeyError, IndexError, TypeError, ValueError) as exc:
            last_error = str(exc)
            continue

    raise RuntimeError(
        f"LOGPROBS_UNSUPPORTED: [{provider_name}] exact completion logprobs are unavailable "
        f"at {endpoint}: {last_error or 'unknown error'}"
    )


OLLAMA_STATUS_TIMEOUT_S = 2
DEFAULT_KEEP_ALIVE = "10m"


def _model_key(name) -> str:
    """Ollama reports "model:latest" for a request of "model"."""
    name = str(name or "").strip()
    return name if not name or ":" in name else f"{name}:latest"


class OllamaProvider:
    """Talks to a local Ollama HTTP server."""

    EMBEDDING_MODEL = "nomic-embed-text"
    # Shown only when the Ollama server cannot be reached to list real models;
    # kept as a named default rather than inline literals (rules.md 2.13/16.3).
    FALLBACK_MODEL_SUGGESTIONS = ["qwen2.5:7b", "llama3", "mistral"]

    def __init__(self, api_base: str = "http://localhost:11434/api"):
        self.api_base = api_base
        self._ollama_proc: Optional[subprocess.Popen] = None
        self._ai_enabled = False
        self._capability_cache: dict[tuple[str, str], bool] = {}
        self._context_limit_cache: dict[str, int] = {}
        self._active_generation_model: str = ""
        self._init_residency()
        self._start_server()

    # Hooks set by the memory manager (MemoryManagerService.attach_llm).
    keep_alive_source: Optional[Callable[[str], str]] = None
    on_out_of_memory: Optional[Callable[[], None]] = None

    def _init_residency(self) -> None:
        """Residency bookkeeping read by the memory manager."""
        self._last_used: dict[str, float] = {}
        self._active: dict[str, int] = {}
        self._model_sizes: dict[str, int] = {}
        self.loaded_by_papyrus: set[str] = set()

    # -- LLMProvider protocol ------------------------------------------------

    @property
    def ai_enabled(self) -> bool:
        return self._ai_enabled

    def generate(
        self,
        prompt: str,
        system: str,
        model: str,
        *,
        stream_callback: Optional[Callable[[str], None]] = None,
        abort_event=None,
        json_mode: bool = False,
        temperature: float = 0.7,
        num_predict: int = 2048,
        num_ctx: int = 16384,
        stop: Optional[List[str]] = None,
        images: Optional[List[dict]] = None,
        **kwargs,
    ) -> str:
        keep_alive = self._generation_keep_alive()
        self._prepare_generation_model(model)
        self._begin_use(model)
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": True,
            "keep_alive": keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }
        if stop:
            payload["options"]["stop"] = stop
        if images:
            payload["images"] = [item["data"] for item in images]
        if json_mode:
            payload["format"] = "json"

        full_response = ""
        try:
            with requests.post(
                f"{self.api_base}/generate",
                json=payload,
                stream=True,
                timeout=(15, 600),
            ) as response:
                if response.status_code != 200:
                    try:
                        detail = response.json().get("error", response.text)
                    except Exception:
                        detail = response.text
                    raise ConnectionError(f"Ollama Error ({response.status_code}): {detail}")

                for line in response.iter_lines():
                    if abort_event and abort_event.is_set():
                        aborted = "\n[Process Aborted by User]"
                        if stream_callback:
                            stream_callback(aborted)
                        full_response += aborted
                        break
                    if line:
                        txt = json.loads(line).get("response", "")
                        full_response += txt
                        if stream_callback:
                            stream_callback(txt)

        except requests.exceptions.ReadTimeout:
            err = "\n[Generation Error: The AI took too long to respond. Hardware limits exceeded.]"
            if stream_callback:
                stream_callback(err)
            full_response += err
        except Exception as exc:
            err = f"\n[Generation Error: {exc}]"
            if self._is_memory_error(str(exc)):
                (self.on_out_of_memory or self.unload_all_models)()
            if stream_callback:
                stream_callback(err)
            full_response += err
        finally:
            self._end_use(model)
            if str(keep_alive).strip() in {"0", "0s"}:
                self._active_generation_model = ""

        return full_response

    def supports_model_capability(self, model: str, capability: str) -> bool:
        if capability != "vision":
            return True
        cache_key = (model, capability)
        if cache_key in self._capability_cache:
            return self._capability_cache[cache_key]
        try:
            response = requests.post(
                f"{self.api_base}/show", json={"model": model}, timeout=5
            )
            if response.status_code == 200:
                capabilities = response.json().get("capabilities") or []
                supported = "vision" in {str(item).lower() for item in capabilities}
                self._capability_cache[cache_key] = supported
                return supported
        except Exception:
            pass
        return False

    def model_context_limit(self, model: str) -> Optional[int]:
        """Read the installed model's native limit from Ollama metadata."""
        if model in self._context_limit_cache:
            return self._context_limit_cache[model]
        try:
            response = requests.post(
                f"{self.api_base}/show", json={"model": model}, timeout=5
            )
            if response.status_code != 200:
                return None
            info = response.json().get("model_info") or {}
            limits = [
                int(value) for key, value in info.items()
                if str(key).endswith(".context_length")
                and isinstance(value, (int, float)) and value > 0
            ]
            if limits:
                self._context_limit_cache[model] = min(limits)
                return min(limits)
        except Exception:
            pass
        return None

    def embed(self, text: str) -> List[float]:
        payload = {
            "model": self.EMBEDDING_MODEL,
            "prompt": text,
            "keep_alive": self._embedding_keep_alive(),
            "options": {"num_ctx": 8192},
        }
        self._begin_use(self.EMBEDDING_MODEL)
        try:
            response = requests.post(
                f"{self.api_base}/embeddings", json=payload, timeout=(10, 300)
            )
            if response.status_code == 200:
                return response.json().get("embedding")
            raise RuntimeError(f"Server returned {response.status_code}: {response.text}")
        except Exception as exc:
            raise RuntimeError(f"Embedding failed: {exc}") from exc
        finally:
            self._end_use(self.EMBEDDING_MODEL)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        payload = {
            "model": self.EMBEDDING_MODEL,
            "input": texts,
            "keep_alive": self._embedding_keep_alive(),
            "options": {"num_ctx": 8192},
        }
        self._begin_use(self.EMBEDDING_MODEL)
        try:
            response = requests.post(
                f"{self.api_base}/embed", json=payload, timeout=(15, 600)
            )
            if response.status_code == 200:
                return response.json().get("embeddings")
        except Exception as exc:
            print(f"[OllamaProvider] Batch embed failed, falling back to sequential: {exc}")
        finally:
            self._end_use(self.EMBEDDING_MODEL)
        return [self.embed(t) for t in texts]

    def available_models(self) -> List[str]:
        try:
            response = requests.get(f"{self.api_base}/tags", timeout=3)
            if response.status_code == 200:
                models = [
                    m["name"] for m in response.json().get("models", [])
                    if m["name"] != self.EMBEDDING_MODEL
                    and not m["name"].startswith(f"{self.EMBEDDING_MODEL}:")
                ]
                if models:
                    return models
        except Exception as exc:
            print(f"[OllamaProvider] Error fetching models: {exc}")
        return list(self.FALLBACK_MODEL_SUGGESTIONS)

    def preload_model(self, model_name: str) -> None:
        if not model_name:
            return
        self.unload_all_models()
        try:
            response = requests.post(
                f"{self.api_base}/generate",
                json={"model": model_name, "keep_alive": self._generation_keep_alive()},
                timeout=(15, 600),
            )
            if response.status_code == 200:
                self.loaded_by_papyrus.add(_model_key(model_name))
                self._last_used[_model_key(model_name)] = time.monotonic()
                print(f"[OllamaProvider] Preloaded {model_name}.")
            else:
                print(f"[OllamaProvider] Failed to preload model: {response.status_code}")
        except Exception as exc:
            print(f"[OllamaProvider] Failed to preload model: {exc}")

    def unload_all_models(self) -> None:
        """Unload the models Papyrus loaded; other apps' models are left alone."""
        for model in self.loaded_models():
            if model["name"] in self.loaded_by_papyrus:
                self.unload_model(model["name"])
        self._active_generation_model = ""

    def loaded_models(self) -> List[dict]:
        """Models resident in the Ollama server: name, size and VRAM bytes."""
        try:
            resp = requests.get(f"{self.api_base}/ps", timeout=OLLAMA_STATUS_TIMEOUT_S)
            if resp.status_code != 200:
                return []
            return [{"name": _model_key(item.get("name")), "size": int(item.get("size") or 0),
                     "size_vram": int(item.get("size_vram") or 0)}
                    for item in resp.json().get("models", []) if item.get("name")]
        except Exception:
            return []

    def model_size(self, model_name: str) -> int:
        """Download size of an installed model (a lower bound on its loaded size)."""
        key = _model_key(model_name)
        if key not in self._model_sizes:
            try:
                resp = requests.get(f"{self.api_base}/tags", timeout=OLLAMA_STATUS_TIMEOUT_S)
                for item in resp.json().get("models", []) if resp.status_code == 200 else []:
                    self._model_sizes[_model_key(item.get("name"))] = int(item.get("size") or 0)
            except Exception:
                return 0
        return self._model_sizes.get(key, 0)

    def model_busy(self, model_name: str) -> bool:
        return self._active.get(_model_key(model_name), 0) > 0

    def model_idle_seconds(self, model_name: str) -> float:
        used = self._last_used.get(_model_key(model_name))
        return time.monotonic() - used if used else 0.0

    def embedding_model_name(self) -> str:
        return _model_key(self.EMBEDDING_MODEL)

    def _begin_use(self, model_name: str) -> None:
        if "_active" not in self.__dict__:
            self._init_residency()
        key = _model_key(model_name)
        self._active[key] = self._active.get(key, 0) + 1
        self._last_used[key] = time.monotonic()
        self.loaded_by_papyrus.add(key)

    def _end_use(self, model_name: str) -> None:
        key = _model_key(model_name)
        self._active[key] = max(0, self._active.get(key, 0) - 1)
        self._last_used[key] = time.monotonic()

    def _prepare_generation_model(self, model_name: str) -> None:
        model_name = str(model_name or "").strip()
        if not model_name:
            return
        previous = self._active_generation_model
        if previous and previous != model_name:
            self._unload_model(previous)
        self._active_generation_model = model_name

    def unload_model(self, model_name: str, timeout: float = 5) -> None:
        model_name = str(model_name or "").strip()
        if not model_name:
            return
        try:
            requests.post(
                f"{self.api_base}/generate",
                json={"model": model_name, "keep_alive": 0},
                timeout=timeout,
            )
        except Exception:
            pass   # a short exit timeout still delivers the request
        if "loaded_by_papyrus" not in self.__dict__:
            self._init_residency()
        self.loaded_by_papyrus.discard(_model_key(model_name))
        if _model_key(self._active_generation_model) == _model_key(model_name):
            self._active_generation_model = ""

    _unload_model = unload_model

    def _generation_keep_alive(self) -> str:
        return self._keep_alive("PAPYRUS_OLLAMA_KEEP_ALIVE", "chat")

    def _embedding_keep_alive(self) -> str:
        return self._keep_alive("PAPYRUS_OLLAMA_EMBED_KEEP_ALIVE", "embed")

    def _keep_alive(self, env_name: str, role: str) -> str:
        """The environment wins; otherwise the memory manager's idle limit."""
        explicit = os.environ.get(env_name, "").strip()
        if explicit:
            return explicit
        if self.keep_alive_source:
            return self.keep_alive_source(role)
        return DEFAULT_KEEP_ALIVE

    @staticmethod
    def _is_memory_error(message: str) -> bool:
        lower = str(message or "").lower()
        return any(marker in lower for marker in _MEMORY_ERROR_MARKERS)

    def check_and_pull_embedding_model(
        self, progress_callback: Optional[Callable[[str], None]] = None
    ) -> None:
        try:
            response = requests.get(f"{self.api_base}/tags", timeout=3)
            if response.status_code == 200:
                names = [m["name"] for m in response.json().get("models", [])]
                if (
                    self.EMBEDDING_MODEL not in names
                    and f"{self.EMBEDDING_MODEL}:latest" not in names
                ):
                    if progress_callback:
                        progress_callback(
                            f"Downloading '{self.EMBEDDING_MODEL}'... (takes a few minutes)"
                        )
                    requests.post(
                        f"{self.api_base}/pull",
                        json={"name": self.EMBEDDING_MODEL},
                        timeout=(10, 1800),
                    )
        except Exception as exc:
            print(f"[OllamaProvider] Could not verify/pull embedding model: {exc}")

    def calculate_completion_pll(self, prefix: str, completion: str, model: str) -> float:
        """Return LIBRA Eq. 1 PLL using exact echoed prompt token logprobs."""
        return self.score_completion_logprobs(prefix, completion, model).score

    def score_completion_logprobs(
        self, prefix: str, completion: str, model: str
    ) -> CompletionLogprobResult:
        native_errors: list[str] = []
        try:
            return self._score_native_generate_prompt_logprobs(prefix, completion, model)
        except RuntimeError as exc:
            native_errors.append(str(exc))
        try:
            return self._score_native_generate_completion_logprobs(prefix, completion, model)
        except RuntimeError as exc:
            native_errors.append(str(exc))
        try:
            return fetch_openai_completion_logprobs(
                self.api_base,
                prefix,
                completion,
                model,
                provider_name="OllamaProvider",
            )
        except RuntimeError as exc:
            native_errors.append(str(exc))
        raise RuntimeError(
            "LOGPROBS_UNSUPPORTED: Ollama did not expose exact completion logprobs "
            "through native /api/generate or the OpenAI-compatible endpoint. "
            + " | ".join(native_errors)
        )

    def _score_native_generate_prompt_logprobs(
        self, prefix: str, completion: str, model: str
    ) -> CompletionLogprobResult:
        payload = {
            "model": model,
            "prompt": prefix + completion,
            "stream": False,
            "raw": True,
            "logprobs": True,
            "top_logprobs": 0,
            "options": {
                "num_predict": 0,
                "temperature": 0.0,
            },
        }
        data = self._post_generate_json(payload)
        raw_items = (
            data.get("prompt_logprobs")
            or data.get("prompt_eval_logprobs")
            or data.get("prompt_probabilities")
            or data.get("prompt_probs")
            or []
        )
        tokens, token_logprobs = self._tokens_from_native_items(raw_items)
        if not tokens:
            raise RuntimeError("native /api/generate returned no prompt logprob fields")
        completion_tokens = _completion_token_span_from_echo(prefix, completion, tokens, token_logprobs)
        score = sum(item.logprob for item in completion_tokens) / len(completion_tokens)
        return CompletionLogprobResult(
            score=score,
            token_logprobs=completion_tokens,
            mode="ollama_native_prompt",
            exact=True,
            prompt=prefix,
            completion=completion,
            diagnostics={
                "endpoint": _ollama_generate_endpoint(self.api_base),
                "prompt_token_count": len(tokens),
                "completion_token_count": len(completion_tokens),
            },
        )

    def _score_native_generate_completion_logprobs(
        self, prefix: str, completion: str, model: str
    ) -> CompletionLogprobResult:
        payload = {
            "model": model,
            "prompt": prefix,
            "stream": False,
            "raw": True,
            "logprobs": True,
            "top_logprobs": 20,
            "options": {
                "num_predict": max(1, len(completion) + 8),
                "temperature": 0.0,
                "stop": [completion + "\n\n"],
            },
        }
        data = self._post_generate_json(payload)
        response_text = data.get("response", "")
        raw_items = data.get("logprobs") or data.get("completion_logprobs") or []
        tokens, token_logprobs = self._tokens_from_native_items(raw_items)
        generated = "".join(tokens) if tokens else response_text
        if not generated.startswith(completion):
            raise RuntimeError(
                "native /api/generate produced text that does not match requested completion"
            )
        if not tokens:
            raise RuntimeError("native /api/generate returned no generated token logprobs")

        selected: list[TokenLogprob] = []
        cursor = 0
        for token, value in zip(tokens, token_logprobs):
            start = len(prefix) + cursor
            cursor += len(token)
            end = len(prefix) + cursor
            if cursor > len(completion):
                break
            if value is None:
                raise RuntimeError("native /api/generate returned null generated token logprob")
            selected.append(TokenLogprob(token=token, logprob=float(value), start=start, end=end))
            if cursor == len(completion):
                break
        if cursor != len(completion) or not selected:
            raise RuntimeError("native /api/generate token boundaries did not cover completion")
        score = sum(item.logprob for item in selected) / len(selected)
        return CompletionLogprobResult(
            score=score,
            token_logprobs=selected,
            mode="ollama_native_generate",
            exact=True,
            prompt=prefix,
            completion=completion,
            diagnostics={
                "endpoint": _ollama_generate_endpoint(self.api_base),
                "completion_token_count": len(selected),
                "generated_prefix": generated[: len(completion)],
            },
        )

    def _post_generate_json(self, payload: dict) -> dict:
        try:
            resp = requests.post(
                _ollama_generate_endpoint(self.api_base),
                json=payload,
                timeout=(_LOGPROB_CONNECT_TIMEOUT, _LOGPROB_READ_TIMEOUT),
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ReadTimeout:
            raise RuntimeError(
                f"[OllamaProvider] native /api/generate logprob request timed out "
                f"({_LOGPROB_READ_TIMEOUT}s)."
            ) from None
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"[OllamaProvider] native /api/generate HTTP error: {exc}") from exc

    @staticmethod
    def _tokens_from_native_items(raw_items) -> tuple[list[str], list[Optional[float]]]:
        if not isinstance(raw_items, list):
            return [], []
        tokens: list[str] = []
        token_logprobs: list[Optional[float]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            token = item.get("token") or item.get("tok_str") or item.get("text")
            value = item.get("logprob")
            if value is None and item.get("prob") is not None:
                value = math.log(float(item["prob"]))
            if value is None:
                value = item.get("token_logprob")
            if token is None:
                continue
            tokens.append(str(token))
            token_logprobs.append(None if value is None else float(value))
        return tokens, token_logprobs

    def _get_next_token_logprobs(
        self, prefix: str, model: str, top_k: int = 100
    ) -> dict[str, float]:
        """Return top-k next-token logprobs natively via Ollama's /api/generate."""
        requested_top_ks = [top_k, 50, 20, 10, 5, 1]
        requested_top_ks = list(dict.fromkeys(k for k in requested_top_ks if 0 < k <= top_k))
        last_bad_request: Optional[str] = None

        for requested_top_k in requested_top_ks:
            payload = {
                "model": model,
                "prompt": prefix,
                "logprobs": True,
                "top_logprobs": requested_top_k,
                "stream": False,
                "options": {
                    "num_predict": 1,
                    "temperature": 0.0
                }
            }
            try:
                resp = requests.post(
                    _ollama_generate_endpoint(self.api_base),
                    json=payload,
                    timeout=(_LOGPROB_CONNECT_TIMEOUT, _LOGPROB_READ_TIMEOUT),
                )

                # Handle context length or top_logprobs limit errors gracefully
                if resp.status_code in {400, 422} and requested_top_k != requested_top_ks[-1]:
                    last_bad_request = resp.text
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Ollama native format: data["logprobs"] is a list of token dicts
                logprobs_arr = data.get("logprobs")
                if not logprobs_arr:
                    raise RuntimeError(
                        f"LOGPROBS_UNAVAILABLE: logprobs returned null for model '{model}'. "
                        "Ensure the model supports logprobs."
                    )

                # We generated 1 token, so take the first item's top_logprobs
                top_lp_list = logprobs_arr[0].get("top_logprobs")
                if not top_lp_list:
                    raise RuntimeError(
                        f"LOGPROBS_UNAVAILABLE: 'top_logprobs' missing for model '{model}'."
                    )

                # Your existing normalize_top_logprobs helper handles this list of dicts perfectly
                normalized = normalize_top_logprobs(top_lp_list)
                if not normalized:
                    raise RuntimeError(
                        f"LOGPROBS_UNAVAILABLE: could not parse top logprobs for model '{model}'."
                    )
                return normalized

            except requests.exceptions.ReadTimeout:
                raise RuntimeError(
                    f"[OllamaProvider] calculate_completion_pll timed out ({_LOGPROB_READ_TIMEOUT}s) "
                    f"for model '{model}'. Hardware limits exceeded."
                ) from None
            except requests.exceptions.RequestException as exc:
                detail = f"; last bad request: {last_bad_request}" if last_bad_request else ""
                raise RuntimeError(
                    f"[OllamaProvider] HTTP error at /api/generate: {exc}{detail}"
                ) from exc
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(
                    f"[OllamaProvider] parse error: {exc}"
                ) from exc

        raise RuntimeError(
            f"[OllamaProvider] could not request logprobs: {last_bad_request or 'unknown error'}"
        )
    def shutdown(self) -> None:
        if self._ollama_proc is not None:
            self._ollama_proc.terminate()
            try:
                self._ollama_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._ollama_proc.kill()
            self._ollama_proc = None

    # -- Internal ------------------------------------------------------------

    def _start_server(self) -> None:
        ollama_cmd = shutil.which("ollama")
        if not ollama_cmd:
            win_path = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
            if os.path.exists(win_path):
                ollama_cmd = win_path
            else:
                print("[OllamaProvider] Ollama not found. Running in standard PDF mode.")
                return

        try:
            requests.get("http://localhost:11434/", timeout=2)
            self._ai_enabled = True
        except requests.exceptions.ConnectionError:
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if platform.system() == "Windows" else {}
            self._ollama_proc = subprocess.Popen(
                [ollama_cmd, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **flags,
            )
            time.sleep(3)
            self._ai_enabled = True

    def _resolve_model(self, requested: str) -> str:
        """Return the best installed model that matches *requested*."""
        try:
            resp = requests.get(f"{self.api_base}/tags", timeout=2)
            if resp.status_code == 200:
                installed = [
                    m["name"] for m in resp.json().get("models", [])
                    if m["name"] != self.EMBEDDING_MODEL
                    and not m["name"].startswith(f"{self.EMBEDDING_MODEL}:")
                ]
                if installed and requested not in installed:
                    match = next(
                        (m for m in installed if requested and requested.lower() in m.lower()),
                        None,
                    )
                    return match or installed[0]
        except Exception:
            pass
        return requested


# ---------------------------------------------------------------------------
# LocalLLMManager — thin coordinator
# ---------------------------------------------------------------------------

class LocalLLMManager:
    """
    Thin coordinator: delegates LLM calls to a provider, manages ChromaDB.

    Only concerns:
    - Provider selection / lifecycle
    - ChromaDB collection management (embeddings index)
    - Public interfaces: query(), get_embedding(), get_batch_embeddings(),
      index_documents(), query_by_raw_embedding()

    Everything else (prompt assembly, RAG orchestration, JSON parsing) lives
    in step classes and MasterActionRunner.
    """

    def __init__(self, provider: Optional[LLMProvider] = None):
        self._provider: LLMProvider = provider or OllamaProvider()
        self.chroma_client: Optional[chromadb.PersistentClient] = None
        self.collection = None
        self.audit_logger = None
        self._query_interceptors: List[Callable] = []
        self._rag_sources: dict[str, Callable] = {}
        self._document_extraction_service = None
        self._model_role_service = None
        # Optional PromptManager.get_prompt hook (set by PapyrusCore) so
        # provider-level instructions like "JSON Mode Instruction" honour
        # user edits instead of the shipped defaults.
        self.prompt_resolver: Optional[Callable[[str], str]] = None
        # MemoryManagerService (set by PapyrusCore): owns preload, unload and
        # making room for the embedding model.
        self.memory_manager = None

    @property
    def provider(self):
        return self._provider

    # -- Lifecycle -----------------------------------------------------------

    @property
    def ai_enabled(self) -> bool:
        return self._provider.ai_enabled

    # Keep the property name that callers reference on the old object
    embedding_model: str = OllamaProvider.EMBEDDING_MODEL

    def set_audit_logger(self, logger_func: Callable) -> None:
        self.audit_logger = logger_func

    def _resolve_prompt(self, key: str, fallback: str = "") -> str:
        """Resolve a PromptManager key via the injected resolver, if any."""
        if self.prompt_resolver:
            try:
                text = self.prompt_resolver(key)
                if text:
                    return text
            except Exception:
                pass
        return fallback

    def set_document_extraction_service(self, svc) -> None:
        """Inject the shared DocumentExtractionService for enriched RAG indexing."""
        self._document_extraction_service = svc

    def set_model_role_service(self, svc) -> None:
        """Inject model-role lookup for embedding metadata and future providers."""
        self._model_role_service = svc

    def model_context_limit(self, model: str) -> Optional[int]:
        resolver = getattr(self._provider, "model_context_limit", None)
        return resolver(model) if callable(resolver) else None

    def shutdown(self) -> None:
        if hasattr(self._provider, "shutdown"):
            self._provider.shutdown()

    # -- Model management ----------------------------------------------------

    def get_available_models(self) -> List[str]:
        return self._provider.available_models()

    def find_model_with_capability(self, capability: str, preferred: str = "") -> str:
        checker = getattr(self._provider, "supports_model_capability", None)
        if not checker:
            return ""
        candidates = self.get_available_models() or []
        ordered = ([preferred] if preferred else []) + [item for item in candidates if item != preferred]
        for model in ordered:
            try:
                if model and checker(model, capability):
                    return model
            except Exception:
                continue
        return ""

    def preload_model(self, model_name: str = "") -> None:
        """Warm a model unless the memory manager says it will not fit."""
        if self.memory_manager is not None:
            self.memory_manager.request_preload(model_name)
            return
        self._provider.preload_model(model_name)

    def unload_all_models(self) -> None:
        self._provider.unload_all_models()

    def check_and_pull_embedding_model(
        self, progress_callback: Optional[Callable[[str], None]] = None
    ) -> None:
        self._provider.check_and_pull_embedding_model(progress_callback)

    # Backward-compat shim kept for callers that read llm_manager.ensure_server_running()
    def ensure_server_running(self) -> None:
        pass  # Provider handles server start in its own __init__

    def release_provider(self) -> None:
        """Unload models + shut down current provider to free memory before switching."""
        try:
            if hasattr(self._provider, "unload_all_models"):
                self._provider.unload_all_models()
        except Exception:
            pass
        try:
            if hasattr(self._provider, "shutdown"):
                self._provider.shutdown()
        except Exception:
            pass

    def switch_provider(self, provider: LLMProvider) -> None:
        """Hot-swap the active LLM backend at runtime."""
        # shutdown() is idempotent — harmless if release_provider() was already called
        if hasattr(self._provider, "shutdown"):
            try:
                self._provider.shutdown()
            except Exception:
                pass
        self._provider = provider
        if self.memory_manager is not None:
            self.memory_manager.attach_llm()
        from core.events.event_bus import EventBus
        EventBus.get_instance().ai_availability_changed.emit(provider.ai_enabled)

    # -- Core LLM call -------------------------------------------------------

    def query(
        self,
        question: str,
        selected_model: str,
        *,
        allowed_docs=None,
        callback: Optional[Callable[[str], None]] = None,
        rag_enabled: bool = False,
        custom_system_prompt: str = "",
        existing_highlights=None,
        tag_filters=None,
        abort_event=None,
        json_mode: bool = False,
        temperature: float = 0.7,
        num_predict: int = 2048,
        num_ctx: int = 16384,
        stop=None,
        images=None,
        required_capabilities=None,
        **kwargs,
    ) -> str:
        """Stream a generation from the provider.

        Prompt assembly and RAG retrieval are the caller's responsibility
        (handled by LLMQueryStep / RagSearchStep).  This method is a thin
        HTTP dispatch that forwards to the provider.

        ``rag_enabled`` is accepted for backward compatibility but ignored —
        callers should use RagSearchStep for RAG retrieval.
        """
        if not selected_model:
            err = "\n[Generation Error: No AI model selected.]"
            if callback:
                callback(err)
            return err

        # Resolve the best matching installed model
        if hasattr(self._provider, "_resolve_model"):
            selected_model = self._provider._resolve_model(selected_model)  # type: ignore[attr-defined]

        required = [str(item).strip().lower() for item in (required_capabilities or []) if str(item).strip()]
        for capability in required:
            checker = getattr(self._provider, "supports_model_capability", None)
            if not checker or not checker(selected_model, capability):
                err = f"\n[Generation Error: Model '{selected_model}' does not support required capability '{capability}'.]"
                if callback:
                    callback(err)
                return err

        normalized_images = self._normalize_images(images or [])
        if normalized_images and "vision" not in required:
            checker = getattr(self._provider, "supports_model_capability", None)
            if not checker or not checker(selected_model, "vision"):
                err = f"\n[Generation Error: Model '{selected_model}' does not support image input.]"
                if callback:
                    callback(err)
                return err

        system = custom_system_prompt or ""

        if json_mode and hasattr(self._provider, "json_mode_instruction"):
            self._provider.json_mode_instruction = self._resolve_prompt(
                "JSON Mode Instruction", self._provider.json_mode_instruction
            )

        result = self._provider.generate(
            prompt=question,
            system=system,
            model=selected_model,
            stream_callback=callback,
            abort_event=abort_event,
            json_mode=json_mode,
            temperature=temperature,
            num_predict=num_predict,
            num_ctx=num_ctx,
            stop=stop,
            images=normalized_images,
        )

        if self.audit_logger and result and "[System Error" not in result:
            self.audit_logger(question, result, selected_model)

        return result

    @staticmethod
    def _normalize_images(images) -> List[dict]:
        normalized = []
        for image in images:
            item = dict(image) if isinstance(image, dict) else {"data": image}
            data = item.get("data")
            if isinstance(data, bytes):
                data = base64.b64encode(data).decode("ascii")
            if not isinstance(data, str) or not data.strip():
                raise ValueError("Image inputs require non-empty bytes or base64 data.")
            if data.startswith("data:"):
                header, _, data = data.partition(",")
                item.setdefault("mime_type", header[5:].split(";", 1)[0] or "image/png")
            normalized.append({"data": data, "mime_type": item.get("mime_type") or "image/png"})
        return normalized

    # -- Embeddings ----------------------------------------------------------

    def calculate_completion_pll(self, prefix: str, completion: str, model: str) -> float:
        """Compute the normalized pseudo-log-likelihood of *completion* given *prefix*.

        Delegates to the active provider's ``calculate_completion_pll`` implementation.
        Raises ``NotImplementedError`` if the current provider does not support it.
        """
        scorer = getattr(self._provider, "calculate_completion_pll", None)
        if not callable(scorer):
            raise NotImplementedError(
                f"Provider {type(self._provider).__name__} does not support logprob scoring."
            )
        return scorer(prefix, completion, model)

    def score_completion_logprobs(
        self, prefix: str, completion: str, model: str
    ) -> CompletionLogprobResult:
        scorer = getattr(self._provider, "score_completion_logprobs", None)
        if not callable(scorer):
            raise NotImplementedError(
                f"Provider {type(self._provider).__name__} does not support exact logprob scoring."
            )
        return scorer(prefix, completion, model)

    def get_embedding(self, text: str) -> List[float]:
        return self._provider.embed(text)

    def get_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        return self._provider.embed_batch(texts)

    # -- ChromaDB management -------------------------------------------------

    def set_project_database(self, project_filepath: str) -> None:
        import gc
        if self.chroma_client is not None:
            try:
                if hasattr(self.chroma_client, "clear_system_cache"):
                    self.chroma_client.clear_system_cache()
            except Exception:
                pass
            self.collection = None
            self.chroma_client = None
            gc.collect()

        if not project_filepath:
            self.collection = None
            return

        db_path = project_filepath + "_chroma_db"
        os.makedirs(db_path, exist_ok=True)
        self.chroma_client = chromadb.PersistentClient(
            path=db_path, settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.chroma_client.get_or_create_collection(name="pdf_workspace")

    def query_by_raw_embedding(
        self,
        embedding_vector: List[float],
        n_results: int = 5,
        allowed_docs=None,
        tag_filters=None,
        tag_logic: str = "AND",
        allowed_source_ids=None, has_citation=None, has_table=None, block_types=None,
    ):
        from core.models.intelligence_models import RetrievalPlan, SourceScope
        from core.services.intelligence.vector_filters import vector_where, filter_vector_payload
        plan = RetrievalPlan(
            query="", allowed_source_ids=list(allowed_source_ids or []),
            source_scope=SourceScope(display_names=[os.path.basename(value) for value in allowed_docs or []]),
            tag_filters=list(tag_filters or []), tag_logic=tag_logic,
            has_citation=has_citation, has_table=has_table, block_types=list(block_types or []),
        )
        results = None

        # 1. Query the main project workspace (if active)
        if self.collection:
            where_clause = vector_where(plan)

            try:
                if self.collection.count() > 0:
                    results = self.collection.query(
                        query_embeddings=[embedding_vector],
                        n_results=n_results,
                        where=where_clause,
                        include=["documents", "metadatas", "distances"],
                    )
            except Exception as exc:
                print(f"[LocalLLMManager] Centroid query failed: {exc}")

        # 2. Query registered plugin-owned RAG sources. Sources may use any
        # storage engine; they only need to return the standard Chroma-shaped
        # result payload consumed by the workflow layer.
        for source_id, source in list(self._rag_sources.items()):
            try:
                source_results = source(
                    embedding_vector=embedding_vector,
                    n_results=n_results,
                    allowed_docs=allowed_docs,
                    tag_filters=tag_filters,
                    tag_logic=tag_logic,
                )
                source_results = filter_vector_payload(source_results, plan)
                results = self._merge_rag_results(results, source_results, n_results)
            except Exception as exc:
                print(f"[LocalLLMManager] RAG source '{source_id}' failed: {exc}")

        # 3. Backward-compatible interceptor chain for older plugins.
        for interceptor in self._query_interceptors:
            try:
                # Interceptors must accept these kwargs and return a merged results dictionary
                results = interceptor(
                    embedding_vector=embedding_vector,
                    n_results=n_results,
                    allowed_docs=allowed_docs,
                    tag_filters=tag_filters,
                    current_results=results
                )
            except Exception as exc:
                print(f"[LocalLLMManager] Plugin interceptor failed: {exc}")

        return filter_vector_payload(results, plan)

    def has_rag_sources(self) -> bool:
        """Return whether core or a plugin can service a RAG retrieval."""
        if self._rag_sources or self._query_interceptors:
            return True
        if not self.collection:
            return False
        try:
            return self.collection.count() > 0
        except Exception:
            return True

    def register_rag_source(self, source_id: str, query: Callable) -> None:
        """Register a backend-only external retrieval source.

        ``query`` receives the embedding and search filters as keyword
        arguments and returns a Chroma-shaped result dictionary. Registering a
        stable id replaces the previous source, which keeps hot reload safe.
        """
        if not source_id or not callable(query):
            raise ValueError("A RAG source requires a non-empty id and callable query.")
        self._rag_sources[source_id] = query

    def unregister_rag_source(self, source_id: str) -> None:
        self._rag_sources.pop(source_id, None)

    @staticmethod
    def _merge_rag_results(first, second, max_results: int):
        """Merge two standard vector-search payloads by ascending distance."""
        if not first:
            return second
        if not second:
            return first

        combined = []
        for payload in (first, second):
            ids = (payload.get("ids") or [[]])[0]
            documents = (payload.get("documents") or [[]])[0]
            metadatas = (payload.get("metadatas") or [[]])[0]
            distances = (payload.get("distances") or [[]])[0]
            length = min(len(ids), len(documents), len(metadatas), len(distances))
            for index in range(length):
                combined.append((distances[index], ids[index], documents[index], metadatas[index]))

        combined.sort(key=lambda item: item[0])
        top = combined[:max(1, int(max_results))]
        return {
            "ids": [[item[1] for item in top]],
            "documents": [[item[2] for item in top]],
            "metadatas": [[item[3] for item in top]],
            "distances": [[item[0] for item in top]],
        }
    # -- Document indexing ---------------------------------------------------

    def index_documents(
        self,
        pdf_paths: List[str],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        if not self.collection:
            raise RuntimeError("Vector database not initialized. Please save the project first.")

        if progress_callback:
            progress_callback("Making room for the embedding model...")
        self._make_room_for_embedding()
        self.check_and_pull_embedding_model(progress_callback)

        existing_ids = set(self.collection.get(include=["metadatas"]).get("ids", []))

        chunks, metadatas, ids = [], [], []
        char_limit = 1500
        overlap = 250

        for pdf_path in pdf_paths:
            doc_name = os.path.basename(pdf_path)
            import uuid as _uuid
            source_id = f"source:{_uuid.uuid5(_uuid.NAMESPACE_URL, pdf_path or '')}"
            source_type = "pdf"
            try:
                if self._document_extraction_service is not None:
                    _path, detected_source_type = self._document_extraction_service._resolve_source(pdf_path)
                    source_type = detected_source_type or source_type
            except Exception:
                pass

            # Phase 7: prefer block-based chunking when extraction service is available
            indexed = False
            if self._document_extraction_service is not None:
                try:
                    blocks = self._document_extraction_service.get_blocks(pdf_path)
                    if blocks:
                        chunk_counter = 0
                        for block in blocks:
                            text = re.sub(r"\s+", " ", block.exact_text.replace("\n", " ").strip())
                            if len(text) < 30:
                                continue
                            for i in range(0, max(1, len(text)), max(1, char_limit - overlap)):
                                chunk_text = text[i: i + char_limit].strip()
                                if len(chunk_text) < 30:
                                    continue
                                chunk_id = f"{doc_name}_blk_{block.block_id}_c{chunk_counter}"
                                if chunk_id not in existing_ids:
                                    section = " > ".join(block.section_path) if block.section_path else ""
                                    artifact_id = f"artifact:{_uuid.uuid5(_uuid.NAMESPACE_URL, str(source_id) + '|chunk|chroma|' + chunk_id)}"
                                    chunks.append(chunk_text)
                                    metadatas.append({
                                        "artifact_id": artifact_id,
                                        "artifact_type": "chunk",
                                        "storage_ref_type": "chroma",
                                        "storage_ref_id": chunk_id,
                                        "doc_name": doc_name,
                                        "doc_id": pdf_path,
                                        "source_id": str(source_id),
                                        "source_type": source_type,
                                        "page": block.page_num,
                                        "block_id": block.block_id,
                                        "block_type": block.block_type,
                                        "section_path": section,
                                        "section_type": block.block_type,
                                        "has_citation": block.has_superscript,
                                        "model_role": "embedding_model",
                                        "model_name": self._embedding_model_name(),
                                    })
                                    ids.append(chunk_id)
                                chunk_counter += 1
                        indexed = True
                except Exception as exc:
                    print(f"[LocalLLMManager] Block-based indexing failed for {doc_name}, falling back: {exc}")

            if not indexed:
                try:
                    with fitz.open(pdf_path) as doc:
                        chunk_counter = 0
                        for page_num in range(len(doc)):
                            text = re.sub(r"\s+", " ", doc.load_page(page_num).get_text("text").replace("\n", " ").strip())
                            for i in range(0, len(text), char_limit - overlap):
                                chunk_id = f"{doc_name}_p{page_num}_c{chunk_counter}"
                                if chunk_id not in existing_ids:
                                    chunk_text = text[i: i + char_limit].strip()
                                    if len(chunk_text) > 50:
                                        artifact_id = f"artifact:{_uuid.uuid5(_uuid.NAMESPACE_URL, str(source_id) + '|chunk|chroma|' + chunk_id)}"
                                        chunks.append(chunk_text)
                                        metadatas.append({
                                            "artifact_id": artifact_id,
                                            "artifact_type": "chunk",
                                            "storage_ref_type": "chroma",
                                            "storage_ref_id": chunk_id,
                                            "doc_name": doc_name,
                                            "doc_id": pdf_path,
                                            "source_id": str(source_id),
                                            "source_type": source_type,
                                            "page": page_num,
                                            "model_role": "embedding_model",
                                            "model_name": self._embedding_model_name(),
                                        })
                                        ids.append(chunk_id)
                                chunk_counter += 1
                except Exception as exc:
                    print(f"[LocalLLMManager] Failed to index {doc_name}: {exc}")

        if not chunks:
            if progress_callback:
                progress_callback("Search index is already up to date!")
            return

        batch_size = 50
        total_batches = (len(chunks) + batch_size - 1) // batch_size
        for i in range(0, len(chunks), batch_size):
            batch_num = i // batch_size + 1
            if progress_callback:
                progress_callback(f"Embedding batch {batch_num}/{total_batches}...")
            batch_texts = chunks[i : i + batch_size]
            self.collection.upsert(
                documents=batch_texts,
                embeddings=self.get_batch_embeddings(batch_texts),
                metadatas=metadatas[i : i + batch_size],
                ids=ids[i : i + batch_size],
            )

        self._release_after_embedding(progress_callback)

    def index_video_transcript(
        self,
        video_path: str,
        source_id: str,
        segments: list,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        if not self.collection:
            raise RuntimeError("Vector database not initialized. Please save the project first.")
        if not segments:
            return
        if progress_callback:
            progress_callback(f"Indexing transcript for {os.path.basename(video_path)}...")
        if self.ai_enabled:
            self._make_room_for_embedding()
            self.check_and_pull_embedding_model(progress_callback)

        self.remove_source_from_index(source_id, fallback_path=video_path)
        doc_name = os.path.basename(video_path)
        chunks, metadatas, ids_list = [], [], []
        current, start, end, chunk_idx = [], None, None, 0
        char_limit = 1500

        def flush():
            nonlocal current, start, end, chunk_idx
            text = " ".join(current).strip()
            if len(text) > 30:
                chunks.append(text)
                chunk_id = f"{source_id}_v{chunk_idx}"
                artifact_id = f"artifact:{uuid.uuid5(uuid.NAMESPACE_URL, str(source_id) + '|transcript_segment|chroma|' + chunk_id)}"
                metadatas.append({
                    "artifact_id": artifact_id,
                    "artifact_type": "transcript_segment",
                    "storage_ref_type": "chroma",
                    "storage_ref_id": chunk_id,
                    "doc_name": doc_name,
                    "doc_id": video_path,
                    "source_id": source_id,
                    "source_type": "video",
                    "start": float(start or 0),
                    "end": float(end or start or 0),
                    "segment_index": chunk_idx,
                    "model_role": "embedding_model",
                    "model_name": self._embedding_model_name(),
                })
                ids_list.append(chunk_id)
                chunk_idx += 1
            current.clear()
            start = end = None

        for seg in segments:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            if start is None:
                start = seg.get("start", 0)
            end = seg.get("end", start)
            if sum(len(p) for p in current) + len(text) > char_limit:
                flush()
                start = seg.get("start", 0)
                end = seg.get("end", start)
            current.append(text)
        flush()

        if not chunks:
            return
        for i in range(0, len(chunks), 50):
            batch = chunks[i : i + 50]
            self.collection.upsert(
                documents=batch,
                embeddings=self.get_batch_embeddings(batch),
                metadatas=metadatas[i : i + 50],
                ids=ids_list[i : i + 50],
            )
        if progress_callback:
            progress_callback("Video transcript index updated.")

    def _make_room_for_embedding(self) -> None:
        if self.memory_manager is None:
            self.unload_all_models()
            return
        name = getattr(self._provider, "embedding_model_name", lambda: "")()
        size = getattr(self._provider, "model_size", lambda _name: 0)(name) if name else 0
        self.memory_manager.reserve(size, "embedding model", protect=[f"llm:{name}"])

    def _release_after_embedding(self, progress_callback=None) -> None:
        # With a memory manager the idle limit unloads it; reloading for every
        # batch of sources would cost more than it saves.
        if self.memory_manager is None:
            if progress_callback:
                progress_callback("Releasing embedding model from memory...")
            self.unload_all_models()

    def _embedding_model_name(self) -> str:
        svc = self._model_role_service
        if svc:
            try:
                return svc.resolve_role("embedding_model")
            except Exception:
                pass
        return getattr(self, "embedding_model", OllamaProvider.EMBEDDING_MODEL)

    def remove_document_from_index(self, pdf_path: str) -> None:
        if not self.collection:
            return
        try:
            results = self.collection.get(where={"doc_id": pdf_path})
            ids_to_delete = results.get("ids", [])
            if not ids_to_delete:
                results = self.collection.get(where={"doc_name": os.path.basename(pdf_path)})
                ids_to_delete = results.get("ids", [])
            if ids_to_delete:
                self.collection.delete(ids=ids_to_delete)
                print(f"[LocalLLMManager] Purged {len(ids_to_delete)} vectors for {os.path.basename(pdf_path)}.")
        except Exception as exc:
            print(f"[LocalLLMManager] Failed to remove document from index: {exc}")

    def rename_document_in_index(self, old_path: str, new_path: str) -> None:
        self.remove_document_from_index(old_path)

    def remove_source_from_index(self, source_id: str, fallback_path: str = "") -> None:
        if not self.collection or not source_id:
            return
        try:
            results = self.collection.get(where={"source_id": source_id})
            ids_to_delete = results.get("ids", [])
            if not ids_to_delete and fallback_path:
                results = self.collection.get(where={"doc_id": fallback_path})
                ids_to_delete = results.get("ids", [])
            if ids_to_delete:
                self.collection.delete(ids=ids_to_delete)
        except Exception as exc:
            print(f"[LocalLLMManager] Failed to remove source from index: {exc}")

    def sync_doc_tags(self, doc_id: str, current_tags: list) -> None:
        if not self.collection or not doc_id:
            return
        try:
            fetched = self.collection.get(where={"doc_id": doc_id}, include=["metadatas"])
            ids = fetched.get("ids") or []
            metadatas = fetched.get("metadatas") or []
            if not ids:
                fallback_name = os.path.basename(doc_id)
                fetched = self.collection.get(where={"doc_name": fallback_name}, include=["metadatas"])
                ids = fetched.get("ids") or []
                metadatas = fetched.get("metadatas") or []
            if not ids:
                return

            new_tag_keys = {f"tag_{str(t.get('name', '')).strip()}": True for t in (current_tags or []) if str(t.get("name", "")).strip()}
            updated: list = []
            for meta in metadatas:
                base = dict(meta or {})
                cleaned = {k: False if k.startswith("tag_") else v for k, v in base.items()}
                cleaned.update(new_tag_keys)
                updated.append(cleaned)
            self.collection.update(ids=ids, metadatas=updated)
        except Exception as exc:
            print(f"[LocalLLMManager] Failed to sync tags for '{doc_id}': {exc}")
    def register_query_interceptor(self, interceptor: Callable) -> None:
        """Allow plugins to inject their own vector DB results into RAG searches."""
        if interceptor not in self._query_interceptors:
            self._query_interceptors.append(interceptor)

    def unregister_query_interceptor(self, interceptor: Callable) -> None:
        """Remove a plugin's RAG interceptor (used during plugin unload/hot-reload)."""
        if interceptor in self._query_interceptors:
            self._query_interceptors.remove(interceptor)
