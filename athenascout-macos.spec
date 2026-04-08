# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for AthenaScout — macOS .app BUNDLE build.

Produces a proper macOS .app bundle with windowed mode for double-click
launch from Finder. Used to create the .dmg disk image distribution.

For the standard onedir build (used by Linux native packages and the macOS
source bundle), see athenascout.spec.

Build with:
    pyinstaller athenascout-macos.spec --noconfirm --distpath dist-mac

The icon is loaded from the path in the ATHENASCOUT_ICON_PATH environment
variable. The CI workflow generates the .icns from frontend/public/icon.svg
and sets this var before invoking PyInstaller.
"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

# ─── Data Files (same as main spec) ──────────────────────────
datas = [
    (str(ROOT / 'frontend' / 'dist'), 'frontend/dist'),
]

# ─── Hidden Imports (same as main spec) ──────────────────────
hiddenimports = [
    'app.library_apps.calibre',
    'app.sources.goodreads',
    'app.sources.kobo',
    'app.sources.hardcover',
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

# ─── Executable (windowed mode for .app bundle compatibility) ─
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='athenascout',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # IMPORTANT: console=False is required for the .app bundle to launch from
    # Finder. A console=True executable can't be wrapped in a .app properly
    # because it expects an attached terminal that Finder doesn't provide.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ─── COLLECT (intermediate, fed into BUNDLE) ─────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='athenascout',
)

# ─── macOS .app Bundle ───────────────────────────────────────
# Wraps the COLLECT output into a proper macOS application bundle.
# The icon is loaded from ATHENASCOUT_ICON_PATH if set and the file exists.
icon_path = os.environ.get('ATHENASCOUT_ICON_PATH', '')
app = BUNDLE(
    coll,
    name='AthenaScout.app',
    icon=icon_path if icon_path and Path(icon_path).exists() else None,
    bundle_identifier='com.mnbaker117.athenascout',
    info_plist={
        'CFBundleShortVersionString': '2.0.0',
        'CFBundleVersion': '2.0.0',
        'CFBundleName': 'AthenaScout',
        'CFBundleDisplayName': 'AthenaScout',
        'CFBundleExecutable': 'athenascout',
        'CFBundleIdentifier': 'com.mnbaker117.athenascout',
        'NSHighResolutionCapable': True,
        'LSBackgroundOnly': False,
        'LSMinimumSystemVersion': '10.13.0',
        'NSPrincipalClass': 'NSApplication',
    },
)
