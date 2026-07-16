"""Auto-update via WinSparkle (Windows only).

WinSparkle checks an appcast feed, downloads the signed installer when a newer
version exists, verifies its EdDSA signature against the public key embedded
below, and runs it. The whole thing is optional at runtime: if the DLL is
missing, the public key is unset, or we're not on Windows, the updater quietly
disables itself and the app runs normally.

There is no maintained Python binding, so this wraps ``WinSparkle.dll`` directly
via ctypes. On 64-bit Windows there is a single calling convention, so plain
``CDLL`` is correct.

Release/signing steps live in ``RELEASING.md``; key generation and signing
helpers live in ``tools/``.
"""

from __future__ import annotations

import sys

from . import __version__
from .paths import BUNDLE_DIR

# --------------------------------------------------------------------------
# Configuration — the only lines you edit to point at your own release feed.
# --------------------------------------------------------------------------

# Set GITHUB_USER once, when you create the public repo (Option A).
GITHUB_USER = "pedroivorocholi"
GITHUB_REPO = "findash"

# The appcast is served raw from the repo's default branch. WinSparkle fetches
# this over plain HTTPS (no auth) — the repo must be PUBLIC.
APPCAST_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}"
    "/main/appcast.xml"
)

# Base64 Ed25519 public key, printed by ``tools/gen_keys.py``. Paste it here.
# Leave blank to disable signature checking (NOT recommended — the updater
# stays disabled until this is set, so unsigned downloads never run).
EDDSA_PUBLIC_KEY = "7uoEAAvngbcIiyHRnkEKbqHveq7yfNaKsC646v3omxw="

# Where WinSparkle stores its own settings (last check time, the user's
# "check automatically" choice). Kept under the app's own key.
_REGISTRY_PATH = r"Software\findash\WinSparkle"
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60  # daily

_dll = None  # loaded WinSparkle.dll handle, or None when unavailable


def _placeholder(value: str) -> bool:
    return (not value) or value.startswith("YOUR_")


def available() -> bool:
    """True if the updater is configured and could run on this platform."""
    return (
        sys.platform == "win32"
        and not _placeholder(GITHUB_USER)
        and bool(EDDSA_PUBLIC_KEY)
    )


def _load_dll():
    """Load WinSparkle.dll from the bundle dir (frozen) or the dev tree, wiring
    up the argument types we use. Returns the handle or None."""
    global _dll
    if _dll is not None:
        return _dll
    if sys.platform != "win32":
        return None
    import ctypes

    dll_path = BUNDLE_DIR / "WinSparkle.dll"
    try:
        lib = ctypes.CDLL(str(dll_path))
    except OSError:
        return None

    # char* args are UTF-8 encoded; *_app_details takes wide (UTF-16) strings.
    lib.win_sparkle_set_appcast_url.argtypes = [ctypes.c_char_p]
    lib.win_sparkle_set_registry_path.argtypes = [ctypes.c_char_p]
    lib.win_sparkle_set_eddsa_public_key.argtypes = [ctypes.c_char_p]
    lib.win_sparkle_set_app_details.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
    ]
    lib.win_sparkle_set_automatic_check_for_updates.argtypes = [ctypes.c_int]
    lib.win_sparkle_set_update_check_interval.argtypes = [ctypes.c_int]
    _dll = lib
    return lib


def init() -> bool:
    """Configure and start WinSparkle. Enables the daily silent check.
    Returns True if the updater is now active. Safe to call unconditionally —
    a no-op when unavailable."""
    if not available():
        return False
    lib = _load_dll()
    if lib is None:
        return False
    try:
        lib.win_sparkle_set_appcast_url(APPCAST_URL.encode("utf-8"))
        lib.win_sparkle_set_registry_path(_REGISTRY_PATH.encode("utf-8"))
        lib.win_sparkle_set_eddsa_public_key(EDDSA_PUBLIC_KEY.encode("utf-8"))
        lib.win_sparkle_set_app_details("findash", "findash", __version__)
        lib.win_sparkle_set_update_check_interval(_CHECK_INTERVAL_SECONDS)
        # Enable automatic checks. On first run WinSparkle asks the user for
        # permission before the first silent check.
        lib.win_sparkle_set_automatic_check_for_updates(1)
        lib.win_sparkle_init()
        # Force a background check on THIS launch. win_sparkle_init()'s own
        # automatic check is throttled to the check interval (and a prior
        # manual check resets that timer), so it often stays silent on startup.
        # This explicit check ignores the interval: it shows the update dialog
        # whenever a newer version exists, and nothing when it doesn't (no
        # "you're up to date" popup every launch).
        lib.win_sparkle_check_update_without_ui()
        return True
    except Exception:
        return False


def check_now() -> None:
    """Manual 'Check for Updates…' — shows the WinSparkle UI, including a
    'you're up to date' message when there's nothing new. No-op if unavailable
    (the menu item explains why via ``unavailable_reason``)."""
    lib = _load_dll() if available() else None
    if lib is None:
        return
    try:
        lib.win_sparkle_check_update_with_ui()
    except Exception:
        pass


def cleanup() -> None:
    """Shut WinSparkle down cleanly (joins its worker thread). Call on exit."""
    if _dll is None:
        return
    try:
        _dll.win_sparkle_cleanup()
    except Exception:
        pass


def unavailable_reason() -> str:
    """Human-readable reason the updater is off, for the manual menu path."""
    if sys.platform != "win32":
        return "Automatic updates are only available on Windows."
    if _placeholder(GITHUB_USER):
        return "Updates aren't configured yet (no release feed set)."
    if not EDDSA_PUBLIC_KEY:
        return "Updates aren't configured yet (no signing key set)."
    if _load_dll() is None:
        return "Update component (WinSparkle) is missing from this build."
    return ""
