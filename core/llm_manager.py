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
import os
import re
import shutil
import subprocess
import time
import platform
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
        **kwargs,
    ) -> str: ...

    def embed(self, text: str) -> List[float]: ...

    def embed_batch(self, texts: List[str]) -> List[List[float]]: ...

    def available_models(self) -> List[str]: ...

    def preload_model(self, model_name: str) -> None: ...

    def unload_all_models(self) -> None: ...

    def check_and_pull_embedding_model(
        self, progress_callback: Optional[Callable[[str], None]] = None
    ) -> None: ...


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------

class OllamaProvider:
    """Talks to a local Ollama HTTP server."""

    EMBEDDING_MODEL = "nomic-embed-text"

    def __init__(self, api_base: str = "http://localhost:11434/api"):
        self.api_base = api_base
        self._ollama_proc: Optional[subprocess.Popen] = None
        self._ai_enabled = False
        self._start_server()

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
        **kwargs,
    ) -> str:
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": True,
            "keep_alive": "60m",
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }
        if stop:
            payload["options"]["stop"] = stop
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
            if stream_callback:
                stream_callback(err)
            full_response += err

        return full_response

    def embed(self, text: str) -> List[float]:
        payload = {
            "model": self.EMBEDDING_MODEL,
            "prompt": text,
            "keep_alive": "60m",
            "options": {"num_ctx": 8192},
        }
        try:
            response = requests.post(
                f"{self.api_base}/embeddings", json=payload, timeout=(10, 300)
            )
            if response.status_code == 200:
                return response.json().get("embedding")
            raise RuntimeError(f"Server returned {response.status_code}: {response.text}")
        except Exception as exc:
            raise RuntimeError(f"Embedding failed: {exc}") from exc

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        payload = {
            "model": self.EMBEDDING_MODEL,
            "input": texts,
            "keep_alive": "60m",
            "options": {"num_ctx": 8192},
        }
        try:
            response = requests.post(
                f"{self.api_base}/embed", json=payload, timeout=(15, 600)
            )
            if response.status_code == 200:
                return response.json().get("embeddings")
        except Exception as exc:
            print(f"[OllamaProvider] Batch embed failed, falling back to sequential: {exc}")
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
        return ["qwen2.5:7b", "llama3", "mistral"]

    def preload_model(self, model_name: str) -> None:
        if not model_name:
            return
        try:
            response = requests.post(
                f"{self.api_base}/generate",
                json={"model": model_name, "keep_alive": "60m"},
                timeout=(15, 600),
            )
            if response.status_code == 200:
                print(f"[OllamaProvider] Preloaded {model_name}.")
            else:
                print(f"[OllamaProvider] Failed to preload model: {response.status_code}")
        except Exception as exc:
            print(f"[OllamaProvider] Failed to preload model: {exc}")

    def unload_all_models(self) -> None:
        try:
            resp = requests.get(f"{self.api_base}/ps", timeout=3)
            if resp.status_code == 200:
                for m in resp.json().get("models", []):
                    name = m.get("name")
                    requests.post(
                        f"{self.api_base}/generate",
                        json={"model": name, "keep_alive": 0},
                        timeout=3,
                    )
        except Exception as exc:
            print(f"[OllamaProvider] Warning during model unload: {exc}")

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

    # -- Lifecycle -----------------------------------------------------------

    @property
    def ai_enabled(self) -> bool:
        return self._provider.ai_enabled

    # Keep the property name that callers reference on the old object
    embedding_model: str = OllamaProvider.EMBEDDING_MODEL

    def set_audit_logger(self, logger_func: Callable) -> None:
        self.audit_logger = logger_func

    def shutdown(self) -> None:
        if hasattr(self._provider, "shutdown"):
            self._provider.shutdown()

    # -- Model management ----------------------------------------------------

    def get_available_models(self) -> List[str]:
        return self._provider.available_models()

    def preload_model(self, model_name: str = "") -> None:
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

        system = custom_system_prompt or ""

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
        )

        if self.audit_logger and result and "[System Error" not in result:
            self.audit_logger(question, result, selected_model)

        return result

    # -- Embeddings ----------------------------------------------------------

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
    ):
        results = None

        # 1. Query the main project workspace (if active)
        if self.collection and self.collection.count() > 0:
            where_clause: dict = {}
            tag_filters_list = [str(t).strip() for t in (tag_filters or []) if str(t).strip()]

            if allowed_docs:
                base_names = [os.path.basename(d) for d in allowed_docs]
                where_clause["doc_name"] = base_names[0] if len(base_names) == 1 else {"$in": base_names}

            for t in tag_filters_list:
                where_clause[f"tag_{t}"] = True

            try:
                results = self.collection.query(
                    query_embeddings=[embedding_vector],
                    n_results=n_results,
                    where=where_clause or None,
                )
            except Exception as exc:
                print(f"[LocalLLMManager] Centroid query failed: {exc}")

        # 2. Pass through plugin interceptors to allow external DB injection
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

        return results
    # -- Document indexing ---------------------------------------------------

    def index_documents(
        self,
        pdf_paths: List[str],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        if not self.collection:
            raise RuntimeError("Vector database not initialized. Please save the project first.")

        if progress_callback:
            progress_callback("Clearing VRAM to prioritize embedding engine...")
        self.unload_all_models()
        self.check_and_pull_embedding_model(progress_callback)

        existing_ids = set(self.collection.get(include=["metadatas"]).get("ids", []))

        chunks, metadatas, ids = [], [], []
        char_limit = 1500
        overlap = 250

        for pdf_path in pdf_paths:
            doc_name = os.path.basename(pdf_path)
            try:
                with fitz.open(pdf_path) as doc:
                    chunk_counter = 0
                    for page_num in range(len(doc)):
                        text = re.sub(r"\s+", " ", doc.load_page(page_num).get_text("text").replace("\n", " ").strip())
                        for i in range(0, len(text), char_limit - overlap):
                            chunk_id = f"{doc_name}_p{page_num}_c{chunk_counter}"
                            if chunk_id not in existing_ids:
                                chunk_text = text[i : i + char_limit].strip()
                                if len(chunk_text) > 50:
                                    import uuid as _uuid
                                    source_id = f"source:{_uuid.uuid5(_uuid.NAMESPACE_URL, pdf_path or '')}"
                                    chunks.append(chunk_text)
                                    metadatas.append({"doc_name": doc_name, "doc_id": pdf_path, "source_id": source_id, "source_type": "pdf", "page": page_num})
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

        if progress_callback:
            progress_callback("Releasing embedding model from memory...")
        self.unload_all_models()

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
            self.unload_all_models()
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
                metadatas.append({"doc_name": doc_name, "doc_id": video_path, "source_id": source_id, "source_type": "video", "start": float(start or 0), "end": float(end or start or 0), "segment_index": chunk_idx})
                ids_list.append(f"{source_id}_v{chunk_idx}")
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