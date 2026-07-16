"""Entry point: ``python -m findash``."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from .paths import BUNDLE_DIR, EXT_DIR

APP_ID = "findash.terminal.desktop.1"


def _set_windows_app_id() -> None:
    """Give Windows an explicit AppUserModelID so the taskbar treats findash
    as its own application — its own icon, its own grouping — instead of
    folding it into the generic ``pythonw.exe`` host process."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # pragma: no cover - cosmetic only
        pass


def _maybe_capture_output() -> None:
    """A frozen build has no console. When FINDASH_DEBUG is set, tee stdout/err
    to a log next to the exe so tracebacks (incl. panel build failures) surface."""
    if os.environ.get("FINDASH_DEBUG"):
        try:
            f = open(EXT_DIR / "findash_stderr.log", "w", encoding="utf-8")
            sys.stdout = f
            sys.stderr = f
        except OSError:
            pass


def main() -> int:
    _maybe_capture_output()
    load_dotenv(EXT_DIR / ".env")
    _set_windows_app_id()

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("findash")
    app.setOrganizationName("findash")

    from PySide6.QtGui import QIcon

    icon_path = BUNDLE_DIR / "findash.ico"
    app_icon = QIcon(str(icon_path)) if icon_path.is_file() else None
    if app_icon is not None:
        app.setWindowIcon(app_icon)

    from .theme import apply_theme

    apply_theme(app)

    # Providers first (so panels' initial subscriptions resolve), then panels.
    from .providers import register_all_providers

    register_all_providers()

    from .panel import PanelRegistry, discover_panels

    # Built-in panels load as a package (frozen-safe); a user's optional
    # ``user_panels`` folder next to the app is scanned by file path.
    errors = discover_panels(
        [EXT_DIR / "user_panels"], packages=("findash.panels",)
    )
    for err in errors:
        print(f"[findash] panel failed to load:\n{err}", file=sys.stderr)

    from .app import MainWindow

    win = MainWindow()
    if app_icon is not None:
        win.setWindowIcon(app_icon)
    win.show()

    startup_err = ""
    try:
        win.default_startup()
    except Exception:
        import traceback as _tb

        startup_err = _tb.format_exc()

    if os.environ.get("FINDASH_DEBUG") or startup_err or errors:
        try:
            default_json = BUNDLE_DIR / "layouts" / "default.json"
            (EXT_DIR / "findash_startup.log").write_text(
                f"frozen: {getattr(sys, 'frozen', False)}\n"
                f"panels registered: {len(PanelRegistry.all())}\n"
                f"BUNDLE_DIR: {BUNDLE_DIR}\n"
                f"default.json exists: {default_json.is_file()}\n"
                f"docks created: {len(win._docks)}\n\n"
                f"discover errors:\n" + "\n".join(errors) + "\n\n"
                f"startup error:\n" + startup_err,
                encoding="utf-8",
            )
        except OSError:
            pass

    # Auto-update: silent daily check via WinSparkle (Windows only; a no-op if
    # the updater isn't configured/available). Cleaned up after the event loop.
    from . import updater

    updater.init()
    try:
        return app.exec()
    finally:
        updater.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
