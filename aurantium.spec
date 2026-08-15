# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds a standalone aurantium app.

Windows:  .venv\\Scripts\\pyinstaller aurantium.spec --noconfirm  ->  dist\\aurantium.exe
macOS:    .venv/bin/pyinstaller aurantium.spec --noconfirm        ->  dist/aurantium.app
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
_ICON = "aurantium.icns" if IS_MAC else "aurantium.ico"
if not os.path.exists(_ICON):
    _ICON = None

datas = [("layouts", "layouts"), (".env.example", ".")]
for _asset in (
    "aurantium.ico",
    "aurantium.icns",
    "aurantium_logo.png",
    "aurantium_logo_ondark.png",
    "aurantium_splash.png",
):
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
# subprocess that cannot import the non-installed `aurantium` package.)
from aurantium.panels import BUILTIN as _PANELS

hiddenimports += ["aurantium.panels", "aurantium.panels._news_common"]
hiddenimports += [f"aurantium.panels.{m}" for m in _PANELS]
hiddenimports += [
    "aurantium.providers",
    "aurantium.providers.econ",
    "aurantium.providers.fundamentals",
    "aurantium.providers.market",
    "aurantium.providers.news",
]
# Shared UI components (imported by panels) and the onboarding dialog (imported
# lazily inside app.py). Listed explicitly so the frozen build never misses them.
hiddenimports += [
    "aurantium.components",
    "aurantium.components.market_table",
    "aurantium.onboarding_dialog",
    "aurantium.language_dialog",
    "aurantium.languages",
    "aurantium.alerts",
    "aurantium.command_bar",
    "aurantium.undo",
]

# macOS-only updater backend (see updater.py's docstring); imported lazily
# from updater.py so it never loads on Windows, but must be bundled for it.
if IS_MAC:
    hiddenimports += ["aurantium.updater_mac"]

# yfinance / news deps that static analysis under-collects.
hiddenimports += [
    "multitasking", "frozendict", "peewee", "platformdirs",
    "websockets", "html5lib", "bs4",
    "openpyxl",  # imported lazily by the Financials xlsx export
]

# Packages with data files or native binaries to bundle fully. langdetect is
# here because its language models live in a `profiles/` data directory that
# static analysis never sees — without collect_all the frozen build imports
# fine and then raises at the first detection, silently disabling the news
# language gate (languages.py treats a missing detector as "accept").
for _pkg in ("yfinance", "gnews", "feedparser", "curl_cffi", "PySide6QtAds", "langdetect"):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        pass

a = Analysis(
    ["run_aurantium.py"],
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

# Boot splash: the bootloader shows the logo card near-instantly on launch,
# before Python even starts; __main__.py swaps it for a Qt copy once Qt is up.
# Rendered by a minimal Tcl/Tk the bootloader bundles itself — independent of
# the `tkinter` exclude above. Windows only: PyInstaller does not support
# Splash on macOS.
splash = None
if not IS_MAC and os.path.exists("aurantium_splash.png"):
    splash = Splash(
        "aurantium_splash.png",
        binaries=a.binaries,
        datas=a.datas,
        text_pos=None,
    )

# Declare per-monitor DPI awareness in the exe manifest (PyInstaller's default
# template plus the dpiAwareness setting). Without it the process starts
# DPI-unaware, Windows scales the Tk splash up, and the instant Qt flips
# awareness on the splash visibly shrinks off-center. With it the splash
# renders 1:1 from the first frame and nothing ever re-scales; Qt sets PMv2
# itself anyway, so app behavior is unchanged.
_MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{e2011457-1546-43c5-a5fe-008deee3d3f0}"></supportedOS>
      <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"></supportedOS>
      <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"></supportedOS>
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"></supportedOS>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"></supportedOS>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
    </windowsSettings>
  </application>
</assembly>
"""

# One-FOLDER build (dist/aurantium/): the exe runs directly from the install
# dir with its DLLs in _internal/ beside it — no per-launch %TEMP% unpack.
# That means instant startup and, crucially, a reliable relaunch when the
# installer starts the app after a silent auto-update (a one-file exe unpacking
# 100 MB in the installer's context is what broke the post-update relaunch).
exe = EXE(
    pyz,
    a.scripts,
    *([splash] if splash is not None else []),
    [],
    exclude_binaries=True,
    name="aurantium",
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
    manifest=None if IS_MAC else _MANIFEST,
)

coll = COLLECT(
    exe,
    *([splash.binaries] if splash is not None else []),
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="aurantium",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="aurantium.app",
        icon=_ICON,
        bundle_identifier="aurantium.terminal.desktop",
        info_plist={
            "CFBundleName": "aurantium",
            "CFBundleDisplayName": "aurantium",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.finance",
        },
    )
