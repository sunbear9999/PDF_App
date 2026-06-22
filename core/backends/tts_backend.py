"""
core/backends/tts_backend.py

Piper TTS binary and voice management.

All functions are synchronous and intended to run from background threads.
No Qt imports.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

import requests

# ---------------------------------------------------------------------------
# Voice catalog
# ---------------------------------------------------------------------------

@dataclass
class VoiceInfo:
    id: str           # e.g. "en_US-lessac-medium"
    name: str         # display name
    language: str     # "en_US", "en_GB", etc.
    quality: str      # "low", "medium", "high"
    size_mb: float
    description: str
    recommended: bool = False

    @property
    def onnx_filename(self) -> str:
        return f"{self.id}.onnx"

    @property
    def json_filename(self) -> str:
        return f"{self.id}.onnx.json"

    @property
    def hf_repo(self) -> str:
        # Voices are stored under rhasspy/piper-voices with path lang/region/name/quality/
        parts = self.id.split("-")
        # id format: lang_REGION-name-quality
        lang_region = parts[0]           # "en_US"
        lang = lang_region.split("_")[0] # "en"
        name = "-".join(parts[1:-1])     # "lessac"
        quality = parts[-1]              # "medium"
        return f"rhasspy/piper-voices/resolve/main/{lang}/{lang_region}/{name}/{quality}"


VOICE_CATALOG: List[VoiceInfo] = [
    VoiceInfo(
        id="en_US-lessac-medium",
        name="Lessac (US, Medium)",
        language="en_US",
        quality="medium",
        size_mb=63,
        description="Natural US English voice. Fast and balanced. Default.",
        recommended=True,
    ),
    VoiceInfo(
        id="en_US-libritts-high",
        name="LibriTTS (US, High)",
        language="en_US",
        quality="high",
        size_mb=240,
        description="High quality US English voice from LibriTTS dataset.",
    ),
    VoiceInfo(
        id="en_US-ryan-high",
        name="Ryan (US, High)",
        language="en_US",
        quality="high",
        size_mb=240,
        description="Crisp, clear US male voice.",
    ),
    VoiceInfo(
        id="en_GB-alan-low",
        name="Alan (GB, Low)",
        language="en_GB",
        quality="low",
        size_mb=30,
        description="British English voice. Lightweight.",
    ),
    VoiceInfo(
        id="en_GB-jenny_dioco-medium",
        name="Jenny (GB, Medium)",
        language="en_GB",
        quality="medium",
        size_mb=63,
        description="Natural British female voice.",
    ),
    VoiceInfo(
        id="en_US-amy-medium",
        name="Amy (US, Medium)",
        language="en_US",
        quality="medium",
        size_mb=63,
        description="Friendly US female voice.",
    ),
]


# ---------------------------------------------------------------------------
# Piper binary detection
# ---------------------------------------------------------------------------

def _piper_binary_candidates(user_data_dir: str = "") -> List[str]:
    candidates = []
    exe_dir = os.path.dirname(sys.executable)
    candidates += [
        os.path.join(exe_dir, "piper.exe"),
        os.path.join(exe_dir, "piper"),
    ]
    cwd = os.getcwd()
    candidates += [
        os.path.join(cwd, ".venv", "Scripts", "piper.exe"),
        os.path.join(cwd, ".venv", "bin", "piper"),
        os.path.join(cwd, "venv", "Scripts", "piper.exe"),
        os.path.join(cwd, "venv", "bin", "piper"),
    ]
    if user_data_dir:
        candidates += [
            os.path.join(user_data_dir, "bin", "piper.exe"),
            os.path.join(user_data_dir, "bin", "piper"),
        ]
    return candidates


def get_piper_binary(user_data_dir: str = "") -> Optional[str]:
    for c in _piper_binary_candidates(user_data_dir):
        if os.path.exists(c):
            return c
    return shutil.which("piper")


def is_piper_installed(user_data_dir: str = "") -> bool:
    return get_piper_binary(user_data_dir) is not None


def get_piper_version(user_data_dir: str = "") -> Optional[str]:
    cmd = get_piper_binary(user_data_dir)
    if not cmd:
        return None
    try:
        out = subprocess.check_output([cmd, "--version"], timeout=5, stderr=subprocess.STDOUT)
        return out.decode().strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Piper installation
# ---------------------------------------------------------------------------

def _piper_release_url() -> str:
    sys_platform = platform.system()
    machine = platform.machine().lower()
    base = "https://github.com/rhasspy/piper/releases/latest/download/"
    if sys_platform == "Linux":
        if "arm" in machine or "aarch" in machine:
            return base + "piper_linux_aarch64.tar.gz"
        return base + "piper_linux_x86_64.tar.gz"
    elif sys_platform == "Darwin":
        return base + "piper_macos_x64.tar.gz"
    elif sys_platform == "Windows":
        return base + "piper_windows_amd64.zip"
    raise RuntimeError(f"Unsupported platform: {sys_platform}")


def install_piper(progress_cb: Callable[[str, int], None], user_data_dir: str = "") -> None:
    import tarfile
    import zipfile

    bin_dir = os.path.join(user_data_dir or os.path.expanduser("~/.papyrus"), "bin")
    os.makedirs(bin_dir, exist_ok=True)

    url = _piper_release_url()
    archive_path = os.path.join(bin_dir, "piper_archive")

    progress_cb("Downloading Piper TTS...", 5)
    _download_file(url, archive_path, lambda msg, pct: progress_cb(msg, 5 + int(pct * 0.8)))

    progress_cb("Extracting Piper...", 85)
    if url.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.namelist():
                fname = os.path.basename(member)
                if fname in ("piper", "piper.exe"):
                    dest = os.path.join(bin_dir, fname)
                    with zf.open(member) as src, open(dest, "wb") as out:
                        out.write(src.read())
                    if platform.system() != "Windows":
                        os.chmod(dest, 0o755)
                    break
    else:
        with tarfile.open(archive_path, "r:gz") as tf:
            for member in tf.getmembers():
                fname = os.path.basename(member.name)
                if fname in ("piper", "piper.exe"):
                    extracted = tf.extractfile(member)
                    if extracted:
                        dest = os.path.join(bin_dir, fname)
                        with open(dest, "wb") as out:
                            out.write(extracted.read())
                        if platform.system() != "Windows":
                            os.chmod(dest, 0o755)
                    break

    os.remove(archive_path)
    progress_cb("Piper TTS installed.", 100)


def uninstall_piper(progress_cb: Callable[[str, int], None], user_data_dir: str = "") -> None:
    cmd = get_piper_binary(user_data_dir)
    if cmd and os.path.exists(cmd):
        os.remove(cmd)
    progress_cb("Piper removed.", 100)


# ---------------------------------------------------------------------------
# Voice management
# ---------------------------------------------------------------------------

def _voices_dir(user_data_dir: str = "") -> str:
    base = user_data_dir or os.path.expanduser("~/.papyrus")
    return os.path.join(base, "tts")


def _all_voice_search_dirs(user_data_dir: str = "") -> List[str]:
    """Return all directories to scan for installed .onnx voice files."""
    dirs = [_voices_dir(user_data_dir)]
    # Legacy: models/ directory next to the running app (original location)
    legacy = os.path.join(os.getcwd(), "models")
    if os.path.isdir(legacy) and legacy not in dirs:
        dirs.append(legacy)
    return dirs


def list_installed_voices(user_data_dir: str = "") -> List[VoiceInfo]:
    seen: set = set()
    results: List[VoiceInfo] = []
    for directory in _all_voice_search_dirs(user_data_dir):
        if not os.path.isdir(directory):
            continue
        for fname in os.listdir(directory):
            if not fname.endswith(".onnx"):
                continue
            voice_id = fname[:-5]  # strip .onnx
            if voice_id in seen:
                continue
            seen.add(voice_id)
            size_mb = round(os.path.getsize(os.path.join(directory, fname)) / (1024 ** 2), 1)
            catalog_entry = next((v for v in VOICE_CATALOG if v.id == voice_id), None)
            if catalog_entry:
                results.append(catalog_entry)
            else:
                results.append(VoiceInfo(
                    id=voice_id,
                    name=voice_id.replace("_", " ").replace("-", " ").title(),
                    language=voice_id.split("-")[0] if "-" in voice_id else "unknown",
                    quality="",
                    size_mb=size_mb,
                    description="",
                ))
    return results


def pull_voice(voice_id: str, progress_cb: Callable[[str, int], None], user_data_dir: str = "") -> None:
    voice = next((v for v in VOICE_CATALOG if v.id == voice_id), None)
    if not voice:
        raise ValueError(f"Unknown voice: {voice_id}")

    vd = _voices_dir(user_data_dir)
    os.makedirs(vd, exist_ok=True)

    base_url = f"https://huggingface.co/{voice.hf_repo}"
    for i, fname in enumerate([voice.onnx_filename, voice.json_filename]):
        url = f"{base_url}/{fname}"
        dest = os.path.join(vd, fname)
        if not os.path.exists(dest):
            progress_cb(f"Downloading {fname}...", i * 50)
            _download_file(url, dest, lambda msg, pct: progress_cb(msg, i * 50 + pct // 2))

    progress_cb(f"{voice.name} ready.", 100)


def remove_voice(voice_id: str, user_data_dir: str = "") -> None:
    vd = _voices_dir(user_data_dir)
    for fname in [f"{voice_id}.onnx", f"{voice_id}.onnx.json"]:
        path = os.path.join(vd, fname)
        if os.path.exists(path):
            os.remove(path)


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
