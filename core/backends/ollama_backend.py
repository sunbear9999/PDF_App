"""
core/backends/ollama_backend.py

Ollama backend specification factory.

All management callables (install, pull_model, etc.) run synchronously inside
background threads — they must NOT import Qt or use Qt objects.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from typing import Callable, List, Optional

import requests

from core.registries.llm_backend_registry import BackendSpec, ModelInfo

# ---------------------------------------------------------------------------
# Catalog — curated list of downloadable models
# ---------------------------------------------------------------------------

OLLAMA_CATALOG: List[ModelInfo] = [
    # Embedding (required for RAG)
    ModelInfo(
        id="nomic-embed-text",
        name="Nomic Embed Text",
        size_gb=0.27,
        description="Required for document search and RAG. Small and fast.",
        tags=["embedding"],
        recommended=True,
        requires_vram_gb=0.3,
    ),
    # Chat — small
    ModelInfo(
        id="llama3.2:3b",
        name="Llama 3.2 3B",
        size_gb=2.0,
        description="Fast, lightweight chat model. Great for lower-end hardware.",
        tags=["chat"],
        recommended=True,
        requires_vram_gb=2.5,
    ),
    ModelInfo(
        id="qwen2.5:3b",
        name="Qwen 2.5 3B",
        size_gb=1.9,
        description="Compact multilingual model with strong instruction following.",
        tags=["chat"],
        requires_vram_gb=2.5,
    ),
    # Chat — medium
    ModelInfo(
        id="llama3.2:8b",
        name="Llama 3.2 8B",
        size_gb=4.9,
        description="Balanced quality and speed. Recommended for most users.",
        tags=["chat"],
        recommended=True,
        requires_vram_gb=6.0,
    ),
    ModelInfo(
        id="mistral:7b",
        name="Mistral 7B",
        size_gb=4.1,
        description="High-quality open model, excellent for analysis tasks.",
        tags=["chat"],
        requires_vram_gb=5.0,
    ),
    ModelInfo(
        id="qwen2.5:7b",
        name="Qwen 2.5 7B",
        size_gb=4.4,
        description="Strong multilingual reasoning and instruction following.",
        tags=["chat"],
        requires_vram_gb=5.5,
    ),
    ModelInfo(
        id="gemma4:e2b",
        name="Gemma 4 E2B",
        size_gb=1.7,
        description="Google's smallest Gemma 4 model — extremely fast, great for low-VRAM hardware.",
        tags=["chat"],
        recommended=True,
        requires_vram_gb=2.0,
    ),
    ModelInfo(
        id="gemma4:12b",
        name="Gemma 4 12B",
        size_gb=7.0,
        description="Google's mid-size Gemma 4 model with strong instruction following.",
        tags=["chat"],
        requires_vram_gb=8.0,
    ),
    ModelInfo(
        id="gemma2:9b",
        name="Gemma 2 9B",
        size_gb=5.4,
        description="Google's efficient open model with excellent benchmarks.",
        tags=["chat"],
        requires_vram_gb=7.0,
    ),
    # Chat — large
    ModelInfo(
        id="llama3.1:70b",
        name="Llama 3.1 70B",
        size_gb=40.0,
        description="Highest quality. Requires 48 GB+ VRAM or slow CPU inference.",
        tags=["chat"],
        requires_vram_gb=48.0,
    ),
    # Vision
    ModelInfo(
        id="llava:7b",
        name="LLaVA 7B",
        size_gb=4.5,
        description="Vision-language model for image understanding.",
        tags=["chat", "vision"],
        requires_vram_gb=6.0,
    ),
    ModelInfo(
        id="llava:13b",
        name="LLaVA 13B",
        size_gb=8.0,
        description="Higher quality vision-language model.",
        tags=["chat", "vision"],
        requires_vram_gb=10.0,
    ),
]

_API_BASE = "http://localhost:11434/api"

# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _ollama_cmd() -> Optional[str]:
    cmd = shutil.which("ollama")
    if cmd:
        return cmd
    win_path = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
    if os.path.exists(win_path):
        return win_path
    return None


def _is_installed() -> bool:
    return _ollama_cmd() is not None


def _get_version() -> Optional[str]:
    cmd = _ollama_cmd()
    if not cmd:
        return None
    try:
        out = subprocess.check_output([cmd, "--version"], timeout=5, stderr=subprocess.DEVNULL)
        return out.decode().strip().replace("ollama version is ", "").strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

def _install(progress_cb: Callable[[str, int], None]) -> None:
    sys_platform = platform.system()
    progress_cb("Checking system...", 5)

    if sys_platform == "Linux":
        progress_cb("Downloading Ollama installer...", 10)
        result = subprocess.run(
            ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Ollama install failed:\n{result.stderr}")

    elif sys_platform == "Darwin":
        progress_cb("Downloading Ollama for macOS...", 10)
        pkg_path = "/tmp/ollama-macos.zip"
        _download_file(
            "https://ollama.com/download/Ollama-darwin.zip",
            pkg_path,
            lambda msg, pct: progress_cb(msg, 10 + int(pct * 0.7)),
        )
        progress_cb("Installing Ollama...", 80)
        subprocess.run(["unzip", "-o", pkg_path, "-d", "/Applications/"], check=True, timeout=60)
        subprocess.run(["ln", "-sf", "/Applications/Ollama.app/Contents/Resources/ollama", "/usr/local/bin/ollama"], timeout=10)

    elif sys_platform == "Windows":
        progress_cb("Downloading Ollama installer for Windows...", 10)
        exe_path = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "OllamaSetup.exe")
        _download_file(
            "https://ollama.com/download/OllamaSetup.exe",
            exe_path,
            lambda msg, pct: progress_cb(msg, 10 + int(pct * 0.7)),
        )
        progress_cb("Running installer...", 80)
        subprocess.run([exe_path, "/S"], check=True, timeout=120)

    else:
        raise RuntimeError(f"Unsupported platform: {sys_platform}")

    # Wait for server to become available after install
    progress_cb("Starting Ollama service...", 90)
    _ensure_server_running()
    progress_cb("Ollama installed successfully.", 100)


def _ensure_server_running() -> None:
    cmd = _ollama_cmd()
    if not cmd:
        return
    for _ in range(10):
        try:
            requests.get("http://localhost:11434/", timeout=2)
            return
        except Exception:
            pass
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if platform.system() == "Windows" else {}
        try:
            subprocess.Popen(
                [cmd, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **flags,
            )
        except Exception:
            pass
        time.sleep(2)


def _uninstall(progress_cb: Callable[[str, int], None]) -> None:
    progress_cb("Uninstalling Ollama...", 10)
    sys_platform = platform.system()
    if sys_platform == "Linux":
        for path in ["/usr/local/bin/ollama", "/usr/bin/ollama"]:
            if os.path.exists(path):
                subprocess.run(["sudo", "rm", "-f", path], timeout=10)
        subprocess.run(["sudo", "rm", "-rf", "/usr/local/lib/ollama"], timeout=10)
    elif sys_platform == "Darwin":
        subprocess.run(["rm", "-rf", "/Applications/Ollama.app", "/usr/local/bin/ollama"], timeout=15)
    elif sys_platform == "Windows":
        prog = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\Uninstall Ollama.exe")
        if os.path.exists(prog):
            subprocess.run([prog, "/S"], timeout=60)
    progress_cb("Ollama removed.", 100)


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

def _list_installed_models() -> List[ModelInfo]:
    try:
        resp = requests.get(f"{_API_BASE}/tags", timeout=5)
        if resp.status_code == 200:
            results = []
            for m in resp.json().get("models", []):
                name = m["name"]
                size_bytes = m.get("size", 0)
                size_gb = round(size_bytes / (1024 ** 3), 2)
                catalog_entry = next((c for c in OLLAMA_CATALOG if c.id == name or c.id == name.split(":")[0]), None)
                tags = catalog_entry.tags if catalog_entry else ["chat"]
                results.append(ModelInfo(
                    id=name,
                    name=catalog_entry.name if catalog_entry else name,
                    size_gb=size_gb,
                    description=catalog_entry.description if catalog_entry else "",
                    tags=tags,
                ))
            return results
    except Exception:
        pass
    return []


def _pull_model(model_id: str, progress_cb: Callable[[str, int], None]) -> None:
    progress_cb(f"Starting download of {model_id}...", 0)
    _ensure_server_running()
    try:
        with requests.post(
            f"{_API_BASE}/pull",
            json={"name": model_id, "stream": True},
            stream=True,
            timeout=(15, 3600),
        ) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"Pull failed: {resp.text}")
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                status = data.get("status", "")
                total = data.get("total", 0)
                completed = data.get("completed", 0)
                pct = int((completed / total) * 100) if total > 0 else 0
                progress_cb(status or f"Downloading {model_id}...", pct)
    except Exception as exc:
        raise RuntimeError(f"Failed to download {model_id}: {exc}") from exc
    progress_cb(f"{model_id} ready.", 100)


def _remove_model(model_id: str) -> None:
    try:
        requests.delete(f"{_API_BASE}/delete", json={"name": model_id}, timeout=15)
    except Exception as exc:
        raise RuntimeError(f"Failed to remove {model_id}: {exc}") from exc


# ---------------------------------------------------------------------------
# Shared download helper
# ---------------------------------------------------------------------------

def _download_file(url: str, dest: str, progress_cb: Callable[[str, int], None]) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with requests.get(url, stream=True, timeout=(30, 3600)) as r:
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
# Factory
# ---------------------------------------------------------------------------

def make_ollama_backend_spec() -> BackendSpec:
    from core.llm_manager import OllamaProvider

    return BackendSpec(
        id="ollama",
        name="Ollama",
        description=(
            "The easiest way to run local AI models. Recommended for most users. "
            "Supports chat, vision, and embedding models."
        ),
        homepage_url="https://ollama.com",
        is_installed=_is_installed,
        get_version=_get_version,
        install=_install,
        uninstall=_uninstall,
        list_installed_models=_list_installed_models,
        catalog=OLLAMA_CATALOG,
        pull_model=_pull_model,
        remove_model=_remove_model,
        create_provider=lambda: OllamaProvider(),
        sort_order=0,
        install_notes=(
            "Ollama runs as a background service and manages models for you.\n"
            "Next: download at least one embedding model (nomic-embed-text) and one chat model below.\n"
            "If Ollama doesn't start automatically, run 'ollama serve' in a terminal."
        ),
    )
