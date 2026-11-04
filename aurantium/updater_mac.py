"""macOS install step for the updater — a lightweight stand-in for Sparkle.framework.

Real Sparkle needs an embedded Objective-C framework, PyObjC bridging, and a
code-signed app bundle to install updates cleanly under Gatekeeper — a heavy
lift for a non-notarized single-machine build. ``updater.py`` does the shared
part on every platform (appcast, download, EdDSA verification, the update
card); this module only swaps the verified ``.app`` bundle in place.

The swap itself never touches files the running process has open: it hands
the rename/move off to a detached shell helper that starts only after this
process has fully quit (``sleep 1`` first), then relaunches via ``open``.
Deleting/replacing a macOS ``.app`` bundle *while it's still running* is
technically possible on POSIX (the kernel keeps unlinked-but-open inodes
alive), but this app lazily imports modules at runtime (panels, openpyxl on
first export, etc.) — a lazy import after the on-disk files are gone would
throw. Quitting first avoids that whole class of bug.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def install(archive: Path) -> None:
    """Unpack the verified update zip and schedule the swap for after quit.
    Raises if this isn't a frozen ``.app`` build; the caller then quits."""
    current = _app_bundle_path()
    if current is None:
        raise RuntimeError("not running from an installed .app bundle")
    _spawn_swap_helper(current, _extract_app(archive))


def _extract_app(zip_path: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="aurantium_update_"))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp_dir)
    for child in tmp_dir.iterdir():
        if child.suffix == ".app":
            return child
    raise ValueError("update archive did not contain a .app bundle")


def _app_bundle_path() -> Path | None:
    """Path to the running aurantium.app, or None outside a frozen build."""
    if not getattr(sys, "frozen", False):
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def _spawn_swap_helper(current: Path, new_app: Path) -> None:
    """Detached shell script that does the rename/move after we've exited.

    ``sleep 1`` guarantees this process (and its open file handles / mmapped
    dylibs) is gone before anything under ``current`` is touched.
    """
    old_backup = current.with_name(current.name + ".old")
    script = "\n".join(
        [
            "sleep 1",
            f"rm -rf {shlex.quote(str(old_backup))}",
            f"mv {shlex.quote(str(current))} {shlex.quote(str(old_backup))}",
            f"mv {shlex.quote(str(new_app))} {shlex.quote(str(current))}",
            f"xattr -cr {shlex.quote(str(current))} 2>/dev/null",
            f"rm -rf {shlex.quote(str(old_backup))}",
            f"open {shlex.quote(str(current))}",
        ]
    )
    subprocess.Popen(
        ["/bin/sh", "-c", script],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
