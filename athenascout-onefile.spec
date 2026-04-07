# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for AthenaScout — SINGLE-FILE build.

Produces a single self-contained executable that extracts to a temp directory
on each launch. Used for the Windows standalone .exe distribution.

For the standard onedir build (faster startup, used by Linux native packages
and source bundles), see athenascout.spec.

Build with:
    pyinstaller athenascout-onefile.spec --noconfirm
"""
from pathlib import Path

ROOT = Path(SPECPATH)

# ─── Data Files ──────────────────────────────────────────────
# Same as athenascout.spec — frontend dist must be bundled.
datas = [
    (str(ROOT / 'frontend' / 'dist'), 'frontend/dist'),
]

# ─── Hidden Imports ──────────────────────────────────────────
# Same as athenascout.spec — covers dynamically-loaded modules.
hiddenimports = [
    'app.library_apps.calibre',
    'app.sources.goodreads',
    'app.sources.kobo',
    'app.sources.hardcover',
    'app.sources.fantasticfiction',
    'app.sources.mam',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.loops.uvloop',
    'apscheduler.triggers.interval',
    'apscheduler.triggers.cron',
    'apscheduler.triggers.date',
    'apscheduler.executors.asyncio',
    'apscheduler.jobstores.memory',
    'aiosqlite',
    'aiosqlite.core',
]

# ─── Analysis ────────────────────────────────────────────────
a = Analysis(
    ['run.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PIL',
        'pytest',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# ─── Onefile Executable ──────────────────────────────────────
# exclude_binaries=False packs everything into the EXE itself.
# No COLLECT step — the .exe is fully self-contained.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AthenaScout-Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,        # Default: extract to system temp
    console=True,               # Match the onedir build — server logs are useful
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
