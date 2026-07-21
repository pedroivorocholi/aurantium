# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds a standalone findash app.

Windows:  .venv\\Scripts\\pyinstaller findash.spec --noconfirm  ->  dist\\findash.exe
macOS:    .venv/bin/pyinstaller findash.spec --noconfirm        ->  dist/findash.app
(Run PyInstaller on the target OS — you can't cross-build a Mac app from Windows.)
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

IS_MAC = sys.platform == "darwin"

# Make the app package importable while this spec is parsed.
_APP_DIR = os.path.abspath(os.getcwd())
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# Per-OS app icon (.icns for the macOS .app, .ico for the Windows .exe).
_ICON = "findash.icns" if IS_MAC else "findash.ico"
if not os.path.exists(_ICON):
    _ICON = None

datas = [("layouts", "layouts"), (".env.example", ".")]
for _asset in ("findash.ico", "findash.icns"):
    if os.path.exists(_asset):
        datas.append((_asset, "."))
binaries = []
# QtNetwork drives the single-instance QLocalServer/QLocalSocket guard in
# __main__.py. It's the only PySide6 module reached solely via runtime import,
# so PyInstaller's PySide6 hook won't detect it — list it explicitly.
hiddenimports = ["PySide6.QtNetwork"]

# WinSparkle auto-updater DLL (Windows only). Drop the 64-bit WinSparkle.dll
# next to this spec — see RELEASING.md. Bundled at the app root so
# updater.py can load it from BUNDLE_DIR at runtime.
if not IS_MAC and os.path.exists("WinSparkle.dll"):
    binaries.append(("WinSparkle.dll", "."))

# Panels are imported dynamically at runtime (importlib), so PyInstaller's
# static analysis never sees them — list them explicitly from the package's
# BUILTIN table. (collect_submodules can't help: it runs in an isolated
# subprocess that cannot import the non-installed `findash` package.)
from findash.panels import BUILTIN as _PANELS

hiddenimports += ["findash.panels", "findash.panels._news_common"]
hiddenimports += [f"findash.panels.{m}" for m in _PANELS]
hiddenimports += [
    "findash.providers",
    "findash.providers.econ",
    "findash.providers.fundamentals",
    "findash.providers.market",
    "findash.providers.news",
]
# Shared UI components (imported by panels) and the onboarding dialog (imported
# lazily inside app.py). Listed explicitly so the frozen build never misses them.
hiddenimports += [
    "findash.components",
    "findash.components.market_table",
    "findash.onboarding_dialog",
    "findash.alerts",
    "findash.command_bar",
    "findash.undo",
]

# yfinance / news deps that static analysis under-collects.
hiddenimports += [
    "multitasking", "frozendict", "peewee", "platformdirs",
    "websockets", "html5lib", "bs4",
    "openpyxl",  # imported lazily by the Financials xlsx export
]

# Packages with data files or native binaries to bundle fully.
for _pkg in ("yfinance", "gnews", "feedparser", "curl_cffi", "PySide6QtAds"):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        pass

a = Analysis(
    ["run_findash.py"],
    pathex=[_APP_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# One-FOLDER build (dist/findash/): the exe runs directly from the install
# dir with its DLLs in _internal/ beside it — no per-launch %TEMP% unpack.
# That means instant startup and, crucially, a reliable relaunch when the
# installer starts the app after a silent auto-update (a one-file exe unpacking
# 100 MB in the installer's context is what broke the post-update relaunch).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="findash",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="findash",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="findash.app",
        icon=_ICON,
        bundle_identifier="findash.terminal.desktop",
        info_plist={
            "CFBundleName": "findash",
            "CFBundleDisplayName": "findash",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.finance",
        },
    )
