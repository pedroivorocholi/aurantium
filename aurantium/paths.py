"""Resource paths that work both in development and when frozen by PyInstaller.

- ``BUNDLE_DIR``: read-only bundled resources (icon, seed layouts). In a frozen
  build these live in the PyInstaller extraction dir (``sys._MEIPASS``).
- ``EXT_DIR``: files that sit *next to the app* and may be edited by the user
  (``.env``, a ``user_panels`` folder). Next to the ``.exe`` when frozen.
- ``ASSETS_DIR``: the app's own art (``icons/`` and ``images/``), bundled under
  ``assets/`` so the frozen layout mirrors the repo's.
- ``LOG_DIR``: runtime logs. Next to the ``.exe`` when frozen (where support
  has always found them); ``logs/`` in the dev tree, so they don't litter the root.
- ``WINSPARKLE_DLL``: the Windows updater DLL. PyInstaller puts it at the bundle
  root; in the dev tree it lives with the other Windows packaging files.
"""

from __future__ import annotations

import sys
from pathlib import Path

# In dev, the package lives at app/aurantium/ so the project root is parent.parent.
_DEV_ROOT = Path(__file__).resolve().parent.parent

if getattr(sys, "frozen", False):  # running inside a PyInstaller bundle
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", _DEV_ROOT))
    EXT_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = _DEV_ROOT
    EXT_DIR = _DEV_ROOT

ASSETS_DIR = BUNDLE_DIR / "assets"

if getattr(sys, "frozen", False):
    LOG_DIR = EXT_DIR
    WINSPARKLE_DLL = BUNDLE_DIR / "WinSparkle.dll"
else:
    LOG_DIR = _DEV_ROOT / "logs"
    WINSPARKLE_DLL = _DEV_ROOT / "packaging" / "windows" / "WinSparkle.dll"


def log_file(name: str) -> Path:
    """``LOG_DIR / name``, creating ``LOG_DIR`` first (the dev ``logs/`` folder
    is not in git, so a fresh checkout doesn't have it). Raises ``OSError`` like
    any other write would; every caller already guards its log write with one."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / name
