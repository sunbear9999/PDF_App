# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('gui/components/examples/*.gif', 'gui/components/examples'),
        ('icon.png', '.')],
    hiddenimports=['chromadb.telemetry.product.posthog','chromadb.api.rust','piper-tts'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Pyarmor patch start:

def apply_pyarmor_patch():

    srcpath = ['/home/sunbe/papyrus/PDF_App']
    obfpath = '/home/sunbe/papyrus/PDF_App/.pyarmor/pack/dist'
    pkgname = 'pyarmor_runtime_000000'
    pkgpath = os.path.join(obfpath, pkgname)
    extpath = os.path.join(pkgname, 'pyarmor_runtime.so')

    if hasattr(a.pure, '_code_cache'):
        code_cache = a.pure._code_cache
    else:
        from PyInstaller.config import CONF
        code_cache = CONF['code_cache'].get(id(a.pure))

    srclist = [os.path.normcase(x) for x in srcpath]
    def match_obfuscated_script(orgpath):
        for x in srclist:
            if os.path.normcase(orgpath).startswith(x):
                return os.path.join(obfpath, orgpath[len(x)+1:])

    count = 0
    for i in range(len(a.scripts)):
        x = match_obfuscated_script(a.scripts[i][1])
        if x and os.path.exists(x):
            a.scripts[i] = a.scripts[i][0], x, a.scripts[i][2]
            count += 1
    if count == 0:
        raise RuntimeError('No obfuscated script found')

    for i in range(len(a.pure)):
        x = match_obfuscated_script(a.pure[i][1])
        if x and os.path.exists(x):
            code_cache.pop(a.pure[i][0], None)
            a.pure[i] = a.pure[i][0], x, a.pure[i][2]

    a.pure.append((pkgname, os.path.join(pkgpath, '__init__.py'), 'PYMODULE'))
    a.binaries.append((extpath, os.path.join(obfpath, extpath), 'EXTENSION'))

apply_pyarmor_patch()

# Pyarmor patch end.
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    icon='icon.ico',
    name='main',
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
