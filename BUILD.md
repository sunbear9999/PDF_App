# Building Papyrus Research

## One-time prerequisites

```bash
# 1. Install system deps (Tk for installer.py's tkinter; create-dmg for packaging)
brew install python-tk@3.14 create-dmg

# 2. Create and activate the venv
python3 -m venv .venv && source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install PyInstaller
pip install pyinstaller
```

## Build the .dmg

```bash
source .venv/bin/activate   # skip if already active
./build_dmg.sh
```

Output: `Papyrus_Research.dmg` in the project root. Takes ~2 minutes.

## What's bundled

- Python 3.x runtime
- Tk (tkinter)
- PySide6 (Qt6 GUI framework + WebEngine)
- All pip dependencies from `requirements.txt`
- App source (`main.py`, `core/`, `gui/`, `models/`)
- Static assets (`assets/`)
- QtWebEngine dictionaries (`qtwebengine_dictionaries/`)
- App icon (`icon.icns`)

## What's NOT bundled

- **Ollama** — the app's first-launch dialog (`core/first_launch.py`) detects whether Ollama is installed on the recipient's Mac and prompts them to install it if missing.
- **Tesseract** — detected on first OCR use; the app surfaces an install prompt at that point.

Recipients need to install these separately if they want AI and OCR features.

## Distribution caveats

The `.app` is **ad-hoc codesigned** (not notarized). Recipients will see a Gatekeeper warning on first open ("Apple cannot verify it"). Workaround:

> Right-click → **Open** → **Open Anyway**

Or, once:
```bash
xattr -d com.apple.quarantine "/Applications/Papyrus Research.app"
```

For real distribution (no warning), you need an Apple Developer ID and notarization — out of scope for this build.
