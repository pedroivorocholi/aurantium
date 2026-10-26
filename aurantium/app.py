"""Main window: dock manager, command bar, Panels menu, layout save/load."""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from collections.abc import Callable
from pathlib import Path

import PySide6QtAds as QtAds
from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QBitmap,
    QCursor,
    QIcon,
    QKeySequence,
    QPixmap,
    QRegion,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSystemTrayIcon,
    QWidget,
    QHBoxLayout,
)

from .alerts import AlertEngine
from .command_bar import CommandBar
from .datahub import DataHub
from .date_context import WINDOW_SPAN_DAYS, WINDOWS, DateContext, parse_iso
from . import motion
from .layout_store import LayoutStore
from .notifier import Notifier
from .title_bar import NativeFrame, TitleBar, frame_panel_window
from .components.fit_width import relax_min_widths
from .panel import Panel, PanelRegistry
from .paths import ASSETS_DIR, BUNDLE_DIR
from .presets import Preset, available_presets
from .symbol_context import DEFAULT_GROUP, GROUPS, SymbolContext
from .undo import UndoStack

LAYOUTS_DIR = BUNDLE_DIR / "layouts"


def _as_of_spec(arg: str) -> tuple:
    """Parse a /day argument into ``(anchor_iso, window_token)``.

    Accepts ``YYYY-MM-DD`` or ``START..END``. A range is converted to the
    midpoint plus the smallest window token that still covers it: the Day
    Brief is built around one anchor session, because a stat block that
    averaged several days would answer a different question than the one
    the user asked. Returns ``(None, "")`` for anything unparseable."""
    text = (arg or "").strip()
    if ".." in text:
        raw_a, _, raw_b = text.partition("..")
        a, b = parse_iso(raw_a), parse_iso(raw_b)
        if a is None or b is None:
            return None, ""
        if b < a:
            a, b = b, a
        mid = a + timedelta(days=(b - a).days // 2)
        span = (b - a).days + 1
        for token in WINDOWS:
            if WINDOW_SPAN_DAYS[token] >= span:
                return mid.isoformat(), token
        return mid.isoformat(), WINDOWS[-1]
    day = parse_iso(text)
    return (day.isoformat(), "1D") if day else (None, "")


# Shareable single-layout file (JSON inside). Import also accepts plain .json.
LAYOUT_EXT = ".aurantiumlayout"

# Bump when the serialized layout schema changes incompatibly. A layout tagged
# with a higher version was written by a newer aurantium and is not loaded.
CURRENT_LAYOUT_VERSION = 1

# Set once the first-run workspace chooser has been presented, so it is never
# offered twice — an empty auto-saved session is not proof of a first run.
WORKSPACE_CHOOSER_SHOWN_KEY = "workspaceChooser/shown"

# Name of the monitor the main window was last closed on (QScreen.name()).
MAIN_SCREEN_KEY = "window/screen"
#: Row height the wordmark is sized against (see _build_logo_label): the mark
#: comes out about 16px tall, quiet beside the 34px title bar.
LOGO_ROW_HEIGHT = 30


class _DockParentWatch(QObject):
    """Notes where a dock lives after it changes parent — i.e. after it was
    dropped somewhere — so a drag that ends off any drop target can put the
    panel back where it came from (see ``MainWindow._settle_new_window``)."""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        self._win = win

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.ParentChange:
            QTimer.singleShot(0, lambda d=obj: self._win._remember_home(d))
        return False


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Aurantium — personal terminal")
        # Windows: frameless, with one integrated title bar (title_bar.py);
        # everywhere else the native frame stays.
        self.setWindowFlags(NativeFrame.window_flags(self.windowFlags()))
        self.resize(1500, 900)
        self._instance_seq: dict[str, int] = {}
        self._docks: dict[str, QtAds.CDockWidget] = {}  # instance_id -> dock
        # panel-maximize (one panel fills its window) state
        self._maximize_actions: dict[str, QAction] = {}  # instance_id -> title-bar action
        self._maximized_instance: str | None = None
        self._pre_maximize_state = None  # QByteArray snapshot to restore on exit
        self._last_closed: dict | None = None  # for reopen-last-closed (Ctrl+Shift+T)
        self._loading_layout = False  # guard: mass-close during layout swap isn't a user close
        #: Called with (panels built, total) as apply_layout restores a
        #: workspace; startup points it at the splash card's progress bar.
        self.layout_progress: Callable[[int, int], None] | None = None
        self._dock_watch = _DockParentWatch(self)
        # Multi-screen: a panel (or a whole tab stack) dragged out past every
        # aurantium window becomes a window of its own, a full docking area
        # for a second monitor. instance_id -> the dock area it last lived in,
        # so a drag released inside a window but on no drop target returns
        # home instead of turning into a stray window.
        self._home_areas: dict[str, object] = {}
        self._pending_windows: set = set()  # made by a drag, not yet settled
        self._opening_windows = 0  # >0 while code (not a drag) creates windows
        self._tray_hidden_windows: list = []
        self._maximizing = False  # guard: hiding siblings is not a real close
        self._last_refresh_all = 0.0  # monotonic timestamp, debounces F5
        self.layout_store = LayoutStore()

        # -- command bar (Bloomberg command-line analog): type ticker, Enter
        bar = QWidget(self)
        bar.setObjectName("commandBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self._cmd = CommandBar(bar)
        self._cmd.setObjectName("commandInput")
        self._cmd.setPlaceholderText("Search a symbol or name (AAPL, nvidia, gold…) or type a /command")
        self._cmd.setAccessibleName("Symbol search")
        # A search glyph inside the field, where the SYMBOL label beside it
        # used to be: the label pushed the field off the title bar's centre.
        from . import icons
        from .theme import FG_MUTED

        self._cmd.addAction(
            icons.icon("search", FG_MUTED, icons.device_pixel_ratio(self)),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self._cmd.returnPressed.connect(self._on_command)
        from .components.symbol_search import SuggestField, shared_engine

        engine = shared_engine()
        engine.set_extra_symbols(self._watchlist_symbols)
        self._suggest = SuggestField(
            self._cmd,
            engine,
            on_pick=self._on_suggestion_picked,
            slash_completions=self._command_completions,
        )
        bl.addWidget(self._cmd, 1)
        self.setMenuWidget(self._build_title_bar(bar))

        # Docking behaviour: a panel opened from the menu arrives as its own
        # window; dragged into the layout it snaps in. Dragged out past the
        # edge of every aurantium window it becomes a window again (that is
        # how panels reach a second monitor), while a drag released inside a
        # window but off any drop target goes back where it was, so moving
        # panels around never leaves a stray window behind
        # (_settle_new_window). Live splitter resize + even splits for free
        # arranging.
        _cfg = QtAds.CDockManager
        for _flag in (
            _cfg.OpaqueSplitterResize,
            _cfg.FocusHighlighting,
            _cfg.EqualSplitOnInsertion,
            _cfg.MiddleMouseButtonClosesTab,
        ):
            _cfg.setConfigFlag(_flag, True)
        # Explicitly OFF: no double-click-to-float, no undock button.
        _cfg.setConfigFlag(_cfg.DoubleClickUndocksWidget, False)
        _cfg.setConfigFlag(_cfg.DockAreaHasUndockButton, False)
        # auto-hide: let panels be pinned to the window edges as slide-out tabs
        try:
            _cfg.setAutoHideConfigFlags(_cfg.DefaultAutoHideConfig)
        except Exception:
            pass
        self.dock_manager = QtAds.CDockManager(self)
        # QtAds sets its own stylesheet on the manager that outranks the global
        # one; apply our steel-blue docking chrome directly here so it wins.
        from .theme import ADS_STYLESHEET

        self.dock_manager.setStyleSheet(ADS_STYLESHEET)
        # a window holding several panels is titled after the app, not after
        # whichever panel happens to be first
        QtAds.CDockManager.setFloatingContainersTitle("Aurantium")
        self.dock_manager.floatingWidgetCreated.connect(self._on_window_created)
        self._register_dock_icons()

        self._build_menus()
        self._native_frame = NativeFrame(self, self._title_bar)
        from . import guide

        guide.open_guide_hook = self.show_guide  # every panel's ? opens here
        self._install_panel_maximize()
        self._install_refresh_all()
        self._install_search_shortcut()
        self._install_more_shortcuts()
        self._install_undo()
        self._install_tray()
        # The bottom status bar is gone (the taller top bar with the logo took
        # its height), so transient feedback goes to a floating notice card
        # instead — see notifier.py and self.notify().
        self.statusBar().setVisible(False)
        self._notifier = Notifier(self)

    # -- transient feedback --------------------------------------------------

    def notify(self, text: str, msecs: int = 4000, level: str = "info") -> None:
        """Report something that just happened — layout saved, undo, refresh,
        an unknown command, a fired alert — in the floating notice card.

        Every call site that used to write to the (now hidden) status bar comes
        through here. ``level="warn"`` tints it amber for "you asked for
        something and it didn't happen".
        """
        self._notifier.show_message(text, msecs, level)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        # keep the notice pinned to the bottom-right corner as the window
        # changes its size
        notifier = getattr(self, "_notifier", None)
        if notifier is not None:
            notifier.reposition()

    # -- native frame (Windows): see title_bar.py ----------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._native_frame.attach()
        self._title_bar.sync_window_state()

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._title_bar.sync_window_state()

    def nativeEvent(self, event_type, message):  # noqa: N802 (Qt override)
        frame = getattr(self, "_native_frame", None)
        answer = frame.handle(message) if frame is not None else None
        if answer is not None:
            return answer
        return super().nativeEvent(event_type, message)

    # -- panel maximize (one panel fills its window) --------------------------

    def _install_panel_maximize(self) -> None:
        """Set up the F11 hotkey, the Esc-to-restore shortcut, and cache the
        maximize/restore title-bar icons."""
        self._icon_maximize = self._chrome_icon("expand")
        self._icon_restore = self._chrome_icon("restore")

        f11 = QAction(self)
        # Letter-key alternates: many laptops (Lenovo, HP) send media keys
        # from the F-row unless Fn is held.
        f11.setShortcuts([QKeySequence(Qt.Key.Key_F11), QKeySequence("Ctrl+Shift+M")])
        f11.triggered.connect(self._toggle_maximize_focused)
        self.addAction(f11)

        # Esc restores; only live while a panel is maximized so it doesn't
        # swallow Escape elsewhere.
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.activated.connect(self._exit_maximize)
        self._esc_shortcut.setEnabled(False)

    def _install_refresh_all(self) -> None:
        """Set up the F5 hotkey: force-refresh every currently-subscribed
        topic (news, charts, quotes, financials — whatever's live)."""
        f5 = QAction(self)
        f5.setShortcuts([QKeySequence(Qt.Key.Key_F5), QKeySequence("Ctrl+R")])
        f5.triggered.connect(self._refresh_all)
        self.addAction(f5)

    def _install_search_shortcut(self) -> None:
        """Ctrl+F (Cmd+F on macOS): focus the search box and select its
        text so typing a new ticker overwrites the old one immediately."""
        find = QAction(self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self._focus_symbol_search)
        self.addAction(find)

    def _focus_symbol_search(self) -> None:
        self._cmd.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._cmd.selectAll()

    def _install_more_shortcuts(self) -> None:
        """Ctrl+W closes the focused panel; Ctrl+Shift+T reopens the last one you
        closed; Ctrl+1..4 sets the focused panel's link group (A/B/C/D)."""
        close_act = QAction(self)
        # explicit Ctrl+W — StandardKey.Close is Ctrl+F4 on Windows, not what
        # users expect for "close this panel".
        close_act.setShortcut(QKeySequence("Ctrl+W"))
        close_act.triggered.connect(self._close_focused_dock)
        self.addAction(close_act)

        reopen_act = QAction(self)
        reopen_act.setShortcut(QKeySequence("Ctrl+Shift+T"))
        reopen_act.triggered.connect(self._reopen_last_closed)
        self.addAction(reopen_act)

        for i in range(min(4, len(GROUPS))):
            act = QAction(self)
            act.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            act.triggered.connect(
                lambda _=False, idx=i: self._set_focused_link_group(idx)
            )
            self.addAction(act)

    def _install_undo(self) -> None:
        """Ctrl+Z: undo the most recent dashboard edit (chart drawings,
        watchlist changes, monitor-list edits, …) from the global UndoStack.
        When a text field is focused, Ctrl+Z stays its normal text undo."""
        undo_act = QAction(self)
        undo_act.setShortcut(QKeySequence.StandardKey.Undo)
        undo_act.triggered.connect(self._undo_last)
        self.addAction(undo_act)

    def _undo_last(self) -> None:
        # let a focused text box keep its own Ctrl+Z (command bar, filter boxes)
        fw = QApplication.focusWidget()
        if isinstance(fw, QLineEdit) and fw.isUndoAvailable():
            fw.undo()
            return
        label = UndoStack.instance().undo()
        if label:
            self.notify(f"Undid: {label}", 3000)
        else:
            self.notify("Nothing to undo.", 2500, level="warn")

    # -- system tray + price alerts ----------------------------------------

    def _install_tray(self) -> None:
        """Tray icon with Show/Hide/Quit, an opt-in 'Close to tray' toggle, and
        balloon notifications when a price alert fires. No-op where the platform
        has no system tray."""
        self._tray: QSystemTrayIcon | None = None
        self._force_quit = False
        # keep the alert engine alive + route its notifications to the tray even
        # if the Alerts panel is never opened
        self._alert_engine = AlertEngine.instance()
        self._alert_engine.alert_triggered.connect(self._on_alert_triggered)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon = QApplication.instance().windowIcon()
        if icon.isNull():
            ico = ASSETS_DIR / "icons" / "aurantium.ico"
            if ico.is_file():
                icon = QIcon(str(ico))
        if icon.isNull():
            icon = self.style().standardIcon(
                self.style().StandardPixmap.SP_ComputerIcon
            )
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("Aurantium")

        menu = QMenu()
        menu.addAction("Show").triggered.connect(self._show_from_tray)
        menu.addAction("Hide").triggered.connect(self.hide)
        menu.addSeparator()
        self._close_to_tray_act = menu.addAction("Close to tray")
        self._close_to_tray_act.setCheckable(True)
        self._close_to_tray_act.setChecked(
            QSettings().value("tray/close_to_tray", False, type=bool)
        )
        # shared setter keeps this in sync with the Settings-menu toggle
        self._close_to_tray_act.toggled.connect(self._set_close_to_tray)
        menu.addSeparator()
        menu.addAction("Quit Aurantium").triggered.connect(self._quit_app)
        self._tray_menu = menu  # keep a reference
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _close_to_tray_enabled(self) -> bool:
        # QSettings is the source of truth (both menu toggles write it); the
        # behavior only applies when a tray actually exists to hide into.
        return self._tray is not None and QSettings().value(
            "tray/close_to_tray", False, type=bool
        )

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.bring_to_front()

    def bring_to_front(self) -> None:
        """Restore, raise, and focus the window. Shared by the tray 'Show' action
        and the single-instance handler that fires when a second launch asks the
        already-running instance to surface (from the tray or from behind other
        windows) instead of opening a duplicate."""
        # also un-hides a window closed to the tray, at the size it had
        if self.windowState() & Qt.WindowState.WindowMaximized:
            self.showMaximized()
        else:
            self.showNormal()
        hidden, self._tray_hidden_windows = self._tray_hidden_windows, []
        for fw in hidden:
            try:
                fw.show()
            except RuntimeError:
                pass
        self.raise_()
        self.activateWindow()

    # -- launch -----------------------------------------------------------------

    def enter_maximized(self) -> None:
        """Open maximized on the monitor the window was last closed on. There
        is no full-screen mode: the window is normal or maximized, and the
        taskbar stays visible either way. (aurantium used to open in borderless
        full screen; it now opens maximized instead.)"""
        self._move_to_saved_screen()
        self.showMaximized()

    def _move_to_saved_screen(self) -> None:
        """Open on the monitor the main window was last closed on, when that
        monitor is still connected (panel windows keep their own monitor in
        the layout). Otherwise Windows picks, which is the primary screen."""
        name = QSettings().value(MAIN_SCREEN_KEY, "", type=str)
        if not name:
            return
        screen = next((s for s in QApplication.screens() if s.name() == name), None)
        if screen is None:
            return
        avail = screen.availableGeometry()
        self.move(avail.topLeft())
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(screen)

    def _quit_app(self) -> None:
        self._force_quit = True
        self.close()

    def _on_alert_triggered(self, message: str) -> None:
        if self._tray is not None:
            # pass the tray's own QIcon (not MessageIcon.Information) so the
            # Windows toast shows the Aurantium icon instead of the generic ⓘ
            self._tray.showMessage(
                "Aurantium — price alert", message,
                self._tray.icon(), 8000,
            )
        self.notify(f"⚠ Alert: {message}", 8000, level="warn")

    def _close_focused_dock(self) -> None:
        dock = self.dock_manager.focusedDockWidget()
        if dock is None and len(self._docks) == 1:
            dock = next(iter(self._docks.values()))
        if dock is not None:
            dock.closeDockWidget()

    def _set_focused_link_group(self, index: int) -> None:
        dock = self.dock_manager.focusedDockWidget()
        if dock is None or not (0 <= index < len(GROUPS)):
            return
        panel = dock.widget()
        if isinstance(panel, Panel):
            panel.set_link_group(GROUPS[index])
            self.notify(
                f"Panel linked to group {GROUPS[index]}", 2500
            )

    def _reopen_last_closed(self) -> None:
        info = self._last_closed
        if not info or not info.get("panel_id"):
            self.notify("No recently closed panel to reopen.", 3000, level="warn")
            return
        self._last_closed = None
        self.add_panel(
            info["panel_id"],
            link_group=info.get("link_group"),
            settings=info.get("settings"),
        )

    def _refresh_all(self) -> None:
        # debounce: ignore repeat presses within 1.5s of the last accepted one
        # (DataHub.request already skips in-flight topics; this is just
        # belt-and-braces against spamming the shortcut).
        now = time.monotonic()
        if now - self._last_refresh_all < 1.5:
            return
        self._last_refresh_all = now
        hub = DataHub.instance()
        topics = hub.subscribed_topics()
        hub.request(topics, force=True)
        self.notify(f"Refreshing {len(topics)} feeds…", 2500)

    def _chrome_icon(self, kind: str, color: str | None = None):
        """One glyph from the painted icon family, tinted for this window.

        The painting itself moved to ``icons.py`` when the family grew past the
        panel chrome — the list editor's row grip and delete affordance are
        drawn from the same six-glyph set now, and a private method on
        MainWindow is not somewhere a dialog can reach. What stays here is the
        part that is genuinely this window's business: the default colour per
        kind, and painting for *this window's* screen rather than the primary
        one, which matters on a multi-monitor desk with mixed scaling.

        ``restore`` keeps the amber tint as a "panel is maximized" state cue.
        """
        from . import icons
        from .theme import ACCENT, CHROME_TEXT_DIM

        col = color or (ACCENT if kind == "restore" else CHROME_TEXT_DIM)
        return icons.icon(kind, col, icons.device_pixel_ratio(self))

    def _register_dock_icons(self) -> None:
        """Replace QtAds' default title-bar icons (tab close, area close, area
        menu, auto-hide pin) with aurantium's crisp custom set so all panel chrome
        shares one icon language."""
        try:
            ip = QtAds.CDockManager.iconProvider()
            ip.registerCustomIcon(QtAds.TabCloseIcon, self._chrome_icon("close"))
            ip.registerCustomIcon(QtAds.DockAreaCloseIcon, self._chrome_icon("close"))
            ip.registerCustomIcon(QtAds.DockAreaMenuIcon, self._chrome_icon("menu"))
            ip.registerCustomIcon(QtAds.AutoHideIcon, self._chrome_icon("pin"))
        except Exception:
            pass  # icon provider is a nicety; never block startup on it

    # Panel maximize/restore is deliberately NOT animated.
    #
    # It used to fade the focal panel in from 50% opacity over 150ms. Three
    # things were wrong with that. It is reached from the keyboard (F11 to
    # maximize, Esc or F11 to restore) — keyboard actions are repeated all day
    # and any animation on them reads as lag on the exact frame the user is
    # watching. It faded the *data*: the thing being maximized is a chart or a
    # table the user just asked to see larger, and content being read should
    # not move or dim for style. And it did it through a
    # QGraphicsOpacityEffect, which forces Qt to render the whole panel to an
    # offscreen pixmap for the duration — the most expensive way to do the
    # least useful thing. Snapping straight to the maximized panel is both
    # faster and correct.

    def _toggle_maximize_focused(self) -> None:
        """F11: maximize the focused panel, or restore if already maximized."""
        if self._maximized_instance:
            self._exit_maximize()
            return
        dock = self.dock_manager.focusedDockWidget()
        iid = dock.objectName() if dock is not None else None
        if iid not in self._docks:
            if len(self._docks) == 1:
                iid = next(iter(self._docks))
            else:
                self.notify(
                    "Click a panel first, then press F11 to maximize it.",
                    4000,
                    level="warn",
                )
                return
        self._set_maximized(iid, True)

    def _set_maximized(self, instance_id: str, on: bool) -> None:
        target = self._docks.get(instance_id)
        if target is None:
            return
        window = self._lone_panel_window(target)
        if window is not None and not self._maximized_instance:
            # alone in its own window there is nothing to hide: F11 (or the
            # title-bar button) maximizes / restores that window instead
            if window.isMaximized():
                window.showNormal()
            else:
                window.showMaximized()
            self._sync_maximize_action(instance_id, False)
            return
        if not on:
            self._exit_maximize()
            return
        if self._maximized_instance == instance_id:
            return
        if self._maximized_instance:
            self._exit_maximize()  # switch: restore, then maximize the new one
        self._pre_maximize_state = self.dock_manager.saveState()
        self._maximizing = True
        # only within the panel's own window: maximizing on one monitor must
        # not empty the others
        home = target.dockContainer()
        try:
            for iid, dock in list(self._docks.items()):
                if iid != instance_id and dock.dockContainer() is home:
                    dock.toggleView(False)
        finally:
            self._maximizing = False
        target.toggleView(True)
        target.setAsCurrentTab()
        self._maximized_instance = instance_id
        self._esc_shortcut.setEnabled(True)
        self._sync_maximize_action(instance_id, True)
        self.notify(
            "Panel maximized — press F11 or Esc to restore.", 4000
        )

    @staticmethod
    def _lone_panel_window(dock):
        """The panel window ``dock`` is the only open panel in, else None."""
        container = dock.dockContainer()
        fw = container.floatingWidget() if container is not None else None
        if fw is None or len(container.openedDockWidgets()) != 1:
            return None
        return fw

    def _exit_maximize(self) -> None:
        if not self._maximized_instance:
            return
        iid = self._maximized_instance
        self._maximized_instance = None
        self._esc_shortcut.setEnabled(False)
        self._restore_pre_maximize()
        self._sync_maximize_action(iid, False)

    def _restore_pre_maximize(self) -> None:
        state, self._pre_maximize_state = self._pre_maximize_state, None
        if state is not None:
            self._opening_windows += 1
            try:
                self.dock_manager.restoreState(state)
            except Exception:
                pass
            finally:
                self._opening_windows -= 1

    def _sync_maximize_action(self, instance_id: str, is_max: bool) -> None:
        """Keep a panel's title-bar button in sync with its maximized state
        (icon, tooltip, checked). setChecked is programmatic and does not
        re-emit triggered, so this never recurses."""
        act = self._maximize_actions.get(instance_id)
        if act is None:
            return
        if act.isChecked() != is_max:
            act.setChecked(is_max)
        act.setIcon(self._icon_restore if is_max else self._icon_maximize)
        act.setText("Restore panel" if is_max else "Maximize panel")
        act.setToolTip(
            "Restore panel (F11)" if is_max else "Maximize panel (F11)"
        )

    def _on_dock_closed(self, instance_id: str) -> None:
        """A dock was closed — drop it, and if it was the maximized one, bring
        the rest of the layout back."""
        if self._maximizing:
            return  # sibling hidden for maximize, not actually closed
        # remember what was here so Ctrl+Shift+T can bring it back (capture
        # before the dock/panel is dropped and deleted)
        dock = self._docks.get(instance_id)
        if dock is not None and not self._loading_layout:
            panel = dock.widget()
            if isinstance(panel, Panel):
                try:
                    self._last_closed = {
                        "panel_id": panel.panel_id,
                        "link_group": panel.link_group,
                        "settings": panel.settings(),
                    }
                except Exception:
                    self._last_closed = {"panel_id": getattr(panel, "panel_id", "")}
        self._docks.pop(instance_id, None)
        self._maximize_actions.pop(instance_id, None)
        if self._maximized_instance == instance_id:
            self._maximized_instance = None
            self._esc_shortcut.setEnabled(False)
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._restore_pre_maximize)

    # -- chrome ----------------------------------------------------------------

    def _build_title_bar(self, bar: QWidget) -> QWidget:
        """The one title bar: logo, Panels and Settings icons, the symbol
        command bar centred, and (on Windows) the caption buttons."""
        logo = self._build_logo_label(self, LOGO_ROW_HEIGHT)
        self._title_bar = TitleBar(self, bar, logo)
        return self._title_bar

    def _build_logo_label(self, parent: QWidget, max_height: int) -> QLabel | None:
        """Wordmark right before the Panels menu, sized to fill ``max_height``
        (the menu bar's own height) with a little breathing room — never
        taller, so it can't grow the row. Themes never hot-swap (switching
        restarts the app — see ``_on_theme_selected``), so picking the
        light/dark asset once here is safe."""
        from .theme import current_theme

        name = (
            "aurantium_logo_ondark.png"
            if current_theme() == "dark"
            else "aurantium_logo.png"
        )
        logo_path = ASSETS_DIR / "images" / name
        if not logo_path.is_file():
            return None
        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            return None
        # The asset carries a wide transparent border (about a fifth of its
        # height above and below the ink), which pushed the mark well in from
        # the window corner whatever the QSS padding said. Trim to the ink so
        # placement is the stylesheet's alone.
        full_h = pixmap.height()
        ink = QRegion(QBitmap.fromImage(pixmap.toImage().createAlphaMask())).boundingRect()
        if not ink.isEmpty():
            pixmap = pixmap.copy(ink)
        # Scale in physical pixels and tag the result with the screen's device
        # pixel ratio — scaling to N logical px directly renders blurry on any
        # HiDPI display, since Qt then upsamples an already-tiny bitmap.
        dpr = QApplication.primaryScreen().devicePixelRatio() if QApplication.primaryScreen() else 1.0
        # the ink at the size it always rendered, when the border was included
        target_h_logical = max(1, round((max_height - 2) * pixmap.height() / full_h))
        scaled = pixmap.scaledToHeight(
            round(target_h_logical * dpr), Qt.TransformationMode.SmoothTransformation
        )
        scaled.setDevicePixelRatio(dpr)
        label = QLabel(parent)
        label.setObjectName("menuBarLogo")
        # centred on the row, so the wordmark sits on the menu titles' line
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setPixmap(scaled)
        return label

    def _build_menus(self) -> None:
        # Panels is the one primary top-level menu; everything else (theme,
        # toggles, layouts, API keys, help, quit) lives under Settings. Both
        # open from icon buttons on the title bar.
        self._m_panels = QMenu("&Panels", self)
        self._rebuild_panels_menu()

        self._build_settings_menu()
        self._title_bar.set_menus(self._m_panels, self._m_settings)

        # Alt+P / Alt+S, the mnemonics the text menus had
        for key, button in (
            ("Alt+P", self._title_bar.panels_button),
            ("Alt+S", self._title_bar.settings_button),
        ):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(button.showMenu)

    def _build_settings_menu(self) -> None:
        """The consolidated Settings menu — appearance, accessibility, layouts,
        data keys, help, and quit. Opens from the title bar beside Panels."""
        from .theme import THEMES, colorblind_enabled, current_theme

        m = QMenu("&Settings", self)
        self._m_settings = m

        # -- appearance: theme + color-blind mode ---------------------------
        theme_menu = m.addMenu("Theme")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        cur = current_theme()
        for name in THEMES:
            act = QAction(name.capitalize(), self)
            act.setCheckable(True)
            act.setChecked(name == cur)
            act.triggered.connect(
                lambda _=False, n=name: self._on_theme_selected(n)
            )
            self._theme_group.addAction(act)
            theme_menu.addAction(act)

        a_cb = QAction("Color-blind mode", self)
        a_cb.setCheckable(True)
        a_cb.setChecked(colorblind_enabled())  # set state before connecting
        a_cb.setToolTip(
            "Deuteranopia-safe up/down colors plus ▲/▼ direction marks"
        )
        a_cb.toggled.connect(self._on_colorblind_toggled)
        self._colorblind_act = a_cb
        m.addAction(a_cb)

        a_tray = QAction("Close to tray", self)
        a_tray.setCheckable(True)
        a_tray.setChecked(QSettings().value("tray/close_to_tray", False, type=bool))
        a_tray.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
        a_tray.setToolTip(
            "Keep Aurantium running in the system tray when the window is closed"
        )
        a_tray.toggled.connect(self._set_close_to_tray)
        self._settings_tray_act = a_tray
        m.addAction(a_tray)

        m.addSeparator()

        # -- curated starter workspaces (shipped presets) -------------------
        self._m_workspaces = m.addMenu("Workspaces")
        self._rebuild_workspaces_menu()

        # -- named layouts (save / load / import / export / reset) ----------
        self._m_layout = m.addMenu("Layout")
        self._rebuild_layout_menu()

        # -- news languages --------------------------------------------------
        a_langs = QAction("News Languages…", self)
        a_langs.setToolTip(
            "Which languages news headlines may appear in"
        )
        a_langs.triggered.connect(self._show_languages)
        m.addAction(a_langs)

        # -- optional data-source keys --------------------------------------
        a_apis = QAction("API Keys…", self)
        a_apis.triggered.connect(self._show_api_keys)
        m.addAction(a_apis)

        m.addSeparator()

        # -- help / updates / about -----------------------------------------
        a_guide = QAction("Keyboard Shortcuts && Guide…", self)
        a_guide.setShortcuts(
            QKeySequence.keyBindings(QKeySequence.StandardKey.HelpContents)
            + [QKeySequence("Ctrl+Shift+H")]
        )
        a_guide.triggered.connect(self._show_onboarding)
        m.addAction(a_guide)
        a_panel_guide = QAction("Guide to the Focused Panel…", self)
        a_panel_guide.setShortcuts([QKeySequence("Shift+F1"), QKeySequence("Ctrl+Shift+G")])
        a_panel_guide.triggered.connect(self._show_focused_panel_guide)
        m.addAction(a_panel_guide)
        a_update = QAction("Check for Updates…", self)
        a_update.triggered.connect(self._check_for_updates)
        m.addAction(a_update)
        a_about = QAction("About Aurantium", self)
        a_about.triggered.connect(self._show_about)
        m.addAction(a_about)

        m.addSeparator()

        a_quit = QAction("&Quit", self)
        a_quit.setShortcut(QKeySequence("Ctrl+Q"))
        # real quit even when 'Close to tray' is on (that only intercepts the X)
        a_quit.triggered.connect(self._quit_app)
        m.addAction(a_quit)

    def _on_colorblind_toggled(self, on: bool) -> None:
        """Persist the color-blind choice and restart (same as a theme switch,
        since the palette is chosen once at import)."""
        from .theme import colorblind_enabled, set_colorblind

        on = bool(on)
        if on == colorblind_enabled():
            return
        resp = QMessageBox.question(
            self,
            "Color-blind mode",
            f"{'Enable' if on else 'Disable'} color-blind mode?\n\nAurantium needs "
            "to restart to apply it — your workspace will be restored "
            "automatically.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            # undo the menu check without re-triggering this handler
            self._colorblind_act.blockSignals(True)
            self._colorblind_act.setChecked(colorblind_enabled())
            self._colorblind_act.blockSignals(False)
            return
        set_colorblind(on)
        self._restart_app()

    def _set_close_to_tray(self, on: bool) -> None:
        """Single source of truth for the 'close to tray' preference, kept in
        sync between the Settings menu item and the tray context menu."""
        on = bool(on)
        QSettings().setValue("tray/close_to_tray", on)
        for act in (
            getattr(self, "_close_to_tray_act", None),
            getattr(self, "_settings_tray_act", None),
        ):
            if act is not None and act.isChecked() != on:
                act.blockSignals(True)
                act.setChecked(on)
                act.blockSignals(False)

    def _show_languages(self, first_run: bool = False) -> None:
        """Open the reading-languages picker; on save, refresh every live topic
        so the news panels re-fetch under the new languages immediately. The
        provider's RSS cache is keyed by language set, so the refresh picks up
        the new feeds rather than replaying the old cached ones."""
        from . import languages
        from .language_dialog import prompt_languages

        if not prompt_languages(self, first_run=first_run):
            return
        self._refresh_all()
        names = ", ".join(languages.endonym(c) for c in languages.spoken_languages())
        self.notify(f"News languages: {names}", 5000)

    def maybe_prompt_languages(self) -> None:
        """First launch only: ask which languages to show news in. Gated by its
        own flag, so adding the feature to an existing install prompts once."""
        from . import languages

        if languages.has_chosen():
            return
        self._show_languages(first_run=True)

    def _show_api_keys(self) -> None:
        from .settings_dialog import ApiKeysDialog

        if ApiKeysDialog(self).exec():
            self.notify(
                "API keys saved — panels use them on their next refresh.", 5000
            )

    def maybe_prompt_api_keys(self) -> None:
        """On the first launch of a fresh install or a new version, offer the
        API-key connect dialog — but only when no key is connected yet, and
        only once per version."""
        from . import __version__
        from .settings_dialog import API_KEYS

        if any(os.environ.get(env) for env, *_ in API_KEYS):
            return
        settings = QSettings()
        if settings.value("apiKeysPrompt/version", "") == __version__:
            return
        settings.setValue("apiKeysPrompt/version", __version__)
        box = QMessageBox(self)
        box.setWindowTitle("Connect free data sources")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            "<b>Aurantium works out of the box</b> — quotes, charts, and news "
            "run on free keyless sources.<br><br>"
            "Connecting <b>free API keys</b> unlocks better sources:"
            "<ul>"
            "<li><b>Finnhub / Twelve Data</b> — real-time quotes</li>"
            "<li><b>FRED / EIA</b> — economic &amp; energy data</li>"
            "<li><b>NewsAPI.org</b> — richer news coverage</li>"
            "</ul>"
            "Each takes about a minute to set up. You can connect (or "
            "disconnect) anytime from <b>Settings ▸ API Keys…</b>"
        )
        connect = box.addButton(
            "Connect now…", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton("Maybe later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is connect:
            self._show_api_keys()

    def _check_for_updates(self) -> None:
        from . import updater

        if updater.available():
            updater.check_now()
        else:
            QMessageBox.information(
                self, "Check for Updates", updater.unavailable_reason()
            )

    def _show_onboarding(self) -> None:
        self.show_guide(None)

    def show_guide(self, section: str | None) -> None:
        """Open the F1 guide, on ``section``'s page when given (a panel id,
        an alias, or a static page id). Every panel's ``?`` comes here."""
        from .onboarding_dialog import OnboardingDialog

        OnboardingDialog(self, section).exec()

    def _show_focused_panel_guide(self) -> None:
        """Shift+F1: the guide page of the focused panel, or the guide's
        start page when no panel has focus."""
        dock = self.dock_manager.focusedDockWidget()
        panel = dock.widget() if dock is not None else None
        if isinstance(panel, Panel):
            panel.open_guide()
        else:
            self.show_guide(None)

    def maybe_show_onboarding(self) -> None:
        """First launch only (per QSettings flag): show the guide once."""
        from .onboarding_dialog import OnboardingDialog

        if OnboardingDialog.should_auto_show():
            OnboardingDialog(self).exec()

    def _show_about(self) -> None:
        from . import __version__

        QMessageBox.about(
            self,
            "About Aurantium",
            f"<b>Aurantium</b> — personal market terminal<br>"
            f"Version {__version__}<br><br>"
            "Data: Yahoo Finance / Google News (free, delayed).",
        )

    def _rebuild_workspaces_menu(self) -> None:
        """List the shipped presets. Built once, at menu-construction time;
        factored out so a future entitlement gate (Plan B) has a single call
        site to filter."""
        m = self._m_workspaces
        m.clear()
        # QMenu drops action tooltips unless it opts in, so without this the
        # per-preset descriptions set below never reach the screen.
        m.setToolTipsVisible(True)
        presets = available_presets()
        if not presets:
            placeholder = QAction("(no workspaces available)", self)
            placeholder.setEnabled(False)
            m.addAction(placeholder)
            return
        for preset in presets:
            act = QAction(preset.name, self)
            if preset.description:
                act.setToolTip(preset.description)
            act.triggered.connect(
                lambda _=False, p=preset: self._load_workspace(p)
            )
            m.addAction(act)

    def _load_workspace(self, preset: Preset) -> None:
        """Replace the current arrangement with a shipped preset. Matches the
        no-confirmation behaviour of loading a saved layout."""
        if self.apply_layout(preset.doc):
            self.notify(f"Workspace loaded: {preset.name}", 4000)

    def _rebuild_layout_menu(self) -> None:
        m = self._m_layout
        m.clear()

        a_save = QAction("Save Current Layout…", self)
        a_save.setShortcut(QKeySequence.StandardKey.Save)
        a_save.triggered.connect(self._save_named_layout)
        m.addAction(a_save)

        a_import = QAction("Import Layout File…", self)
        a_import.triggered.connect(self._import_layout_file)
        m.addAction(a_import)

        names = self.layout_store.names()
        m.addSeparator()
        if names:
            for name in names:
                act = QAction(name, self)
                act.triggered.connect(
                    lambda _=False, n=name: self._load_named_layout(n)
                )
                m.addAction(act)
            exp_menu = m.addMenu("Export Layout")
            for name in names:
                act = QAction(name, self)
                act.triggered.connect(
                    lambda _=False, n=name: self._export_named_layout(n)
                )
                exp_menu.addAction(act)
            del_menu = m.addMenu("Delete Layout")
            for name in names:
                act = QAction(name, self)
                act.triggered.connect(
                    lambda _=False, n=name: self._delete_named_layout(n)
                )
                del_menu.addAction(act)
        else:
            placeholder = QAction("(no saved layouts yet)", self)
            placeholder.setEnabled(False)
            m.addAction(placeholder)

        m.addSeparator()
        a_reset = QAction("Reset to Default Layout", self)
        a_reset.triggered.connect(self._reset_to_default)
        m.addAction(a_reset)

    # -- in-app named layout actions -----------------------------------------

    def _save_named_layout(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save Layout", "Layout name:"
        )
        name = name.strip()
        if not ok or not name:
            return
        if name in self.layout_store.names():
            resp = QMessageBox.question(
                self, "Overwrite layout",
                f"A layout named “{name}” already exists. Overwrite it?",
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
        self.layout_store.put(name, self.serialize_layout())
        self._rebuild_layout_menu()
        self.notify(f"Layout saved: {name}", 4000)

    def _load_named_layout(self, name: str) -> None:
        doc = self.layout_store.get(name)
        if doc and self.apply_layout(doc):
            self.notify(f"Layout loaded: {name}", 4000)

    def _delete_named_layout(self, name: str) -> None:
        resp = QMessageBox.question(
            self, "Delete layout", f"Delete the layout “{name}”?"
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.layout_store.delete(name)
            self._rebuild_layout_menu()
            self.notify(f"Layout deleted: {name}", 4000)

    def _reset_to_default(self) -> None:
        default = LAYOUTS_DIR / "default.json"
        if default.is_file():
            try:
                doc = json.loads(default.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                doc = None
            if isinstance(doc, dict) and self.apply_layout(doc):
                return
        self._open_core_four()

    def _rebuild_panels_menu(self) -> None:
        self._m_panels.clear()
        by_cat: dict[str, list] = {}
        for meta in PanelRegistry.all():
            by_cat.setdefault(meta.category, []).append(meta)
        for cat, metas in sorted(by_cat.items()):
            sub = self._m_panels.addMenu(cat)
            for meta in metas:
                act = QAction(meta.title, self)
                # a panel opened from the menu arrives as its own window,
                # so it never reshuffles the layout; drag it in to dock it
                act.triggered.connect(
                    lambda _=False, pid=meta.id: self.add_panel(pid, floating=True)
                )
                sub.addAction(act)

    def _on_theme_selected(self, name: str) -> None:
        from .theme import current_theme, set_theme

        if name == current_theme():
            return  # already active (e.g. re-clicking the checked item)
        resp = QMessageBox.question(
            self,
            "Switch theme",
            f"Switch to the {name} theme?\n\nAurantium needs to restart to apply "
            "it — your workspace will be restored automatically.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            self._sync_theme_actions()  # undo the menu selection
            return
        set_theme(name)
        self._restart_app()

    def _sync_theme_actions(self) -> None:
        from .theme import current_theme

        cur = current_theme()
        for act in self._theme_group.actions():
            act.setChecked(act.text().lower() == cur)

    def _restart_app(self) -> None:
        """Persist the workspace, then relaunch this app so the new theme takes
        effect. The fresh process restores the auto-saved layout on startup."""
        import sys

        from PySide6.QtCore import QProcess

        try:
            self.layout_store.set_last(self.serialize_layout())
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, [])
        else:
            QProcess.startDetached(sys.executable, ["-m", "aurantium"])
        self.close()

    def _on_command(self) -> None:
        raw = self._cmd.text().strip()
        if not raw:
            return
        self._cmd.push_history(raw)
        if raw.startswith("/"):
            self._run_slash_command(raw)
        else:
            # "AAPL 2026-03-14" -- symbol and as-of date in one entry. Only a
            # second token that actually parses as a date is treated this way;
            # anything else falls through to the plain symbol path unchanged.
            parts = raw.split()
            if len(parts) == 2 and parse_iso(parts[1]) is not None:
                SymbolContext.instance().set_symbol(
                    DEFAULT_GROUP, parts[0].upper(), source=self
                )
                self._set_as_of(parts[1])
            else:
                SymbolContext.instance().set_symbol(
                    DEFAULT_GROUP, raw.upper(), source=self
                )
        self._cmd.clear()

    def _set_as_of(self, arg: str) -> None:
        """Point the Day Brief at a date, opening one if none is on screen.

        ``arg`` is ``YYYY-MM-DD`` or ``START..END``. A range is expressed as an
        anchor plus the smallest window that covers it, because the Day Brief's
        model is an anchor day with a sweep around it -- the stat block has to
        describe one session to mean anything."""
        anchor, window = _as_of_spec(arg)
        if anchor is None:
            self.notify(f"Not a date: {arg}", 3000, level="warn")
            return
        self.open_day_brief(anchor, window, source=self)

    def open_day_brief(
        self,
        anchor: str,
        window: str | None = None,
        group: str = DEFAULT_GROUP,
        source=None,
    ) -> None:
        """Put a Day Brief on screen and point it at ``anchor``.

        **Every** gesture that names a date routes through here — the chart's
        events lane, the `D` key, the chart context menu and `/day` alike.
        Publishing to DateContext alone is not enough: a workspace with no Day
        Brief docked (which is every workspace saved before this panel existed)
        has nobody listening, so the date goes nowhere and the click reads as
        broken. The panel joins ``group`` so a brief opened from a group-B
        chart follows that chart rather than group A.
        """
        if parse_iso(anchor) is None:
            return
        if not any(
            getattr(d.widget(), "panel_id", "") == "day_brief"
            for d in self._docks.values()
        ):
            # Opened as its own window, not docked: wedging a brief into the
            # side of a tuned workspace steals width from every other panel to
            # show something the user will read once and close. Dragging it
            # onto a dock area still docks it if they want it to stay.
            self.add_panel("day_brief", link_group=group, floating=True)
        DateContext.instance().set_date(group, anchor, window, source=source)

    def _on_suggestion_picked(self, suggestion) -> None:
        """A suggestion row was accepted in the top bar: navigate immediately
        (Bloomberg-style, one action) and clear the bar for the next entry."""
        code = suggestion.code
        self._cmd.push_history(code)
        SymbolContext.instance().set_symbol(DEFAULT_GROUP, code, source=self)
        self._cmd.clear()

    def _command_completions(self) -> list[str]:
        """Slash-command candidates for the command bar: /add <panel>,
        /layout <name>, and the bare /save & /refresh verbs. (Symbols come
        from the suggestion engine, not this list.)"""
        items = [f"/add {m.id}" for m in PanelRegistry.all()]
        items += [f"/layout {name}" for name in self.layout_store.names()]
        items += ["/save", "/refresh", "/day"]
        return items

    def _watchlist_symbols(self) -> list[str]:
        """Every watchlist symbol currently on screen — the suggestion
        engine's instant local source for tickers the user already tracks."""
        symbols: set[str] = set()
        for dock in self._docks.values():
            widget = dock.widget()
            if getattr(widget, "panel_id", "") == "watchlist":
                symbols.update(getattr(widget, "_symbols", []) or [])
        return sorted(symbols)

    def _run_slash_command(self, raw: str) -> None:
        parts = raw[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd == "add":
            if arg in PanelRegistry.known_ids():
                self.add_panel(arg, floating=True)
                self.notify(f"Added panel: {arg}", 3000)
            else:
                self.notify(
                    f"Unknown panel: {arg or '(none)'}", 3000, level="warn"
                )
        elif cmd == "layout":
            if arg in self.layout_store.names():
                self._load_named_layout(arg)
            else:
                self.notify(
                    f"Unknown layout: {arg or '(none)'}", 3000, level="warn"
                )
        elif cmd == "save":
            if arg:
                self.layout_store.put(arg, self.serialize_layout())
                self._rebuild_layout_menu()
                self.notify(f"Layout saved: {arg}", 4000)
            else:
                self._save_named_layout()
        elif cmd == "refresh":
            self._refresh_all()
        elif cmd == "day":
            self._set_as_of(arg or date.today().isoformat())
        else:
            self.notify(f"Unknown command: /{cmd}", 3000, level="warn")

    # -- panel management --------------------------------------------------------

    # -- extra windows (multi-screen) -----------------------------------------

    def _remember_home(self, dock) -> None:
        """Record the dock area a panel now lives in. Skipped while the panel
        sits in a window a drag just made: that is not a home yet, and the
        settle step may send it back to the old one."""
        try:
            area = dock.dockAreaWidget()
            container = dock.dockContainer()
        except RuntimeError:  # deleted while the check was queued
            return
        if area is None:
            return
        fw = container.floatingWidget() if container is not None else None
        if fw is not None and fw in self._pending_windows:
            return
        self._home_areas[dock.objectName()] = area

    def _on_window_created(self, container) -> None:
        """Every new panel window gets the app's shortcuts; one a drag made
        is then checked for whether it should exist at all."""
        # Window-scoped shortcuts (F1, F5, F11, Ctrl+F, the menus' keys) only
        # fire in a window their action belongs to; without this a panel on a
        # second monitor would ignore every one of them.
        container.addActions(self._shortcut_actions())
        # the main window's bar (no command bar) instead of the grey native one
        frame_panel_window(
            container,
            self._build_logo_label(container, LOGO_ROW_HEIGHT),
            self._m_panels,
            self._m_settings,
        )
        if self._opening_windows:
            return  # a menu open or a layout restore placed it on purpose
        self._pending_windows.add(container)
        QTimer.singleShot(0, lambda c=container: self._settle_new_window(c))

    def _shortcut_actions(self) -> list:
        """Every main-window action that carries a keyboard shortcut."""
        found: list = []

        def walk(actions) -> None:
            for act in actions:
                if act.menu() is not None:
                    walk(act.menu().actions())
                elif act.shortcuts() and act not in found:
                    found.append(act)

        walk(self.actions())
        # The menus live on the title bar. Never self.menuBar() here: with no
        # QMenuBar it makes an empty one and installs it in place of the
        # title bar, deleting it.
        walk([self._m_panels.menuAction(), self._m_settings.menuAction()])
        return found

    def _settle_new_window(self, container) -> None:
        """A drag just tore panels off into ``container``. Released outside
        every aurantium window, that is the user making a new window (for a
        second monitor, say): keep it, on a screen. Released inside one but
        off any drop target, it was a move that missed: send the panels back
        to the area they came from, as a drag with nowhere to land always did."""
        self._pending_windows.discard(container)
        try:
            docks = list(container.dockContainer().dockWidgets())
        except RuntimeError:
            return  # already gone (dropped into a window before this ran)
        if not docks:
            return
        drop = QCursor.pos()
        hosts = [self] + [
            fw for fw in self.dock_manager.floatingWidgets()
            if fw is not container and fw.isVisible()
        ]
        host = next((w for w in hosts if w.frameGeometry().contains(drop)), None)
        if host is None:
            self._keep_on_screen(container)
            for d in docks:
                self._remember_home(d)
            return
        target = None
        for d in docks:
            area = self._home_areas.get(d.objectName())
            if self._area_alive(area, container):
                target = area
                break
        if target is None:
            where = self.dock_manager if host is self else host.dockContainer()
            target = where.dockAreaAt(drop)
        for d in docks:
            if self._area_alive(target, container):
                self.dock_manager.addDockWidget(
                    QtAds.DockWidgetArea.CenterDockWidgetArea, d, target
                )
            else:
                self.dock_manager.addDockWidget(QtAds.DockWidgetArea.RightDockWidgetArea, d)
                target = d.dockAreaWidget()
        docks[0].setAsCurrentTab()

    @staticmethod
    def _area_alive(area, container) -> bool:
        """``area`` still exists and is not inside the window being settled."""
        if area is None:
            return False
        try:
            import shiboken6

            if not shiboken6.isValid(area):
                return False
            owner = area.dockContainer()
            return owner is not None and owner.floatingWidget() is not container
        except RuntimeError:
            return False

    def _keep_on_screen(self, window) -> None:
        """Pull a window back onto a connected screen. A layout saved with a
        second monitor attached must not open its windows off in the void
        once that monitor is gone."""
        try:
            geo = window.frameGeometry()
        except RuntimeError:
            return
        if QApplication.screenAt(geo.center()) is not None:
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        w = min(geo.width(), avail.width())
        h = min(geo.height(), avail.height())
        window.resize(w, h)
        window.move(avail.center().x() - w // 2, avail.center().y() - h // 2)

    def _active_panel_window(self):
        """The panel window (not the main one) that has focus, if any."""
        active = QApplication.activeWindow()
        for fw in self.dock_manager.floatingWidgets():
            if fw is active:
                return fw
        return None

    def _place_new_window(self, container, size: tuple[int, int] = (760, 540)) -> None:
        """Centre a newly opened panel's window over the main window, stepping
        each one down-right so several opened in a row don't stack exactly."""
        if container is None:
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        w = min(size[0], int(avail.width() * 0.9))
        h = min(size[1], int(avail.height() * 0.9))
        floating_now = sum(
            1 for d in self._docks.values() if d.isFloating() or d.isInFloatingContainer()
        )
        step = 28 * (floating_now % 6)
        centre = self.geometry().center()
        x = centre.x() - w // 2 + step
        y = centre.y() - h // 2 + step
        x = min(max(avail.left(), x), avail.right() - w)
        y = min(max(avail.top(), y), avail.bottom() - h)
        try:
            container.resize(w, h)
            container.move(x, y)
        except Exception:  # pragma: no cover - geometry is best-effort
            pass

    def _place_floating(self, container, size: tuple[int, int] = (620, 700)) -> None:
        """Size and position a freshly floated panel.

        Parked against the right edge of the screen rather than centred: these
        windows open in response to a click on the chart, and landing on top of
        the chart would hide the very thing the user is asking about. Clamped
        to the available geometry so it can never open partly off-screen or
        taller than the display.
        """
        if container is None:
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        w = min(size[0], int(avail.width() * 0.9))
        h = min(size[1], int(avail.height() * 0.9))
        margin = 24
        x = max(avail.left(), avail.right() - w - margin)
        y = max(avail.top(), avail.center().y() - h // 2)
        try:
            container.resize(w, h)
            container.move(x, y)
        except Exception:  # pragma: no cover - geometry is best-effort
            pass

    def add_panel(
        self,
        panel_id: str,
        instance_id: str | None = None,
        link_group: str | None = None,
        settings: dict | None = None,
        area: QtAds.DockWidgetArea = QtAds.DockWidgetArea.CenterDockWidgetArea,
        target_instance: str | None = None,
        floating: bool = False,
        restore_floating: bool = False,
    ) -> QtAds.CDockWidget | None:
        """Create a panel dock. ``area`` is global unless ``target_instance``
        names an existing panel instance — then the new dock is placed
        relative to (or tabbed into, with CenterDockWidgetArea) that panel's
        dock area. Lets layouts build precise multi-column arrangements.

        ``floating`` opens the panel as its own window instead of docking it.
        Docking a new panel into a tuned layout costs every other panel width,
        so panels the user opens from the menu arrive floating. Any panel can
        be dragged into the layout and back out into a window of its own (see
        ``_settle_new_window``); a ``FLOATABLE`` class (the Day Brief) opens
        parked at the screen edge instead of centred.

        ``restore_floating`` is accepted for saved layouts and callers that
        pass it; every panel can float now, so it changes nothing."""
        # a retired id (e.g. "key_metrics") opens its replacement on the
        # matching tab; explicit settings win over the alias defaults
        panel_id, alias_settings = PanelRegistry.resolve(panel_id)
        if alias_settings:
            settings = {**alias_settings, **(settings or {})}
        meta = PanelRegistry.get(panel_id)
        if meta is None:
            return None
        if instance_id is None:
            self._instance_seq[panel_id] = self._instance_seq.get(panel_id, 0) + 1
            instance_id = f"{panel_id}#{self._instance_seq[panel_id]}"
        else:
            # keep the sequence counter ahead of restored ids
            try:
                n = int(instance_id.rsplit("#", 1)[1])
                self._instance_seq[panel_id] = max(
                    self._instance_seq.get(panel_id, 0), n
                )
            except (IndexError, ValueError):
                pass

        try:
            panel: Panel = meta.cls()
            panel.build()
            # one long label or combo must not hold the dock wide
            relax_min_widths(panel)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            # one broken panel must not blank the whole window — report it to
            # the user and skip; the invalid panel never reaches the dock manager
            self.notify(
                f"Couldn't open {meta.title}: {exc}", 6000, level="warn"
            )
            return None
        if link_group:
            panel.set_link_group(link_group)
        if settings:
            try:
                panel.restore(settings)
            except Exception:
                pass

        dock = QtAds.CDockWidget(self.dock_manager, meta.title)
        dock.setObjectName(instance_id)
        dock.setWidget(panel)
        # QtAds wraps the panel in a scroll area. Panels reflow to any width
        # (wrapping control rows, shrinking columns), so that wrapper only
        # ever scrolls vertically — a docked panel never scrolls sideways.
        wrapper = panel.parentWidget()
        while wrapper is not None and wrapper is not dock:
            if isinstance(wrapper, QScrollArea):
                wrapper.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                break
            wrapper = wrapper.parentWidget()
        dock.setFeature(QtAds.CDockWidget.DockWidgetDeleteOnClose, True)
        # FLOATABLE (read from the class, so a restored layout behaves the
        # same) now only decides where a new window of that panel opens.
        always_float = bool(getattr(meta.cls, "FLOATABLE", False))
        dock.setFeature(QtAds.CDockWidget.DockWidgetMovable, True)
        # every panel can become a window, so it can live on another monitor
        dock.setFeature(QtAds.CDockWidget.DockWidgetFloatable, True)
        # A drop always re-parents the dock (whatever QtAds signals do or
        # don't fire for that drop path): note where it landed.
        dock.installEventFilter(self._dock_watch)
        # Let a panel be dragged down to a small size — size from the content's
        # own minimum (near-zero) rather than its full size hint.
        dock.setMinimumSizeHintMode(
            QtAds.CDockWidget.eMinimumSizeHintMode.MinimumSizeHintFromContentMinimumSize
        )
        # maximize/restore button in the dock title bar (mirrors F11)
        max_act = QAction(self._icon_maximize, "Maximize panel", dock)
        max_act.setCheckable(True)
        max_act.setToolTip("Maximize panel (F11)")
        max_act.triggered.connect(
            lambda checked, iid=instance_id: self._set_maximized(iid, checked)
        )
        dock.setTitleBarActions([max_act])
        self._maximize_actions[instance_id] = max_act
        dock.closed.connect(lambda iid=instance_id: self._on_dock_closed(iid))
        target = self._docks.get(target_instance) if target_instance else None
        if floating:
            self._opening_windows += 1
            try:
                container = self.dock_manager.addDockWidgetFloating(dock)
            finally:
                self._opening_windows -= 1
            if always_float:
                self._place_floating(container)
            else:
                self._place_new_window(container)
        elif target is not None:
            self.dock_manager.addDockWidget(area, dock, target.dockAreaWidget())
        else:
            self.dock_manager.addDockWidget(area, dock)
        self._docks[instance_id] = dock

        # late join: sync to the group's current symbol
        sym = SymbolContext.instance().symbol(panel.link_group)
        if sym:
            panel._apply_symbol(sym)
        if not self._loading_layout:
            # Only for a panel the user just asked for. Restoring a saved
            # layout builds a dozen at once, and fading them all in would just
            # delay the data they opened the app to read.
            motion.fade_in_panel(panel)
        return dock

    # -- layout serialization (dict in / dict out) ---------------------------

    def serialize_layout(self) -> dict:
        """Capture the current arrangement + panel state as a plain dict."""
        panels = []
        for instance_id, dock in self._docks.items():
            panel = dock.widget()
            if not isinstance(panel, Panel):
                continue
            panels.append(
                {
                    "instance": instance_id,
                    "panel_id": panel.panel_id,
                    "link_group": panel.link_group,
                    "settings": panel.settings(),
                    "floating": bool(dock.isFloating() or dock.isInFloatingContainer()),
                }
            )
        # If a panel is maximized, the live arrangement has the others hidden.
        # Persist the pre-maximize snapshot instead so saved/auto-saved layouts
        # always capture the full arrangement.
        if self._maximized_instance and self._pre_maximize_state is not None:
            ads_state = self._pre_maximize_state
        else:
            ads_state = self.dock_manager.saveState()
        return {
            "version": 1,
            "panels": panels,
            "ads_state": bytes(ads_state.toHex()).decode(),
            "symbols": SymbolContext.instance().to_json(),
            "dates": DateContext.instance().to_json(),
        }

    def apply_layout(self, doc: dict) -> bool:
        """Rebuild panels and dock arrangement from a serialized layout dict."""
        if not isinstance(doc, dict):
            return False
        try:
            layout_version = int(doc.get("version", 1))
        except (TypeError, ValueError):
            layout_version = 1  # malformed version — treat as current, try to load
        if layout_version > CURRENT_LAYOUT_VERSION:
            self.notify(
                "This layout was saved by a newer version of Aurantium — "
                "update to load it.",
                6000,
                level="warn",
            )
            return False
        # drop any maximize state — we're rebuilding the whole arrangement
        self._maximized_instance = None
        self._pre_maximize_state = None
        self._esc_shortcut.setEnabled(False)
        UndoStack.instance().clear()  # undo history doesn't cross a layout swap
        self._loading_layout = True  # mass-close below isn't a user close
        try:
            for dock in list(self._docks.values()):
                dock.closeDockWidget()
        finally:
            self._loading_layout = False
        self._docks.clear()
        self._maximize_actions.clear()
        SymbolContext.instance().from_json(doc.get("symbols", {}))
        DateContext.instance().from_json(doc.get("dates", {}))
        from .panels.company import migrate_layout

        doc = migrate_layout(doc)
        # Still a restore, not a user adding panels: add_panel must not fade
        # each one in (the guard used to drop before this loop, so every
        # restored panel faded). The skeletons below carry the arrival instead.
        self._loading_layout = True
        specs = doc.get("panels", [])
        try:
            for done, spec in enumerate(specs, 1):
                self.add_panel(
                    spec.get("panel_id", ""),
                    instance_id=spec.get("instance"),
                    link_group=spec.get("link_group"),
                    settings=spec.get("settings"),
                    restore_floating=bool(spec.get("floating")),
                )
                if self.layout_progress is not None:
                    self.layout_progress(done, len(specs))
        finally:
            self._loading_layout = False
        state = doc.get("ads_state", "")
        if state:
            from PySide6.QtCore import QByteArray

            self._opening_windows += 1
            try:
                self.dock_manager.restoreState(QByteArray.fromHex(state.encode()))
            except Exception:
                pass
            finally:
                self._opening_windows -= 1
            for fw in self.dock_manager.floatingWidgets():
                self._keep_on_screen(fw)
                # full screen is gone; a panel window saved in it comes back
                # maximized on the same monitor
                if fw.isFullScreen():
                    fw.showMaximized()
        # Every panel is at its saved place now. Those still waiting on their
        # first data stand in as their own outline until it arrives (each
        # skeleton tracks its panel's size, so a later relayout is fine).
        for dock in self._docks.values():
            panel = dock.widget()
            if isinstance(panel, Panel):
                panel.show_skeleton()
        return True

    # -- layout file sharing (export a saved layout / import one) -------------

    def _export_named_layout(self, name: str) -> None:
        """Write a saved layout to a shareable ``.aurantiumlayout`` file."""
        doc = self.layout_store.get(name)
        if not doc:
            return
        payload = dict(doc)
        payload["name"] = name  # so the recipient's import knows what to call it
        fn, _ = QFileDialog.getSaveFileName(
            self,
            "Export layout",
            f"{name}{LAYOUT_EXT}",
            f"Aurantium layout (*{LAYOUT_EXT});;JSON (*.json)",
        )
        if not fn:
            return
        try:
            Path(fn).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export layout", f"Couldn't save:\n{exc}")
            return
        self.notify(f"Layout exported: {Path(fn).name}", 5000)

    def _import_layout_file(self) -> None:
        """Import a shared layout file into the saved layouts, then load it."""
        fn, _ = QFileDialog.getOpenFileName(
            self,
            "Import layout",
            "",
            f"Aurantium layout (*{LAYOUT_EXT} *.json);;All files (*)",
        )
        if not fn:
            return
        path = Path(fn)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(
                self, "Import layout", f"Couldn't read {path.name}:\n{exc}"
            )
            return
        if not isinstance(doc, dict) or "panels" not in doc:
            QMessageBox.warning(
                self,
                "Import layout",
                f"{path.name} isn't a valid Aurantium layout file.",
            )
            return
        name = str(doc.get("name") or path.stem).strip() or "Imported Layout"
        if name in self.layout_store.names():
            resp = QMessageBox.question(
                self,
                "Overwrite layout",
                f"A layout named “{name}” already exists. Overwrite it?",
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
        self.layout_store.put(name, doc)
        self._rebuild_layout_menu()
        self.apply_layout(doc)
        self.notify(f"Layout imported: {name}", 5000)

    def _open_core_four(self) -> None:
        self.add_panel("watchlist", area=QtAds.DockWidgetArea.LeftDockWidgetArea)
        self.add_panel("chart", area=QtAds.DockWidgetArea.CenterDockWidgetArea)
        self.add_panel("news", area=QtAds.DockWidgetArea.RightDockWidgetArea)
        self.add_panel("analyst", area=QtAds.DockWidgetArea.BottomDockWidgetArea)

    def default_startup(
        self, before_prompt: Callable[[], None] | None = None
    ) -> None:
        """Restore the auto-saved last session if there is one. A fresh install
        has none, so it offers the shipped workspaces instead of opening to an
        empty window. Dismissing the chooser keeps the empty workspace — the
        user adds panels from the Panels menu or loads a saved layout.

        The offer happens genuinely once. An empty session is not proof of a
        first run: ``closeEvent`` always auto-saves, and a workspace with
        nothing open serializes to ``"panels": []``. Gating on that alone
        would re-prompt at every launch — a modal nag with no way to dismiss
        it permanently — so a QSettings flag records that the chooser was
        presented and both conditions must hold.

        ``before_prompt``, if given, is called immediately before the chooser
        is shown — and only on that branch. The caller uses it to close the
        startup splash first, since the splash must otherwise stay up while
        the modal chooser dialog waits for the user, which reads as the app
        being stuck loading. On the restore branch the splash stays up for
        the caller's own subsequent close, since that path builds panels
        rather than asking a question."""
        last = self.layout_store.get_last()
        if isinstance(last, dict) and last.get("panels"):
            self.apply_layout(last)
            return
        settings = QSettings()
        if settings.value(WORKSPACE_CHOOSER_SHOWN_KEY, False, type=bool):
            return
        settings.setValue(WORKSPACE_CHOOSER_SHOWN_KEY, True)
        from .workspace_chooser import pick_workspace

        if before_prompt is not None:
            before_prompt()
        preset = pick_workspace(available_presets(), self)
        if preset is not None:
            self._load_workspace(preset)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Hide to the tray if the user opted in (unless Quit was chosen);
        otherwise auto-save the arrangement and really close."""
        if self._close_to_tray_enabled() and not self._force_quit:
            event.ignore()
            # panel windows on other monitors go to the tray with the app
            self._tray_hidden_windows = [
                fw for fw in self.dock_manager.floatingWidgets() if fw.isVisible()
            ]
            for fw in self._tray_hidden_windows:
                fw.hide()
            self.hide()
            if self._tray is not None:
                # QIcon overload → app icon in the toast, not the generic ⓘ
                self._tray.showMessage(
                    "Aurantium",
                    "Still running in the tray — right-click the icon to quit.",
                    self._tray.icon(), 4000,
                )
            return
        try:
            self.layout_store.set_last(self.serialize_layout())
        except Exception:
            pass
        screen = self.screen()
        if screen is not None:
            QSettings().setValue(MAIN_SCREEN_KEY, screen.name())
        try:
            DataHub.instance()._flush_cache()  # persist latest values for next launch
        except Exception:
            pass
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)
