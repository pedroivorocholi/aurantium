"""Entry point: ``python -m aurantium``."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from .paths import BUNDLE_DIR, EXT_DIR

APP_ID = "aurantium.terminal.desktop.1"

# Name of the local IPC endpoint used to enforce a single running instance.
# The first instance owns this server; later launches connect to it, ask it to
# surface its window, then exit instead of opening a duplicate.
_IPC_NAME = "aurantium.terminal.singleinstance"


def _running_instance_activated() -> bool:
    """Return True if another aurantium is already running. When it is, hand it a
    one-line 'activate' message over the local socket so it surfaces its window
    (from the tray or from behind other windows), and let this launch exit."""
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(_IPC_NAME)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"activate\n")
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(300)
    return True


def _listen_for_second_instance():
    """Own the single-instance lock by listening on the local server that later
    launches probe. Returns the server (the caller keeps it alive) or None if we
    couldn't listen."""
    from PySide6.QtNetwork import QLocalServer

    QLocalServer.removeServer(_IPC_NAME)  # clear a stale endpoint from a hard crash
    server = QLocalServer()
    if not server.listen(_IPC_NAME):
        return None
    return server


def _surface_on_second_instance(server, win) -> None:
    """Drain any pending connections from other launches and, if any arrived,
    bring the running window to the front."""
    surfaced = False
    while server.hasPendingConnections():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnectFromServer()
            surfaced = True
    if surfaced:
        win.bring_to_front()


# Splash card: aurantium_splash.png (640x360, logo + LOADING line + border) is
# both the bootloader splash image and the Qt card's bitmap, shown 1:1 at
# PHYSICAL pixel size — the exe manifest declares per-monitor DPI awareness
# (see aurantium.spec), so neither window is ever re-scaled by Windows and the
# Tk→Qt handoff is pixel-identical. The status line is repainted on a copy of
# the bitmap in physical coordinates, matching the baked text exactly.
_SPLASH_TEXT_Y = 262  # top of the status-line rect (physical px, under logo)
_SPLASH_AMBER = "#c8842a"  # theme ACCENT_DEEP


def _splash_pixmap(base, dpr: float, text: str):
    """The card bitmap with the status line reading ``text``: erase the band
    under the logo, redraw the message the same way make_splash baked it."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

    from .theme import MONO_FONT

    pix = QPixmap(base)  # copy; physical-pixel coordinates
    p = QPainter(pix)
    p.fillRect(QRect(1, _SPLASH_TEXT_Y - 6, pix.width() - 2, 34), QColor("#000000"))
    font = QFont(MONO_FONT, 8)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 115)
    p.setFont(font)
    p.setPen(QColor(_SPLASH_AMBER))
    p.drawText(
        QRect(0, _SPLASH_TEXT_Y, pix.width(), 24),
        Qt.AlignmentFlag.AlignHCenter,
        text,
    )
    p.end()
    # Physical-size bitmap + screen DPR = shown 1:1, no scaling anywhere.
    pix.setDevicePixelRatio(dpr)
    return pix


def _show_splash(app):
    """Show the logo card while providers/panels/window build.

    In a frozen build the bootloader's Tk splash is already up; this puts the
    identical Qt card directly over it and then retires it — the swap is
    invisible because the manifest-declared DPI awareness keeps both windows
    at the same unscaled geometry. Dev runs simply start here. Returns the
    splash (or None without the image); update its status line with
    :func:`_splash_message`."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QSplashScreen

    base = QPixmap(str(BUNDLE_DIR / "aurantium_splash.png"))
    if base.isNull():
        return None
    screen = app.primaryScreen()
    dpr = screen.devicePixelRatio() if screen is not None else 1.0

    # Stay-on-top is load-bearing: without it the splash can open BEHIND the
    # foreground app (Windows only grants activation to the focused process
    # tree), leaving the load looking like a blank gap.
    splash = QSplashScreen(
        _splash_pixmap(base, dpr, "LOADING"), Qt.WindowType.WindowStaysOnTopHint
    )
    splash._aurantium_base = base  # for _splash_message repaints
    splash._aurantium_dpr = dpr
    splash.show()
    splash.raise_()
    app.processEvents()  # paint it now, before the heavy imports start

    # Qt copy is up — retire the bootloader's Tk splash underneath.
    try:
        import pyi_splash  # exists only inside a frozen build with a Splash target

        pyi_splash.close()
    except Exception:
        pass
    app.processEvents()
    return splash


def _splash_message(splash, text: str) -> None:
    """Advance the splash status line (no-op when the splash isn't up)."""
    if splash is None:
        return
    splash.setPixmap(
        _splash_pixmap(splash._aurantium_base, splash._aurantium_dpr, text)
    )
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()


def _close_splash(splash, win) -> None:
    """Drop the splash now that the window is populated."""
    if splash is not None:
        splash.finish(win)


def _set_windows_app_id() -> None:
    """Give Windows an explicit AppUserModelID so the taskbar treats aurantium
    as its own application — its own icon, its own grouping — instead of
    folding it into the generic ``pythonw.exe`` host process."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # pragma: no cover - cosmetic only
        pass


def _install_crash_logging() -> None:
    """Always record unhandled exceptions to a size-capped ``aurantium.log`` next to
    the app, so a crash leaves a trace even without AURANTIUM_DEBUG. Additive — it
    doesn't redirect normal stdout/stderr. PySide6 routes unhandled slot
    exceptions through ``sys.excepthook``, so Qt-callback crashes are captured too."""
    import threading
    import traceback as _tb

    log_path = EXT_DIR / "aurantium.log"

    def _write(header: str, text: str) -> None:
        try:
            if log_path.exists() and log_path.stat().st_size > 512 * 1024:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-256 * 1024:]
                log_path.write_text(tail, encoding="utf-8")
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n{header}\n{text}\n")
        except OSError:
            pass

    def _excepthook(exc_type, exc, tb) -> None:
        _write(
            "=== unhandled exception ===",
            "".join(_tb.format_exception(exc_type, exc, tb)),
        )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _excepthook

    _default_thread_hook = threading.excepthook

    def _thread_hook(args) -> None:
        _write(
            "=== unhandled thread exception ===",
            "".join(
                _tb.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback
                )
            ),
        )
        _default_thread_hook(args)  # keep default stderr behavior / SystemExit handling

    try:
        threading.excepthook = _thread_hook
    except Exception:
        pass


def _maybe_capture_output() -> None:
    """A frozen build has no console. When AURANTIUM_DEBUG is set, tee stdout/err
    to a log next to the exe so tracebacks (incl. panel build failures) surface."""
    if os.environ.get("AURANTIUM_DEBUG"):
        try:
            f = open(EXT_DIR / "aurantium_stderr.log", "w", encoding="utf-8")
            sys.stdout = f
            sys.stderr = f
        except OSError:
            pass


def _migrate_legacy_identity(app) -> None:
    """One-time carry-over from the app's old name, ``findash`` (through
    v1.4.3), to ``aurantium``. QSettings and QStandardPaths key off the Qt
    app/organization name, so renaming it would otherwise strand existing
    installs' saved layouts, alerts, theme, and history under the old
    registry/config location. Best-effort and silent — never blocks startup."""
    try:
        from PySide6.QtCore import QSettings, QStandardPaths

        app.setOrganizationName("findash")
        app.setApplicationName("findash")
        legacy_settings = QSettings()
        legacy_keys = legacy_settings.allKeys()
        legacy_config = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation
        )

        app.setOrganizationName("aurantium")
        app.setApplicationName("aurantium")
        settings = QSettings()
        if legacy_keys and not settings.allKeys():
            for key in legacy_keys:
                settings.setValue(key, legacy_settings.value(key))
            settings.sync()

        config_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation
        )
        if legacy_config and config_dir and legacy_config != config_dir:
            import shutil
            from pathlib import Path

            old_dir, new_dir = Path(legacy_config), Path(config_dir)
            if old_dir.is_dir() and not new_dir.exists():
                shutil.copytree(old_dir, new_dir)
    except Exception:  # pragma: no cover - best-effort, never fatal
        pass


def main() -> int:
    _maybe_capture_output()
    _install_crash_logging()
    load_dotenv(EXT_DIR / ".env")
    _set_windows_app_id()

    from PySide6.QtWidgets import QApplication  # heavy import — Tk splash covers it

    app = QApplication(sys.argv)
    # Qt card over the (still-showing) bootloader splash, then swap — the exe
    # manifest's DPI awareness keeps both at identical geometry, so the load
    # shows one continuous centered card from click to dashboard.
    splash = _show_splash(app)

    _migrate_legacy_identity(app)

    # Single instance: if aurantium is already running, tell it to surface its
    # window and exit before building anything. Otherwise claim the lock — done
    # early so a rapid double-launch can't slip a second window through the gap.
    if _running_instance_activated():
        return 0
    ipc_server = _listen_for_second_instance()

    from PySide6.QtGui import QIcon

    icon_path = BUNDLE_DIR / "aurantium.ico"
    app_icon = QIcon(str(icon_path)) if icon_path.is_file() else None
    if app_icon is not None:
        app.setWindowIcon(app_icon)

    from .theme import apply_theme

    apply_theme(app)

    # Providers first (so panels' initial subscriptions resolve), then panels.
    _splash_message(splash, "LOADING · PROVIDERS")
    from .providers import register_all_providers

    register_all_providers()

    _splash_message(splash, "LOADING · PANELS")
    from .panel import PanelRegistry, discover_panels

    # Built-in panels load as a package (frozen-safe); a user's optional
    # ``user_panels`` folder next to the app is scanned by file path.
    errors = discover_panels(
        [EXT_DIR / "user_panels"], packages=("aurantium.panels",)
    )
    for err in errors:
        print(f"[aurantium] panel failed to load:\n{err}", file=sys.stderr)

    _splash_message(splash, "LOADING · WORKSPACE")
    from .app import MainWindow

    win = MainWindow()
    if app_icon is not None:
        win.setWindowIcon(app_icon)

    # Now that the window exists, route second-launch pings to it. Drain once up
    # front in case a near-simultaneous launch connected during startup.
    if ipc_server is not None:
        ipc_server.newConnection.connect(
            lambda: _surface_on_second_instance(ipc_server, win)
        )
        _surface_on_second_instance(ipc_server, win)
    # launch in borderless full screen (covers the taskbar); toggle off with
    # Settings ▸ Full Screen / Shift+F11, which drops back to a maximized window.
    # The 1500x900 set in MainWindow stays as the restore size.
    win.enter_fullscreen()

    _splash_message(splash, "LOADING · LAYOUT")
    startup_err = ""
    try:
        # On the no-saved-session branch, default_startup shows a modal
        # first-run chooser. The splash is always-on-top and would otherwise
        # sit over that dialog (or at least make the app look stuck loading
        # while it's actually waiting on the user), so close it right before
        # the prompt appears. On the restore branch this callback never
        # fires, and the splash stays up until the unconditional close below.
        win.default_startup(before_prompt=lambda: _close_splash(splash, win))
    except Exception:
        import traceback as _tb

        startup_err = _tb.format_exc()

    # Panels are built and the layout restored — hand off from splash to window.
    # Harmless no-op if the no-session branch already closed it above:
    # _close_splash guards on `splash is not None`, not on prior-close state.
    _close_splash(splash, win)

    if os.environ.get("AURANTIUM_DEBUG") or startup_err or errors:
        try:
            default_json = BUNDLE_DIR / "layouts" / "default.json"
            (EXT_DIR / "aurantium_startup.log").write_text(
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

    # First run: pick reading languages, show the onboarding guide once, then
    # offer the API-keys dialog (all no-ops after first launch / once keys are
    # connected). Delayed so the window paints first; run sequentially since
    # each dialog is modal. Languages come first — it's the only one that
    # changes what the panels behind the dialog will show.
    from PySide6.QtCore import QTimer

    def _first_run_dialogs() -> None:
        win.maybe_prompt_languages()
        win.maybe_show_onboarding()
        win.maybe_prompt_api_keys()

    QTimer.singleShot(600, _first_run_dialogs)

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
