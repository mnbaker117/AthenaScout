# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for AthenaScout.

Build with:
    pyinstaller athenascout.spec --noconfirm

Or use the helper script:
    python build_standalone.py
"""
import sys
from pathlib import Path

# Repo root (the directory containing this .spec file)
ROOT = Path(SPECPATH)

# Platform gates — uvloop is POSIX-only, strip is a no-op (and can break
# signed binaries) on Windows/macOS. Toggling via sys.platform keeps the
# same spec file working across the three build matrix runners.
IS_WINDOWS = sys.platform.startswith("win")
IS_POSIX = not IS_WINDOWS
STRIP_SYMBOLS = sys.platform.startswith("linux")  # safest on Linux only

# ─── Data Files ──────────────────────────────────────────────
# The frontend dist directory must be bundled so the FastAPI static
# file mount can find it via sys._MEIPASS at runtime. The path
# detection logic in app/main.py handles both source-tree and bundled
# (PyInstaller) paths transparently.
datas = [
    (str(ROOT / 'frontend' / 'dist'), 'frontend/dist'),
]

# ─── Hidden Imports ──────────────────────────────────────────
# Modules that PyInstaller's static analysis might miss because they're
# loaded dynamically (string-based imports, plugin registries, etc).
hiddenimports = [
    # Library app registry — loaded dynamically from app/library_apps/__init__.py
    'app.library_apps.calibre',

    # Source plugins — loaded by name from settings
    'app.sources.goodreads',
    'app.sources.kobo',
    'app.sources.hardcover',
    'app.sources.mam',

    # Uvicorn protocol/lifespan handlers — auto-selected at runtime
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.http.httptools_impl',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',

    # APScheduler triggers and executors
    'apscheduler.triggers.interval',
    'apscheduler.triggers.cron',
    'apscheduler.triggers.date',
    'apscheduler.executors.asyncio',
    'apscheduler.jobstores.memory',

    # aiosqlite internals
    'aiosqlite',
    'aiosqlite.core',
]

# uvloop is only available on POSIX — adding it to hiddenimports on Windows
# causes PyInstaller to warn (and in some versions, fail) on missing module.
if IS_POSIX:
    hiddenimports.append('uvicorn.loops.uvloop')

# ─── Analysis ────────────────────────────────────────────────
# Walks the dependency tree starting from the entry script.
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
        # Exclude things we don't need to slim the binary
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'PIL',
        'pytest',
    ],
    noarchive=False,
)

# ─── Python Bytecode Archive ─────────────────────────────────
pyz = PYZ(a.pure)

# ─── Executable ──────────────────────────────────────────────
# exclude_binaries=True for onedir mode (binaries go into the COLLECT step).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='athenascout',
    debug=False,
    bootloader_ignore_signals=False,
    strip=STRIP_SYMBOLS,
    upx=True,                  # Compress binaries to reduce size
    console=True,              # Show terminal — server logs are useful
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,    # No code signing — see guide
    entitlements_file=None,
)

# ─── Collect (onedir output) ─────────────────────────────────
# Bundles the executable + all binaries + data into dist/athenascout/
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=STRIP_SYMBOLS,
    upx=True,
    upx_exclude=[],
    name='athenascout',
)
