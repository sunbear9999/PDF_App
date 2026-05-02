# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Mac (Apple Silicon) .app bundle.
# Do NOT merge into main.spec — that file is Windows-targeted.

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('gui/components/examples/*.gif', 'gui/components/examples'),
        ('icon.png', '.'),
        ('icon.icns', '.'),
        ('core/pdf_worker.py', 'core'),
        ('assets', 'assets'),
        ('qtwebengine_dictionaries', 'qtwebengine_dictionaries'),
    ],
    hiddenimports=[
        'chromadb.telemetry.product.posthog',
        'chromadb.api.rust',
        'piper-tts',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Papyrus Research',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX breaks Mac code-signing; never use on macOS
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,            # Mac drag-and-drop / Open With file arguments
    target_arch='arm64',            # Apple Silicon; change to None for universal2
    codesign_identity=None,         # ad-hoc signing; Gatekeeper warning expected on first run
    entitlements_file=None,
    icon='icon.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Papyrus Research',
)

app = BUNDLE(
    coll,
    name='Papyrus Research.app',
    icon='icon.icns',
    bundle_identifier='com.papyrus.research',
    info_plist={
        'CFBundleName': 'Papyrus Research',
        'CFBundleDisplayName': 'Papyrus Research',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        # Required for QtWebEngine to load remote-style content / spellcheck files
        'NSAppTransportSecurity': {'NSAllowsArbitraryLoads': True},
        'LSMinimumSystemVersion': '11.0',
        'LSApplicationCategoryType': 'public.app-category.productivity',
    },
)
