#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# build_dmg.sh — Build Papyrus Research.app and package as DMG
# Usage: ./build_dmg.sh (from project root)
# ============================================================

APP_NAME="Papyrus Research"
APP_BUNDLE="dist/${APP_NAME}.app"
DMG_NAME="Papyrus_Research.dmg"
SPEC_FILE="main_mac.spec"

echo ""
echo "📦 Papyrus Research — DMG build script"
echo "========================================"

# ── Step 1: Confirm we're in the project root ──────────────────────────────
if [[ ! -f "${SPEC_FILE}" ]]; then
  echo "❌  Cannot find '${SPEC_FILE}' in the current directory."
  echo "    Run this script from the PDF_App_Mac project root."
  exit 1
fi
echo "✅  Project root confirmed: $(pwd)"

# ── Step 2: Activate venv ─────────────────────────────────────────────────
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ ! -f ".venv/bin/activate" ]]; then
    echo "❌  No .venv found. See BUILD.md for one-time setup instructions."
    exit 1
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  echo "✅  Activated venv: .venv"
else
  echo "✅  venv already active: ${VIRTUAL_ENV}"
fi

# ── Step 3: Verify required tools ─────────────────────────────────────────
check_tool() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "${cmd}" &>/dev/null; then
    echo "❌  Required tool '${cmd}' not found. ${hint}"
    exit 1
  fi
}

check_tool pyinstaller  "Install with: pip install pyinstaller"
check_tool create-dmg   "Install with: brew install create-dmg"
check_tool xattr        "(should be pre-installed on macOS)"
check_tool codesign     "(should be pre-installed with Xcode Command Line Tools)"
echo "✅  All required tools found."

# ── Step 4: Pre-clean ─────────────────────────────────────────────────────
echo "⚠️   Cleaning previous build artefacts (build/, dist/, ${DMG_NAME}) ..."
rm -rf build/ dist/ "${DMG_NAME}"
echo "✅  Clean done."

# ── Step 5: Run PyInstaller ───────────────────────────────────────────────
echo "📦 Running PyInstaller (this takes 1–2 minutes) ..."
pyinstaller "${SPEC_FILE}" --clean --noconfirm || PYINSTALLER_EXIT=$?

if [[ ! -d "${APP_BUNDLE}" ]]; then
  echo "❌  PyInstaller failed and '${APP_BUNDLE}' does not exist."
  echo "    Check the output above for errors."
  exit 1
fi

if [[ -n "${PYINSTALLER_EXIT:-}" ]]; then
  echo "⚠️   PyInstaller exited with code ${PYINSTALLER_EXIT}, but '${APP_BUNDLE}' was produced."
  echo "    This is expected when the build directory is on an iCloud-synced volume"
  echo "    (codesign fails on iCloud xattrs). Continuing — will fix xattrs next."
fi
echo "✅  App bundle present: ${APP_BUNDLE}"

# ── Step 6: iCloud xattr workaround (unconditional / idempotent) ──────────
echo "⚠️   Clearing iCloud xattrs recursively ..."
xattr -cr "${APP_BUNDLE}"
echo "⚠️   Re-signing ad-hoc ..."
codesign --force --deep --sign - "${APP_BUNDLE}"
echo "✅  xattr cleared and app re-signed."

# ── Step 7: Sanity-check the bundle ───────────────────────────────────────
echo "📦 Verifying codesign ..."
if ! VERIFY_OUT=$(codesign --verify --deep "${APP_BUNDLE}" 2>&1); then
  echo "❌  codesign --verify failed:"
  echo "${VERIFY_OUT}"
  exit 1
fi
echo "✅  codesign --verify passed."

# ── Step 8: Package into DMG ──────────────────────────────────────────────
echo "📦 Packaging DMG with create-dmg ..."
create-dmg \
  --volname "${APP_NAME}" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 128 \
  --icon "${APP_NAME}.app" 150 200 \
  --app-drop-link 450 200 \
  --no-internet-enable \
  "${DMG_NAME}" \
  "dist/${APP_NAME}.app"

if [[ ! -f "${DMG_NAME}" ]]; then
  echo "❌  create-dmg finished but '${DMG_NAME}' was not found."
  exit 1
fi
echo "✅  DMG created."

# ── Step 9: Summary ───────────────────────────────────────────────────────
DMG_SIZE=$(du -sh "${DMG_NAME}" | awk '{print $1}')
echo ""
echo "========================================"
echo "✅  Build complete!"
echo "    DMG:  $(pwd)/${DMG_NAME}"
echo "    Size: ${DMG_SIZE}"
echo "    Distribute: recipients should open the DMG, then drag Papyrus Research.app to /Applications."
echo "========================================"
