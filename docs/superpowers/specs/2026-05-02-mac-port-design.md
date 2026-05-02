# Mac (Apple Silicon) Port Design — Papyrus Research / PDF_App

**Date:** 2026-05-02
**Source:** `https://github.com/sunbear9999/PDF_App.git` (cloned to `~/Desktop/PDF_App_Mac/`)

## Goal

Make `python main.py` runnable on an Apple Silicon (ARM) Mac after a single `python installer.py` pass. Preserve all existing Windows and Linux functionality.

## Scope

**In scope:**
- New `requirements.txt`
- Mac (`Darwin`) branches in `installer.py` rewritten to actually install dependencies (currently they're stubs that mostly open a browser)
- One small fix in app code (`main.py` — guard the dictionaries env var)
- Mac spellcheck dictionary download

**Note:** The original spec called for a second app-code edit in `core/llm_manager.py` to guard `subprocess.CREATE_NO_WINDOW`. Verified during planning: the existing code at `llm_manager.py:94-106` already splits Windows / non-Windows into two `subprocess.Popen` calls, and `CREATE_NO_WINDOW` is only referenced inside the Windows branch. Confirmed by grep — no unguarded Windows constants exist in the app code. That edit is removed from scope.

**Out of scope (do not touch):**
- `installer_win.py`, `installer_win.spec` — Windows-only build artifacts
- `main.patched.spec` — Linux PyArmor build artifact
- `main.spec` — PyInstaller spec for `.app` distribution; not needed for run-from-source
- Any properly-guarded Windows code path (e.g., `installer.py`'s admin elevation, `core/ocr_engine.py`'s `C:\Program Files\Tesseract-OCR\tesseract.exe` path, `core/prompt_manager.py:149-151`'s `LOCALAPPDATA` branch)
- General refactoring or cleanup unrelated to Mac compatibility

## Mac installer flow (`installer.py`, Darwin branches only)

The installer is a Tkinter GUI with sequential phases. The Windows branch already auto-installs everything; the existing Mac branch mostly just opens a browser. The new Mac branch will mirror Windows behavior using a hybrid `brew`-or-direct-download strategy.

### Phase 0: Homebrew bootstrap (NEW, Mac only)

Tesseract has no official macOS `.pkg` distribution; brew is the only realistic auto-install path. So if `brew` is missing, install it first.

1. If `brew` is on `PATH` → skip.
2. Else log a clear message ("Homebrew is required to auto-install Tesseract; this will prompt for your password") and run the official one-liner:
   ```
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
   The Homebrew installer itself uses `sudo` and prompts via the terminal. Stream output into the installer log box.
3. After install, add `/opt/homebrew/bin` to `PATH` for the rest of the installer process so `brew` is callable.
4. If install fails → log "Install Homebrew manually from https://brew.sh, then re-run this installer" and continue. Tesseract phase will then fall through to instruction-only.

### Phase 1: Tesseract OCR

1. If `tesseract` is on `PATH` → already installed, skip.
2. Else if `brew` is on `PATH` (post-Phase-0) → run `brew install tesseract`.
3. Else log "Run `brew install tesseract` manually" and continue.

### Phase 2: Ollama

1. If `ollama` resolves via `get_ollama_cmd()` → already installed, skip.
2. Else if `brew` is on `PATH` → run `brew install ollama`.
3. Else download `https://ollama.com/download/Ollama-darwin.zip` into a temp dir, unzip, move `Ollama.app` to `/Applications/`. Then `open -a Ollama` to launch it (this starts the background daemon).
4. After install, poll `http://127.0.0.1:11434` for up to 15 seconds. Log success/failure.

### Phase 3: Pip dependencies (NEW phase, all platforms)

After Tesseract and Ollama, run:

```python
subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
```

Stream the output into the installer log box. On failure, log clearly and continue (the user can retry by hand). This phase runs on all platforms, not just Mac — it's a strict improvement over the current installer, which assumes deps are pre-installed.

### Phase 4: Models + voices (no change)

The existing logic (`ollama pull` for the chat model and embedding model; HuggingFace HTTPS for Piper voices) is already cross-platform. No edits.

### Phase 5: Spellcheck dictionary (NEW)

Download `en-US-10-1.bdic` from the Chromium source mirror at:

```
https://chromium.googlesource.com/chromium/deps/hunspell_dictionaries/+/main/en-US-10-1.bdic?format=TEXT
```

(Note: googlesource serves blobs base64-encoded with `?format=TEXT` — the installer must base64-decode the response before writing.)

Save to `<project_dir>/qtwebengine_dictionaries/en-US-10-1.bdic`. Skip if any `.bdic` file already exists in that folder. ~200KB. Failure here is non-fatal — the app guards for missing dict in `main.py`.

If the primary URL fails, fall back to: `https://redirector.gvt1.com/edgedl/chrome/dict/en-us-10-1.bdic` (Google's CDN, raw bytes, no base64). If both fail, log and continue.

### Phase 6: Desktop shortcut

Existing Mac branch creates a symlink on the Desktop pointing to a built `main` binary. Change: only create the symlink if `Papyrus Research.app` or `main` actually exists in `project_dir`. For run-from-source there's nothing to link to, so skip silently.

## Source-tree changes

### `requirements.txt` (NEW)

Generated by AST-parsing all imports across `core/`, `gui/`, `main.py`, `tests/`, mapping to PyPI package names, then cross-referenced with the `hiddenimports` already declared in `main.spec`. Lower-bound pins only.

Expected packages (final list derived during implementation):
- `PySide6>=6.7` (includes QtWebEngine on Mac via `PySide6-Addons` — verify on first install)
- `PyMuPDF>=1.24`
- `pytesseract`
- `Pillow`
- `chromadb`
- `piper-tts`
- `requests`
- `numpy`
- Anything else surfaced by AST scan (e.g., `pydantic`, `sentence-transformers`, etc., if used)

### `main.py` — guard QTWEBENGINE_DICTIONARIES_PATH

Current (lines ~13-15):
```python
dict_path = os.path.join(root_dir, "qtwebengine_dictionaries")
os.environ["QTWEBENGINE_DICTIONARIES_PATH"] = dict_path
```

Change to:
```python
dict_path = os.path.join(root_dir, "qtwebengine_dictionaries")
if os.path.isdir(dict_path):
    os.environ["QTWEBENGINE_DICTIONARIES_PATH"] = dict_path
```

When the folder is absent, Qt's WebEngine spellcheck silently disables itself. App runs fine.

### Windows-constant audit

A grep pass during planning (`CREATE_NO_WINDOW`, `STARTUPINFO`, `STARTF_USESHOWWINDOW`, `SW_HIDE`, `windll`) found four references in app/installer code, all already inside `if platform.system() == "Windows":` branches:

- `installer.py:308` (Windows shortcut creation)
- `installer.py:376` and `:382` (Windows admin elevation)
- `core/llm_manager.py:99` (Windows-only `Popen` for `ollama serve`)

No code changes required for platform-constant safety.

## Verification

After installer runs cleanly:
- `python main.py` from `~/Desktop/PDF_App_Mac/` opens the main window without traceback
- `python run_tests.py` passes (already platform-neutral)
- Manual smoke tests:
  - Load a PDF, run OCR (Tesseract integration)
  - Ask the LLM dock a question (Ollama integration on `127.0.0.1:11434`)
  - Generate TTS audio (Piper integration; `_resolve_piper_command` already cross-platform)

## Non-goals reaffirmed

- No `.app` bundle. No `.icns` icon. No `BUNDLE` block in any spec.
- No PyArmor obfuscation. No code signing. No notarization.
- No `README_MAC.md` or other new docs — the installer's log output is the user-facing instruction surface.
