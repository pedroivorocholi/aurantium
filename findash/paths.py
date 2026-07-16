"""Resource paths that work both in development and when frozen by PyInstaller.

- ``BUNDLE_DIR``: read-only bundled resources (icon, seed layouts). In a frozen
  build these live in the PyInstaller extraction dir (``sys._MEIPASS``).
- ``EXT_DIR``: files that sit *next to the app* and may be edited by the user
  (``.env``, a ``user_panels`` folder). Next to the ``.exe`` when frozen.
"""

from __future__ import annotations

import sys
from pathlib import Path

# In dev, the package lives at app/findash/ so the project root is parent.parent.
_DEV_ROOT = Path(__file__).resolve().parent.parent

if getattr(sys, "frozen", False):  # running inside a PyInstaller bundle
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", _DEV_ROOT))
    EXT_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = _DEV_ROOT
    EXT_DIR = _DEV_ROOT
