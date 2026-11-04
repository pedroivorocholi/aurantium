"""Auto-update: one in-app card, one click.

Every launch checks the appcast in the background. When a newer version
exists, the installer is downloaded and its EdDSA signature verified *before*
anything is shown — so the card that pops up (``update_card.py``) already has
the update in hand. It lists what's new (the release notes from the appcast)
and has a single button: **Update & restart**. One click runs the installer
silently, quits through the normal close path (layout saved), and the
installer relaunches the new version.

This replaced WinSparkle, whose native Win32 dialog couldn't wear the app's
design and split the update into accept → wait for download → install.
Builds up to 1.16.0 still update through WinSparkle once, to reach a build
that has this module.

The appcast format is unchanged (Sparkle's): each ``<item>`` carries its notes
in ``<description>`` and one ``<enclosure>`` per OS, tagged ``sparkle:os``.
Signatures are plain Ed25519 over the raw file bytes (see
``tools/release/sign_update.py``). Installing is the only platform-specific
step: Windows runs the Inno Setup installer with the enclosure's
``sparkle:installerArguments``; macOS swaps the ``.app`` bundle via
``updater_mac.py``.

Release/signing steps live in ``docs/guides/RELEASING.md``; key generation and
signing helpers live in ``tools/``.
"""

from __future__ import annotations

import base64
import html
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from xml.etree import ElementTree

from PySide6.QtCore import QObject, Signal

from . import __version__

# --------------------------------------------------------------------------
# Configuration — the only lines you edit to point at your own release feed.
# --------------------------------------------------------------------------

# Set GITHUB_USER once, when you create the public repo (Option A).
GITHUB_USER = "pedroivorocholi"
GITHUB_REPO = "aurantium"

# The appcast is served raw from the repo's default branch, over plain HTTPS
# (no auth) — the repo must be PUBLIC.
APPCAST_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}"
    "/main/appcast.xml"
)

# Base64 Ed25519 public key, printed by ``tools/release/gen_keys.py``. Paste it here.
# Leave blank to disable updates entirely — unsigned downloads never run.
EDDSA_PUBLIC_KEY = "7uoEAAvngbcIiyHRnkEKbqHveq7yfNaKsC646v3omxw="

_SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"

#: Verified downloads wait here until the click. Named per version, so a
#: download that finished on an earlier launch is reused, not fetched again.
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "aurantium-update"


def _placeholder(value: str) -> bool:
    return (not value) or value.startswith("YOUR_")


def _os_tag() -> str | None:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return None


def available() -> bool:
    """True if the updater is configured and could run on this platform."""
    return (
        _os_tag() is not None
        and not _placeholder(GITHUB_USER)
        and bool(EDDSA_PUBLIC_KEY)
    )


def unavailable_reason() -> str:
    """Human-readable reason the updater is off, for the manual menu path."""
    if _os_tag() is None:
        return "Automatic updates are only available on Windows or macOS."
    if _placeholder(GITHUB_USER):
        return "Updates aren't configured yet (no release feed set)."
    if not EDDSA_PUBLIC_KEY:
        return "Updates aren't configured yet (no signing key set)."
    return ""


# --------------------------------------------------------------------------
# Appcast parsing + signature verification (pure, unit-tested)
# --------------------------------------------------------------------------


def parse_version(v: str) -> tuple:
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def release_notes(description: str) -> list[str]:
    """The bullet points of an appcast ``<description>``, as plain text.

    The notes are HTML written for WinSparkle's browser pane (a ``<style>``
    block, an ``<h3>``, a ``<ul>``). The card shows them in its own type, so
    only the ``<li>`` text is kept. Notes without a list fall back to their
    plain-text lines."""
    if not description:
        return []
    description = re.sub(r"(?is)<style.*?</style>", "", description)

    def text(fragment: str) -> str:
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())

    items = [text(li) for li in re.findall(r"(?is)<li[^>]*>(.*?)</li>", description)]
    if not items:
        stripped = re.sub(r"(?is)<h\d[^>]*>.*?</h\d>", "", description)
        items = [text(line) for line in re.split(r"(?i)<br\s*/?>|\n", stripped)]
    return [i for i in items if i]


def find_item(xml_bytes: bytes, os_tag: str) -> dict | None:
    """The newest ``<item>`` with a complete ``<enclosure sparkle:os=os_tag>``."""
    root = ElementTree.fromstring(xml_bytes)
    best = None
    for item in root.iter("item"):
        for enclosure in item.findall("enclosure"):
            if enclosure.get(f"{{{_SPARKLE_NS}}}os") != os_tag:
                continue
            version = enclosure.get(f"{{{_SPARKLE_NS}}}version")
            url = enclosure.get("url")
            signature = enclosure.get(f"{{{_SPARKLE_NS}}}edSignature")
            if not (version and url and signature):
                continue
            length = enclosure.get("length")
            candidate = {
                "version": version,
                "url": url,
                "signature": signature,
                "length": int(length) if length else None,
                "arguments": enclosure.get(
                    f"{{{_SPARKLE_NS}}}installerArguments", ""
                ),
                "notes": release_notes(item.findtext("description") or ""),
            }
            if best is None or parse_version(version) > parse_version(best["version"]):
                best = candidate
    return best


def verify(data: bytes, signature_b64: str, public_key_b64: str = EDDSA_PUBLIC_KEY) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        pub.verify(base64.b64decode(signature_b64), data)
        return True
    except (InvalidSignature, ValueError):
        return False


# --------------------------------------------------------------------------
# Check → download → verify, off the Qt main thread
# --------------------------------------------------------------------------


class Updater(QObject):
    """Owns the background check and the one-click install.

    Signals cross from the worker thread to the UI thread (queued, since this
    object lives on the main thread):

    * ``ready(item)`` — a newer version is downloaded and verified; show the card.
    * ``status(text, level)`` — a manual check reporting back ("You're up to
      date", a failure). Silent launch checks never emit it.
    """

    ready = Signal(dict)
    status = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = threading.Lock()
        self._ready_item: dict | None = None

    def check(self, manual: bool = False) -> None:
        if not available():
            if manual:
                self.status.emit(unavailable_reason(), "warn")
            return
        if self._ready_item is not None:
            self.ready.emit(self._ready_item)  # already in hand: show it again
            return
        if not self._busy.acquire(blocking=False):
            if manual:
                self.status.emit("Already checking for updates…", "info")
            return
        if manual:
            self.status.emit("Checking for updates…", "info")
        threading.Thread(target=self._run, args=(manual,), daemon=True).start()

    def _run(self, manual: bool) -> None:
        try:
            import requests

            resp = requests.get(APPCAST_URL, timeout=10)
            resp.raise_for_status()
            item = find_item(resp.content, _os_tag())
            if item is None or parse_version(item["version"]) <= parse_version(__version__):
                if manual:
                    self.status.emit(f"You're up to date — Aurantium {__version__}.", "info")
                return
            if manual:
                self.status.emit(f"Downloading Aurantium {item['version']}…", "info")
            item["path"] = str(self._download(item))
            self._ready_item = item
            self.ready.emit(item)
        except Exception as exc:
            if manual:
                self.status.emit(f"Couldn't check for updates: {exc}", "warn")
        finally:
            self._busy.release()

    def _download(self, item: dict) -> Path:
        """Fetch the enclosure (or reuse an earlier verified copy) and verify
        its signature. Only a verified file is ever left under its final name."""
        import requests

        name = Path(item["url"].rsplit("/", 1)[-1]).name or "update.bin"
        target = DOWNLOAD_DIR / f"{item['version']}-{name}"
        if target.exists():
            data = target.read_bytes()
            if verify(data, item["signature"]):
                return target
            target.unlink(missing_ok=True)

        resp = requests.get(item["url"], timeout=300)
        resp.raise_for_status()
        data = resp.content
        if item["length"] and len(data) != item["length"]:
            raise ValueError("downloaded size doesn't match the appcast")
        if not verify(data, item["signature"]):
            raise ValueError("signature verification failed")
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".part")
        partial.write_bytes(data)
        os.replace(partial, target)
        return target


def install(item: dict) -> None:
    """Hand the verified download to the platform installer. Raises on failure;
    the caller quits the app on success."""
    path = Path(item["path"])
    if sys.platform == "win32":
        # os.startfile goes through ShellExecute, so the installer can ask
        # for elevation if a per-machine install needs it (Popen can't).
        # The installer waits for this process to exit (CloseApplications)
        # and relaunches the new build itself (installer.iss [Code]).
        os.startfile(str(path), "open", item.get("arguments", ""))
    elif sys.platform == "darwin":
        from . import updater_mac

        updater_mac.install(path)
    else:
        raise RuntimeError(unavailable_reason())


# --------------------------------------------------------------------------
# App wiring
# --------------------------------------------------------------------------

_updater: Updater | None = None


def init(window) -> bool:
    """Wire the updater to the main window and start the launch check.
    Safe to call unconditionally — a no-op when unavailable."""
    global _updater
    if not available() or window is None:
        return False
    from .update_card import UpdateCard

    _updater = Updater(window)
    card = UpdateCard(window)
    window._update_card = card  # repositioned from the window's resizeEvent
    _updater.ready.connect(card.show_update)
    _updater.status.connect(card.report)

    def _install(item: dict) -> None:
        try:
            install(item)
        except Exception as exc:
            card.install_failed(str(exc))
            return
        window._quit_app()

    card.install_requested.connect(_install)
    _updater.check(manual=False)
    return True


def check_now() -> None:
    """Manual 'Check for Updates…': always checks and reports back."""
    if _updater is not None:
        _updater.check(manual=True)


def cleanup() -> None:
    pass  # worker threads are daemons; nothing persistent to tear down
