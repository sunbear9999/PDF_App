"""
core/backends/llamacpp_backend.py

llama.cpp backend specification.

Runs llama-server in OpenAI-compatible server mode on localhost:8080.
The LlamaCppProvider implements LLMProvider by talking to that HTTP server.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import threading
import time
from typing import Callable, List, Optional

import requests

from core.registries.llm_backend_registry import BackendSpec, ModelInfo

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

LLAMACPP_CATALOG: List[ModelInfo] = [
    ModelInfo(
        id="bartowski/Llama-3.2-3B-Instruct-GGUF/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        name="Llama 3.2 3B (Q4)",
        size_gb=2.0,
        description="Fast, lightweight. Great for lower-end hardware.",
        tags=["chat"],
        recommended=True,
        quantization="Q4_K_M",
        requires_vram_gb=2.5,
    ),
    ModelInfo(
        id="bartowski/Mistral-7B-Instruct-v0.3-GGUF/Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
        name="Mistral 7B Instruct v0.3 (Q4)",
        size_gb=4.1,
        description="High quality open model, excellent for analysis.",
        tags=["chat"],
        quantization="Q4_K_M",
        requires_vram_gb=5.0,
    ),
    ModelInfo(
        id="Qwen/Qwen2.5-7B-Instruct-GGUF/qwen2.5-7b-instruct-q4_k_m.gguf",
        name="Qwen 2.5 7B Instruct (Q4)",
        size_gb=4.4,
        description="Strong multilingual reasoning and instruction following.",
        tags=["chat"],
        quantization="Q4_K_M",
        requires_vram_gb=5.5,
    ),
    ModelInfo(
        id="bartowski/gemma-2-9b-it-GGUF/gemma-2-9b-it-Q4_K_M.gguf",
        name="Gemma 2 9B IT (Q4)",
        size_gb=5.4,
        description="Google's efficient model with strong benchmarks.",
        tags=["chat"],
        quantization="Q4_K_M",
        requires_vram_gb=7.0,
    ),
    ModelInfo(
        id="nomic-ai/nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.Q4_K_M.gguf",
        name="Nomic Embed Text v1.5 (Q4)",
        size_gb=0.08,
        description="Required for document search and RAG.",
        tags=["embedding"],
        recommended=True,
        requires_vram_gb=0.1,
    ),
]

_LLAMA_PORT = 8080
_LLAMA_API_BASE = f"http://localhost:{_LLAMA_PORT}"

# ---------------------------------------------------------------------------
# Binary management helpers
# ---------------------------------------------------------------------------

def _binary_dir(user_data_dir: str = "") -> str:
    base = user_data_dir or os.path.expanduser("~/.papyrus")
    return os.path.join(base, "bin")


def _install_dir(user_data_dir: str = "") -> str:
    """Directory where the full llama.cpp archive is extracted (includes .so files)."""
    return os.path.join(_binary_dir(user_data_dir), "llamacpp")


def _server_binary(user_data_dir: str = "") -> Optional[str]:
    """Return the absolute path of llama-server, or None if not installed."""
    suffix = ".exe" if platform.system() == "Windows" else ""
    # Primary: inside the extracted archive directory
    primary = os.path.join(_install_dir(user_data_dir), "llama-server" + suffix)
    if os.path.exists(primary):
        return primary
    # Legacy single-file install (older version of this code)
    legacy = os.path.join(_binary_dir(user_data_dir), "llama-server" + suffix)
    if os.path.exists(legacy):
        return legacy
    return shutil.which("llama-server")


def _is_installed(user_data_dir: str = "") -> bool:
    return _server_binary(user_data_dir) is not None


def _get_version(user_data_dir: str = "") -> Optional[str]:
    cmd = _server_binary(user_data_dir)
    if not cmd:
        return None
    try:
        out = subprocess.check_output([cmd, "--version"], timeout=5, stderr=subprocess.STDOUT)
        return out.decode().strip().splitlines()[0]
    except Exception:
        return None


def _get_release_url() -> str:
    """
    Query GitHub API for the latest llama.cpp release and return the URL of the
    plain CPU build for this platform.  Asset names are versioned at release time
    (e.g. llama-b9761-bin-ubuntu-x64.tar.gz) so we resolve them at install time.
    """
    sys_platform = platform.system()
    machine = platform.machine().lower()
    is_arm = "arm" in machine or "aarch" in machine

    # Keywords that must ALL be absent (we want the plain CPU build, not GPU variants)
    _GPU_TAGS = ("rocm", "vulkan", "cuda", "openvino", "sycl", "hip")

    if sys_platform == "Linux":
        arch = "arm64" if is_arm else "x64"
        # Linux builds are .tar.gz
        must_contain = [f"bin-ubuntu-{arch}", ".tar.gz"]
    elif sys_platform == "Darwin":
        arch = "arm64" if is_arm else "x64"
        must_contain = [f"bin-macos-{arch}", ".tar.gz"]
    elif sys_platform == "Windows":
        # Windows CPU build
        must_contain = ["bin-win-cpu-x64", ".zip"]
    else:
        raise RuntimeError(f"Unsupported platform: {sys_platform}")

    try:
        api_resp = requests.get(
            "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest",
            timeout=15,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "papyrus-app"},
        )
        api_resp.raise_for_status()
        assets = api_resp.json().get("assets", [])
        for asset in assets:
            name: str = asset.get("name", "").lower()
            if all(kw in name for kw in must_contain) and not any(t in name for t in _GPU_TAGS):
                return asset["browser_download_url"]
    except Exception as exc:
        raise RuntimeError(f"Could not fetch llama.cpp release info from GitHub: {exc}") from exc

    raise RuntimeError(
        f"No plain-CPU llama.cpp asset found for platform '{sys_platform}/{arch}'. "
        "Check https://github.com/ggerganov/llama.cpp/releases for available builds."
    )


def _install(progress_cb: Callable[[str, int], None], user_data_dir: str = "") -> None:
    """
    Download the latest llama.cpp CPU release and extract it to
    ~/.papyrus/bin/llamacpp/.  The archive includes shared libraries that must
    live alongside the binary, so we extract everything (not just llama-server).
    """
    import tarfile
    import zipfile

    dest_dir = _install_dir(user_data_dir)
    os.makedirs(dest_dir, exist_ok=True)

    progress_cb("Fetching latest llama.cpp release info...", 2)
    url = _get_release_url()
    is_tar = url.endswith(".tar.gz")
    archive_path = os.path.join(dest_dir, "llama_release.tar.gz" if is_tar else "llama_release.zip")

    progress_cb("Downloading llama.cpp...", 5)
    _download_file(url, archive_path, lambda msg, pct: progress_cb(msg, 5 + int(pct * 0.80)))

    progress_cb("Extracting...", 86)
    if is_tar:
        with tarfile.open(archive_path, "r:gz") as tf:
            for member in tf.getmembers():
                # Strip the top-level versioned directory (e.g. "llama-b9761/")
                parts = member.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                member.name = parts[1]
                # Avoid absolute paths / path traversal
                if member.name.startswith(".."):
                    continue
                tf.extract(member, dest_dir)
    else:
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                parts = info.filename.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                info.filename = parts[1]
                if info.filename.startswith(".."):
                    continue
                zf.extract(info, dest_dir)

    os.remove(archive_path)

    # Make the server binary executable on POSIX
    server = os.path.join(dest_dir, "llama-server")
    if platform.system() != "Windows" and os.path.exists(server):
        os.chmod(server, 0o755)

    if not _is_installed(user_data_dir):
        raise RuntimeError(
            "Extraction completed but llama-server binary not found. "
            "The release archive may have changed structure."
        )

    progress_cb("llama.cpp installed successfully.", 100)


def _uninstall(progress_cb: Callable[[str, int], None], user_data_dir: str = "") -> None:
    import shutil as _shutil
    dest = _install_dir(user_data_dir)
    if os.path.isdir(dest):
        _shutil.rmtree(dest)
    # Also remove legacy single-file install
    legacy = os.path.join(_binary_dir(user_data_dir), "llama-server")
    if platform.system() == "Windows":
        legacy += ".exe"
    if os.path.exists(legacy):
        os.remove(legacy)
    progress_cb("llama.cpp removed.", 100)


# ---------------------------------------------------------------------------
# Model directory
# ---------------------------------------------------------------------------

def _model_dir(user_data_dir: str = "") -> str:
    base = user_data_dir or os.path.expanduser("~/.papyrus")
    return os.path.join(base, "models", "gguf")


def _list_installed_models(user_data_dir: str = "") -> List[ModelInfo]:
    md = _model_dir(user_data_dir)
    if not os.path.isdir(md):
        return []
    results = []
    for fname in os.listdir(md):
        if not fname.endswith(".gguf"):
            continue
        fpath = os.path.join(md, fname)
        size_gb = round(os.path.getsize(fpath) / (1024 ** 3), 2)
        catalog_entry = next((c for c in LLAMACPP_CATALOG if os.path.basename(c.id) == fname), None)
        tags = catalog_entry.tags if catalog_entry else ["chat"]
        results.append(ModelInfo(
            id=fname,
            name=catalog_entry.name if catalog_entry else fname,
            size_gb=size_gb,
            description=catalog_entry.description if catalog_entry else "",
            tags=tags,
        ))
    return results


def _pull_model(model_id: str, progress_cb: Callable[[str, int], None], user_data_dir: str = "") -> None:
    """model_id is repo/filename.gguf from HuggingFace — e.g. 'bartowski/Llama-3.2-3B-Instruct-GGUF/Llama-3.2-3B-Instruct-Q4_K_M.gguf'."""
    parts = model_id.split("/")
    if len(parts) < 3:
        raise ValueError(f"Expected 'owner/repo/filename.gguf', got: {model_id}")

    owner = parts[0]
    repo = parts[1]
    filename = "/".join(parts[2:])
    url = f"https://huggingface.co/{owner}/{repo}/resolve/main/{filename}"

    os.makedirs(_model_dir(user_data_dir), exist_ok=True)
    dest = os.path.join(_model_dir(user_data_dir), os.path.basename(filename))
    progress_cb(f"Downloading {os.path.basename(filename)}...", 0)
    _download_file(url, dest, progress_cb)
    progress_cb(f"{os.path.basename(filename)} ready.", 100)


def _remove_model(model_id: str, user_data_dir: str = "") -> None:
    path = os.path.join(_model_dir(user_data_dir), os.path.basename(model_id))
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

def _download_file(url: str, dest: str, progress_cb: Callable[[str, int], None]) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with requests.get(url, stream=True, timeout=(30, 7200)) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                pct = int((downloaded / total) * 100) if total else 0
                progress_cb(f"Downloading... {downloaded // (1024*1024)} MB", pct)


# ---------------------------------------------------------------------------
# LlamaCppProvider — implements LLMProvider via llama-server HTTP API
# ---------------------------------------------------------------------------

class LlamaCppProvider:
    """
    Manages a llama-server subprocess and proxies LLM calls to its
    OpenAI-compatible HTTP API.
    """

    EMBEDDING_MODEL = "nomic-embed-text"

    def __init__(self, model_dir: str = "", port: int = _LLAMA_PORT, user_data_dir: str = ""):
        self._user_data_dir = user_data_dir
        self._model_dir = model_dir or _model_dir(user_data_dir)
        self._port = port
        self._base_url = f"http://localhost:{port}"
        self._server_proc: Optional[subprocess.Popen] = None
        self._current_model: Optional[str] = None
        self._ai_enabled = False
        self._ready_event = threading.Event()
        # Start server in background thread — constructor returns immediately
        t = threading.Thread(target=self._try_start_with_any_model, daemon=True, name="llamacpp-startup")
        t.start()
        self._startup_thread = t

    def wait_for_ready(self, timeout: float = 120.0) -> bool:
        """Block until the server is healthy or timeout expires. Returns True if ready."""
        return self._ready_event.wait(timeout=timeout)

    @property
    def ai_enabled(self) -> bool:
        return self._ai_enabled and self._ping()

    def _ping(self) -> bool:
        try:
            requests.get(f"{self._base_url}/health", timeout=2)
            return True
        except Exception:
            return False

    def _try_start_with_any_model(self) -> None:
        models = self._find_gguf_files()
        if not models:
            self._ready_event.set()
            return
        # Prefer chat models over embedding models for the default
        chat_models = [m for m in models if "embed" not in m.lower()]
        model_path = chat_models[0] if chat_models else models[0]
        self._start_server_blocking(model_path)

    def _find_gguf_files(self) -> List[str]:
        if not os.path.isdir(self._model_dir):
            return []
        return sorted(
            os.path.join(self._model_dir, f)
            for f in os.listdir(self._model_dir)
            if f.endswith(".gguf")
        )

    def _start_server_blocking(self, model_path: str) -> None:
        """Start llama-server for the given model and wait until healthy (up to 120s)."""
        if self._server_proc and self._server_proc.poll() is None:
            self._server_proc.terminate()
            try:
                self._server_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._server_proc.kill()

        cmd = _server_binary(self._user_data_dir)
        if not cmd:
            self._ready_event.set()
            return

        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if platform.system() == "Windows" else {}
        try:
            self._server_proc = subprocess.Popen(
                [cmd, "--model", model_path, "--port", str(self._port), "--ctx-size", "8192"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **flags,
            )
            self._current_model = os.path.basename(model_path)
            # Poll until healthy — max 120s
            for _ in range(120):
                time.sleep(1)
                if self._ping():
                    self._ai_enabled = True
                    break
        except Exception as exc:
            print(f"[LlamaCppProvider] Failed to start server: {exc}")
        finally:
            self._ready_event.set()

    # Alias so external callers can use the non-blocking form
    _start_server = _start_server_blocking

    def generate(
        self,
        prompt: str,
        system: str,
        model: str,
        *,
        stream_callback=None,
        abort_event=None,
        json_mode: bool = False,
        temperature: float = 0.7,
        num_predict: int = 2048,
        num_ctx: int = 8192,
        stop=None,
        images=None,
        **kwargs,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if images:
            content = [{"type": "text", "text": prompt}]
            for image in images:
                mime = image.get("mime_type") or "image/png"
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{image['data']}"},
                })
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        if json_mode:
            # Instruct the model via the system prompt rather than grammar-constrained generation.
            # Grammar-constrained response_format causes extreme slowness on many llama-server builds.
            system = (system + "\n\n" if system else "") + "Respond with valid JSON only. No prose."

        payload: dict = {
            "model": self._current_model or "default",
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": num_predict,
        }
        if stop:
            payload["stop"] = stop

        full_response = ""
        try:
            with requests.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                stream=True,
                timeout=(15, 600),
            ) as resp:
                if resp.status_code != 200:
                    raise ConnectionError(f"llama-server error ({resp.status_code}): {resp.text}")
                # chunk_size=1 prevents buffering — each SSE line is yielded immediately
                for line in resp.iter_lines(chunk_size=1):
                    if abort_event and abort_event.is_set():
                        aborted = "\n[Process Aborted by User]"
                        if stream_callback:
                            stream_callback(aborted)
                        full_response += aborted
                        break
                    if line and line.startswith(b"data: "):
                        data = line[6:]
                        if data == b"[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            txt = chunk["choices"][0]["delta"].get("content", "")
                            if txt:
                                full_response += txt
                                if stream_callback:
                                    stream_callback(txt)
                        except (json.JSONDecodeError, KeyError):
                            pass
        except Exception as exc:
            err = f"\n[Generation Error: {exc}]"
            if stream_callback:
                stream_callback(err)
            full_response += err
        return full_response

    def supports_model_capability(self, model: str, capability: str) -> bool:
        # This backend does not currently configure llama-server's required
        # multimodal projector, so advertising vision would be unsafe.
        return capability != "vision"

    def embed(self, text: str) -> List[float]:
        try:
            resp = requests.post(
                f"{self._base_url}/v1/embeddings",
                json={"model": "default", "input": text},
                timeout=(10, 120),
            )
            if resp.status_code == 200:
                return resp.json()["data"][0]["embedding"]
        except Exception as exc:
            raise RuntimeError(f"Embedding failed: {exc}") from exc
        raise RuntimeError("Embedding returned no data")

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

    def available_models(self) -> List[str]:
        return [os.path.basename(f) for f in self._find_gguf_files()]

    def preload_model(self, model_name: str) -> None:
        candidates = self._find_gguf_files()
        match = next((c for c in candidates if model_name in os.path.basename(c)), None)
        if match and os.path.basename(match) != self._current_model:
            # Restart in background — caller must not rely on synchronous completion
            self._ready_event.clear()
            self._ai_enabled = False
            t = threading.Thread(
                target=self._start_server_blocking, args=(match,), daemon=True, name="llamacpp-reload"
            )
            t.start()

    def unload_all_models(self) -> None:
        pass  # llama-server keeps one model loaded; no-op

    def check_and_pull_embedding_model(self, progress_callback=None) -> None:
        pass  # Embedding handled by the model in use

    def shutdown(self) -> None:
        if self._server_proc and self._server_proc.poll() is None:
            self._server_proc.terminate()
            try:
                self._server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._server_proc.kill()
            self._server_proc = None
        self._ai_enabled = False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_llamacpp_backend_spec(user_data_dir: str = "") -> BackendSpec:
    def _is() -> bool:
        return _is_installed(user_data_dir)

    def _ver() -> Optional[str]:
        return _get_version(user_data_dir)

    def _inst(cb: Callable) -> None:
        _install(cb, user_data_dir)

    def _uninst(cb: Callable) -> None:
        _uninstall(cb, user_data_dir)

    def _list() -> List[ModelInfo]:
        return _list_installed_models(user_data_dir)

    def _pull(model_id: str, cb: Callable) -> None:
        _pull_model(model_id, cb, user_data_dir)

    def _remove(model_id: str) -> None:
        _remove_model(model_id, user_data_dir)

    return BackendSpec(
        id="llamacpp",
        name="llama.cpp",
        description=(
            "Runs models locally via llama-server (OpenAI-compatible API). "
            "Good for power users who want direct model control without Ollama."
        ),
        homepage_url="https://github.com/ggerganov/llama.cpp",
        is_installed=_is,
        get_version=_ver,
        install=_inst,
        uninstall=_uninst,
        list_installed_models=_list,
        catalog=LLAMACPP_CATALOG,
        pull_model=_pull,
        remove_model=_remove,
        create_provider=lambda: LlamaCppProvider(user_data_dir=user_data_dir),
        sort_order=1,
        install_notes=(
            "llama-server is installed to ~/.papyrus/bin/llamacpp/ — no PATH changes needed.\n"
            "Next: download at least one embedding model and one chat model below.\n"
            "GPU note: this installs the CPU build. For NVIDIA GPU acceleration, "
            "install CUDA and download the CUDA build from the llama.cpp releases page. "
            "For AMD GPU, install ROCm and use the ROCm build."
        ),
    )
