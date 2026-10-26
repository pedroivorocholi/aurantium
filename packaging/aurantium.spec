# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds a standalone aurantium app.

Run from the repo root (app/), so dist/ and build/ land there:

Windows:  .venv\\Scripts\\pyinstaller packaging\\aurantium.spec --noconfirm  ->  dist\\aurantium\\aurantium.exe
macOS:    .venv/bin/pyinstaller packaging/aurantium.spec --noconfirm        ->  dist/aurantium.app
(Run PyInstaller on the target OS — you can't cross-build a Mac app from Windows.)

Every source path below is absolute, built from ROOT (the repo root, one level
above this spec), so the spec does not depend on the working directory.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

IS_MAC = sys.platform == "darwin"

# SPECPATH is the folder holding this spec (packaging/); the repo root is above it.
ROOT = os.path.dirname(os.path.abspath(SPECPATH))
_WINDOWS_DIR = os.path.join(SPECPATH, "windows")
_ASSETS = os.path.join(ROOT, "assets")

# Make the app package importable while this spec is parsed.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Per-OS app icon (.icns for the macOS .app, .ico for the Windows .exe).
_ICON = os.path.join(_ASSETS, "icons", "aurantium.icns" if IS_MAC else "aurantium.ico")
if not os.path.exists(_ICON):
    _ICON = None

# assets/ is bundled whole and keeps its layout: paths.ASSETS_DIR reads
# icons/ and images/ from it in dev and frozen runs alike.
datas = [
    (os.path.join(ROOT, "layouts"), "layouts"),
    (_ASSETS, "assets"),
    (os.path.join(ROOT, ".env.example"), "."),
]
binaries = []
# QtNetwork drives the single-instance QLocalServer/QLocalSocket guard in
# __main__.py. It's the only PySide6 module reached solely via runtime import,
# so PyInstaller's PySide6 hook won't detect it — list it explicitly.
hiddenimports = ["PySide6.QtNetwork"]

# WinSparkle auto-updater DLL (Windows only). It lives in packaging/windows/
# (see docs/guides/RELEASING.md) and is bundled at the bundle root, where
# paths.WINSPARKLE_DLL points in a frozen build.
_WINSPARKLE = os.path.join(_WINDOWS_DIR, "WinSparkle.dll")
if not IS_MAC and os.path.exists(_WINSPARKLE):
    binaries.append((_WINSPARKLE, "."))

# Panels are imported dynamically at runtime (importlib), so PyInstaller's
# static analysis never sees them — list them explicitly from the package's
# BUILTIN table. (collect_submodules can't help: it runs in an isolated
# subprocess that cannot import the non-installed `aurantium` package.)
from aurantium.panels import BUILTIN as _PANELS

hiddenimports += [
    "aurantium.panels",
    "aurantium.panels._news_common",
    "aurantium.panels._events_lane",
    "aurantium.panels._chart_export",
]
hiddenimports += [f"aurantium.panels.{m}" for m in _PANELS]
hiddenimports += [
    "aurantium.providers",
    "aurantium.providers.econ",
    "aurantium.providers.fundamentals",
    "aurantium.providers.market",
    "aurantium.providers.news",
    "aurantium.providers.dayinfo",
    # Key-metrics maths. Imported lazily inside FundamentalsProvider's
    # metrics fetch, so static analysis never sees it.
    "aurantium.valuation",
    "aurantium.glossary",
]
# Shared UI components (imported by panels) and the onboarding dialog (imported
# lazily inside app.py). Listed explicitly so the frozen build never misses them.
hiddenimports += [
    "aurantium.components",
    "aurantium.components.market_table",
    "aurantium.components.fit_width",
    "aurantium.components.chip_bar",
    "aurantium.components.responsive_row",
    "aurantium.components.action_bar",
    "aurantium.components.fmt",
    # Pure-maths colour module: OKLab, CVD and contrast. Imported normally by
    # theme.py and several panels, so static analysis should find it — listed
    # anyway, because everything else in this block is.
    "aurantium.color",
    # Keyboard-focus filter. Imported *inside* __main__.main() as
    # ``from . import focus``, which is the same lazy-import shape that put
    # onboarding_dialog in this list. Without it a frozen build loses the focus
    # ring, or fails at startup.
    "aurantium.focus",
    "aurantium.popups",
    # Press feedback filter. Imported the same lazy way as ``focus`` above,
    # from inside __main__.main(), so static analysis never sees it — without
    # this line a frozen build silently loses every button's press response.
    "aurantium.press",
    # The painted icon family. Reached lazily from app.py's _chrome_icon and
    # from the onboarding dialog, so static analysis sees only the list
    # editor's import of it — listed anyway, on the same rule as everything
    # else in this block.
    "aurantium.icons",
    "aurantium.onboarding_dialog",
    # The F1 guide's content model (PanelGuide, the shortcut table, the static
    # pages). Imported by panel.py today, but listed on the same rule as every
    # first-party module: the onboarding dialog reaches it lazily.
    "aurantium.guide",
    "aurantium.language_dialog",
    "aurantium.languages",
    "aurantium.alerts",
    "aurantium.date_context",
    "aurantium.sector_meta",
    "aurantium.command_bar",
    "aurantium.title_bar",
    "aurantium.transitions",
    "aurantium.reicon_icons",
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
    [os.path.join(SPECPATH, "run_aurantium.py")],
    pathex=[ROOT],
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
_SPLASH = os.path.join(_ASSETS, "images", "aurantium_splash.png")
if not IS_MAC and os.path.exists(_SPLASH):
    splash = Splash(
        _SPLASH,
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
