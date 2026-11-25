"""Tesseract OCR engine: is it installed, and how to install it from the app.

Tesseract is a system program, not a Python package, so installation goes
through the platform package manager. On Linux the command runs through
``pkexec`` so the desktop shows its own administrator password prompt; the
app never sees or stores the password.
"""
from __future__ import annotations

import os
import platform
import shutil
from typing import Dict, List, Optional

_WINDOWS_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)
# (package manager binary, install arguments) in preference order.
_LINUX_MANAGERS = (
    ("apt-get", ["install", "-y", "tesseract-ocr", "tesseract-ocr-eng"]),
    ("dnf", ["install", "-y", "tesseract", "tesseract-langpack-eng"]),
    ("pacman", ["-S", "--noconfirm", "--needed", "tesseract", "tesseract-data-eng"]),
    ("zypper", ["--non-interactive", "install", "tesseract-ocr", "tesseract-ocr-traineddata-english"]),
)


def tesseract_path() -> str:
    found = shutil.which("tesseract")
    if found:
        return found
    if platform.system() == "Windows":
        return next((path for path in _WINDOWS_PATHS if os.path.exists(path)), "")
    if platform.system() == "Darwin":
        return next((path for path in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract")
                     if os.path.exists(path)), "")
    return ""


def ocr_engine_status() -> Dict[str, object]:
    path = tesseract_path()
    if path:
        return {"ready": True, "path": path, "error": ""}
    return {
        "ready": False, "path": "",
        "error": "The Tesseract OCR engine is not installed. " + manual_install_hint(),
    }


def install_command() -> Optional[List[str]]:
    """The command that installs Tesseract here, or None if unknown."""
    system = platform.system()
    if system == "Linux":
        elevate = shutil.which("pkexec")
        for manager, args in _LINUX_MANAGERS:
            binary = shutil.which(manager)
            if binary and elevate:
                return [elevate, binary, *args]
        return None
    if system == "Darwin":
        brew = shutil.which("brew") or next(
            (path for path in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.exists(path)), "")
        return [brew, "install", "tesseract"] if brew else None
    if system == "Windows":
        winget = shutil.which("winget")
        return [winget, "install", "--id", "UB-Mannheim.TesseractOCR", "-e",
                "--accept-source-agreements", "--accept-package-agreements"] if winget else None
    return None


def manual_install_hint() -> str:
    system = platform.system()
    if system == "Linux":
        for manager, args in _LINUX_MANAGERS:
            if shutil.which(manager):
                return f"Install it from Settings → AI Setup, or run: sudo {manager} {' '.join(args)}"
        return "Install the 'tesseract' package with your distribution's package manager."
    if system == "Darwin":
        return "Install it from Settings → AI Setup, or run: brew install tesseract"
    if system == "Windows":
        return ("Install it from Settings → AI Setup, or download the installer from "
                "https://github.com/UB-Mannheim/tesseract/wiki")
    return "Install Tesseract OCR for your operating system."
