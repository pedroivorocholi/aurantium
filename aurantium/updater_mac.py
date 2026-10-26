"""Auto-update for macOS — a lightweight stand-in for Sparkle.framework.

Real Sparkle needs an embedded Objective-C framework, PyObjC bridging, and a
code-signed app bundle to install updates cleanly under Gatekeeper — a heavy
lift for a non-notarized single-machine build. This module gets the same
outcome with plain Python: it reads the *same* ``appcast.xml`` used by the
Windows WinSparkle path (looking for the ``<enclosure>`` tagged
``sparkle:os="macos"``), verifies the download against the *same* EdDSA
public key using ``cryptography`` directly (the signing format documented in
``tools/release/sign_update.py`` is a generic Ed25519 signature over raw file bytes —
nothing WinSparkle-specific about it), and swaps the app bundle in place.

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

import base64
import shlex
import subprocess
import sys
import tempfile
import threading
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .updater import APPCAST_URL, EDDSA_PUBLIC_KEY, GITHUB_USER, _placeholder

_SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
_SETTINGS_KEY = "updaterMac/lastCheckDate"


class _Signals(QObject):
    message = Signal(str, str)
    offer = Signal(dict)
    quit = Signal()


_signals = _Signals()
_signals.message.connect(
    lambda title, text: QMessageBox.information(None, title, text)
)
_signals.offer.connect(lambda item: _prompt_install(item))
_signals.quit.connect(lambda: QApplication.instance().quit())


def available() -> bool:
    return (
        sys.platform == "darwin"
        and not _placeholder(GITHUB_USER)
        and bool(EDDSA_PUBLIC_KEY)
    )


def unavailable_reason() -> str:
    if sys.platform != "darwin":
        return "Automatic updates are only available on macOS or Windows."
    if _placeholder(GITHUB_USER):
        return "Updates aren't configured yet (no release feed set)."
    if not EDDSA_PUBLIC_KEY:
        return "Updates aren't configured yet (no signing key set)."
    return ""


def init() -> bool:
    """Kick off the once-a-day silent check. Safe to call unconditionally."""
    if not available():
        return False
    settings = QSettings()
    today = date.today().isoformat()
    if settings.value(_SETTINGS_KEY, "") == today:
        return True
    settings.setValue(_SETTINGS_KEY, today)
    threading.Thread(target=_background_check, args=(False,), daemon=True).start()
    return True


def check_now() -> None:
    """Manual 'Check for Updates…' — always checks, always reports back."""
    if not available():
        return
    threading.Thread(target=_background_check, args=(True,), daemon=True).start()


def cleanup() -> None:
    pass  # nothing persistent to tear down


# --------------------------------------------------------------------------
# Appcast + signature verification
# --------------------------------------------------------------------------


def _parse_version(v: str) -> tuple:
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _find_mac_item(xml_bytes: bytes) -> dict | None:
    """Newest <item> whose <enclosure sparkle:os="macos"> looks complete."""
    root = ElementTree.fromstring(xml_bytes)
    for item in root.iter("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        if enclosure.get(f"{{{_SPARKLE_NS}}}os") != "macos":
            continue
        version = enclosure.get(f"{{{_SPARKLE_NS}}}version")
        url = enclosure.get("url")
        signature = enclosure.get(f"{{{_SPARKLE_NS}}}edSignature")
        length = enclosure.get("length")
        if version and url and signature:
            return {
                "version": version,
                "url": url,
                "signature": signature,
                "length": int(length) if length else None,
            }
    return None


def _verify(data: bytes, signature_b64: str) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(EDDSA_PUBLIC_KEY))
        pub.verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError):
        return False


# --------------------------------------------------------------------------
# Background check / install (runs off the Qt main thread)
# --------------------------------------------------------------------------


def _background_check(show_up_to_date: bool) -> None:
    try:
        resp = requests.get(APPCAST_URL, timeout=10)
        resp.raise_for_status()
        item = _find_mac_item(resp.content)
    except Exception as exc:
        if show_up_to_date:
            _signals.message.emit("Check for Updates", f"Couldn't check for updates: {exc}")
        return

    if item is None or _parse_version(item["version"]) <= _parse_version(__version__):
        if show_up_to_date:
            _signals.message.emit("Check for Updates", "You're up to date.")
        return

    _signals.offer.emit(item)


def _prompt_install(item: dict) -> None:
    box = QMessageBox(
        QMessageBox.Icon.Information,
        "Update available",
        f"Aurantium {item['version']} is available (you have {__version__}).\n\n"
        "Install now? Aurantium will quit and reopen automatically.",
    )
    install = box.addButton("Install & Restart", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is not install:
        return
    threading.Thread(target=_download_and_install, args=(item,), daemon=True).start()


def _download_and_install(item: dict) -> None:
    try:
        resp = requests.get(item["url"], timeout=180)
        resp.raise_for_status()
        data = resp.content
        if item["length"] and len(data) != item["length"]:
            raise ValueError("downloaded size doesn't match appcast length")
        if not _verify(data, item["signature"]):
            raise ValueError("signature verification failed")

        new_app = _extract_app(data)
        current = _app_bundle_path()
        if current is None:
            raise RuntimeError("not running from an installed .app bundle")

        _spawn_swap_helper(current, new_app)
        _signals.quit.emit()
    except Exception as exc:
        _signals.message.emit("Update failed", f"Couldn't install the update: {exc}")


def _extract_app(data: bytes) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="aurantium_update_"))
    zip_path = tmp_dir / "update.zip"
    zip_path.write_bytes(data)
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
