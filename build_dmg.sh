#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# build_dmg.sh — Build Papyrus Research.app and package as DMG
# Usage: ./build_dmg.sh (from project root)
#
# Builds entirely inside /tmp because the project root is on an
# iCloud-synced volume (~/Desktop/) and iCloud-fileprovider plants
# xattrs faster than `codesign` can run, breaking ad-hoc signing.
# Only the final .dmg is moved back to the project root.
# ============================================================

APP_NAME="Papyrus Research"
DMG_NAME="Papyrus_Research.dmg"
SPEC_FILE="main_mac.spec"
PROJECT_ROOT="$(pwd)"
TMP_BASE="/tmp/papyrus_build_$$"
TMP_DIST="${TMP_BASE}/dist"
TMP_WORK="${TMP_BASE}/work"
APP_BUNDLE="${TMP_DIST}/${APP_NAME}.app"

# Always clean up our /tmp scratch area on exit (success or failure).
trap 'rm -rf "${TMP_BASE}"' EXIT

echo ""
echo "📦 Papyrus Research — DMG build script"
echo "========================================"

# ── Step 1: Confirm we're in the project root ──────────────────────────────
if [[ ! -f "${SPEC_FILE}" ]]; then
  echo "❌  Cannot find '${SPEC_FILE}' in the current directory."
  echo "    Run this script from the PDF_App_Mac project root."
  exit 1
fi
echo "✅  Project root confirmed: ${PROJECT_ROOT}"

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
echo "⚠️   Cleaning previous build artefacts (build/, dist/, ${DMG_NAME}, ${TMP_BASE}) ..."
rm -rf build/ dist/ "${DMG_NAME}" "${TMP_BASE}"
mkdir -p "${TMP_BASE}"
echo "✅  Clean done. Scratch dir: ${TMP_BASE}"

# ── Step 5: Run PyInstaller, output direct to /tmp ────────────────────────
# Building inside /tmp keeps the bundle off the iCloud-synced volume so
# codesign doesn't race iCloud-fileprovider re-tagging files mid-run.
echo "📦 Running PyInstaller (this takes 1–2 minutes) ..."
pyinstaller "${SPEC_FILE}" --clean --noconfirm \
  --distpath "${TMP_DIST}" \
  --workpath "${TMP_WORK}" \
  || PYINSTALLER_EXIT=$?

if [[ ! -d "${APP_BUNDLE}" ]]; then
  echo "❌  PyInstaller failed and '${APP_BUNDLE}' does not exist."
  echo "    Check the output above for errors."
  exit 1
fi

if [[ -n "${PYINSTALLER_EXIT:-}" ]]; then
  echo "⚠️   PyInstaller exited ${PYINSTALLER_EXIT}, but the bundle was produced."
  echo "    (PyInstaller's auto-codesign step often fails when source frameworks"
  echo "     carry iCloud xattrs from the venv. We'll strip + re-sign in /tmp next.)"
fi
echo "✅  App bundle present: ${APP_BUNDLE}"

# ── Step 6: Strip xattrs and re-sign inside /tmp ─────────────────────────
# /tmp is not iCloud-synced, so xattr -cr's effect is permanent for the
# duration of this script. codesign can then write the signature.
echo "⚠️   Clearing xattrs recursively (in /tmp; iCloud cannot race here) ..."
xattr -cr "${APP_BUNDLE}"
echo "⚠️   Re-signing ad-hoc ..."
codesign --force --deep --sign - "${APP_BUNDLE}"
echo "✅  xattrs cleared and app re-signed."

# ── Step 7: Sanity-check the bundle ───────────────────────────────────────
echo "📦 Verifying codesign ..."
if ! VERIFY_OUT=$(codesign --verify --deep "${APP_BUNDLE}" 2>&1); then
  echo "❌  codesign --verify failed:"
  echo "${VERIFY_OUT}"
  exit 1
fi
echo "✅  codesign --verify passed."

# ── Step 8: Package into DMG (also in /tmp) ───────────────────────────────
echo "📦 Packaging DMG with create-dmg ..."
TMP_DMG="${TMP_BASE}/${DMG_NAME}"
# create-dmg requires that the output file does not exist yet.
rm -f "${TMP_DMG}"

# Run from /tmp so create-dmg's intermediate work files land there too.
(
  cd "${TMP_BASE}"
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
)

if [[ ! -f "${TMP_DMG}" ]]; then
  echo "❌  create-dmg finished but '${TMP_DMG}' was not found."
  exit 1
fi

# ── Step 9: Move just the .dmg back to the project root ──────────────────
mv "${TMP_DMG}" "${PROJECT_ROOT}/${DMG_NAME}"
echo "✅  DMG moved to project root."

# ── Step 10: Summary ──────────────────────────────────────────────────────
DMG_SIZE=$(du -sh "${PROJECT_ROOT}/${DMG_NAME}" | awk '{print $1}')
echo ""
echo "========================================"
echo "✅  Build complete!"
echo "    DMG:  ${PROJECT_ROOT}/${DMG_NAME}"
echo "    Size: ${DMG_SIZE}"
echo "    Distribute: recipients should open the DMG, then drag Papyrus Research.app to /Applications."
echo "========================================"
