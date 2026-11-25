# -*- mode: python ; coding: utf-8 -*-
# Papyrus build spec.
#   Plain build:       pyinstaller --clean main.spec
#   Obfuscated build:  pyarmor gen --pack main.spec -r main.py core gui web
#
# Data paths below must match where the frozen code looks under sys._MEIPASS:
#   core/project_manager.py   -> analysis_templates.json at the bundle ROOT
#   core/engine/bias_evaluator.py -> bias_datasets/ at the bundle ROOT
#   core/help/content_loader.py   -> core/help/content/
#   core/plugins/plugin_loader.py -> plugins/<name>/plugin.py (loaded from disk)
#   core/papyrus_core.py      -> assets/metrics.db, assets/default_english.json
#   web/server/app.py         -> web/frontend/dist/
#   main.py (--run-pdf-worker) -> core/pdf_worker.py as plain source

import os
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


def _plugin_datas():
    """Ship the plugins folder as files: the loader imports plugin.py by path."""
    out = []
    for root, dirs, files in os.walk('plugins'):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', 'dbs')]
        for name in files:
            if name.endswith('.py'):
                out.append((os.path.join(root, name), root))
    return out


datas = [
    ('icon.png', '.'),
    ('core/pdf_worker.py', 'core'),
    ('core/analysis_templates.json', '.'),
    ('core/bias_datasets/*.json', 'bias_datasets'),
    ('core/help/content/*.json', 'core/help/content'),
    ('assets/default_english.json', 'assets'),
    ('assets/metrics.db', 'assets'),
    ('assets/quill', 'assets/quill'),
    ('assets/opendeck', 'assets/opendeck'),
    ('qtwebengine_dictionaries', 'qtwebengine_dictionaries'),
    ('gui/components/examples/*.gif', 'gui/components/examples'),
    ('web/frontend/dist', 'web/frontend/dist'),
] + _plugin_datas()

hiddenimports = [
    # Vector store
    'chromadb.telemetry.product.posthog',
    'chromadb.api.rust',
    # Media / speech
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'faster_whisper',
    'ctranslate2',
    # Web companion
    'fastapi',
    'multipart',
    'python_multipart',
    'qrcode',
    'PIL',
    'PIL.Image',
    # Models
    'huggingface_hub',
    # Used only by plugins (plugins are loaded from disk, so analysis cannot see them)
    'duckdb',
    'pyarrow',
    'pandas',
    'pyzotero',
    'pyzotero.zotero',
] + collect_submodules('onnxruntime') \
  + collect_submodules('tokenizers') \
  + collect_submodules('uvicorn')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=collect_dynamic_libs('onnxruntime') + collect_dynamic_libs('tokenizers'),
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Dev-only packages that must never end up in the app
    excludes=['tests', 'tools', 'pytest', 'tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    icon='icon.png',
    name='main',  # installer.py expects dist/main (main.exe on Windows)
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
