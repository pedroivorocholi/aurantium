"""Main window: dock manager, command bar, Panels menu, layout save/load."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import PySide6QtAds as QtAds
from PySide6.QtCore import Qt, QSettings
from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
    QHBoxLayout,
)

from .alerts import AlertEngine
from .command_bar import CommandBar
from .datahub import DataHub
from .layout_store import LayoutStore
from .panel import Panel, PanelRegistry
from .paths import BUNDLE_DIR
from .symbol_context import DEFAULT_GROUP, GROUPS, SymbolContext
from .undo import UndoStack

LAYOUTS_DIR = BUNDLE_DIR / "layouts"

# Shareable single-layout file (JSON inside). Import also accepts plain .json.
LAYOUT_EXT = ".findashlayout"

# Bump when the serialized layout schema changes incompatibly. A layout tagged
# with a higher version was written by a newer findash and is not loaded.
CURRENT_LAYOUT_VERSION = 1


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("findash — personal terminal")
        self.resize(1500, 900)
        self._instance_seq: dict[str, int] = {}
        self._docks: dict[str, QtAds.CDockWidget] = {}  # instance_id -> dock
        # full-screen (maximize-in-window) state
        self._maximize_actions: dict[str, QAction] = {}  # instance_id -> title-bar action
        self._maximized_instance: str | None = None
        self._pre_maximize_state = None  # QByteArray snapshot to restore on exit
        self._last_closed: dict | None = None  # for reopen-last-closed (Ctrl+Shift+T)
        self._loading_layout = False  # guard: mass-close during layout swap isn't a user close
        self._maximizing = False  # guard: hiding siblings is not a real close
        self._last_refresh_all = 0.0  # monotonic timestamp, debounces F5
        self.layout_store = LayoutStore()

        # -- command bar (Bloomberg command-line analog): type ticker, Enter
        bar = QWidget(self)
        bar.setObjectName("commandBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 4, 10, 4)
        bl.setSpacing(10)
        lbl = QLabel("SYMBOL", bar)
        lbl.setObjectName("commandLabel")
        self._cmd = CommandBar(bar, completions=self._command_completions)
        self._cmd.setObjectName("commandInput")
        self._cmd.setPlaceholderText(
            "Ticker (AAPL) or a /command — /add /layout /save /refresh · ↑↓ history · Tab completes"
        )
        self._cmd.returnPressed.connect(self._on_command)
        bl.addWidget(lbl)
        bl.addWidget(self._cmd, 1)
        self.setMenuWidget(self._wrap_menu_and_bar(bar))

        # Docking behaviour: panels move and snap into place, but never tear off
        # into free-floating windows (floating is disabled per-panel in
        # add_panel). Live splitter resize + even splits for free arranging.
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
        self._register_dock_icons()

        self._build_menus()
        self._install_fullscreen()
        self._install_refresh_all()
        self._install_search_shortcut()
        self._install_more_shortcuts()
        self._install_undo()
        self._install_tray()
        self.statusBar().showMessage(
            "Click any ticker — every linked panel follows. Data: Yahoo Finance/Google News (free, delayed)."
        )

    # -- full screen (maximize one panel in the window) ----------------------

    def _install_fullscreen(self) -> None:
        """Set up the F11 hotkey, the Esc-to-restore shortcut, and cache the
        maximize/restore title-bar icons."""
        self._icon_maximize = self._chrome_icon("expand")
        self._icon_restore = self._chrome_icon("restore")

        f11 = QAction(self)
        f11.setShortcut(QKeySequence(Qt.Key.Key_F11))
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
        f5.setShortcut(QKeySequence(Qt.Key.Key_F5))
        f5.triggered.connect(self._refresh_all)
        self.addAction(f5)

    def _install_search_shortcut(self) -> None:
        """Ctrl+F (Cmd+F on macOS): focus the SYMBOL command bar and select its
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
            self.statusBar().showMessage(f"Undid: {label}", 3000)
        else:
            self.statusBar().showMessage("Nothing to undo.", 2500)

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
            ico = BUNDLE_DIR / "findash.ico"
            if ico.is_file():
                icon = QIcon(str(ico))
        if icon.isNull():
            icon = self.style().standardIcon(
                self.style().StandardPixmap.SP_ComputerIcon
            )
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("findash")

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
        menu.addAction("Quit findash").triggered.connect(self._quit_app)
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
        self.showNormal()  # also un-hides a window closed to the tray
        self.raise_()
        self.activateWindow()

    def _quit_app(self) -> None:
        self._force_quit = True
        self.close()

    def _on_alert_triggered(self, message: str) -> None:
        if self._tray is not None:
            self._tray.showMessage(
                "findash — price alert", message,
                QSystemTrayIcon.MessageIcon.Information, 8000,
            )
        self.statusBar().showMessage(f"⚠ Alert: {message}", 8000)

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
            self.statusBar().showMessage(
                f"Panel linked to group {GROUPS[index]}", 2500
            )

    def _reopen_last_closed(self) -> None:
        info = self._last_closed
        if not info or not info.get("panel_id"):
            self.statusBar().showMessage("No recently closed panel to reopen.", 3000)
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
        self.statusBar().showMessage(f"Refreshing {len(topics)} feeds…", 2500)

    def _chrome_icon(self, kind: str, color: str | None = None):
        """The panel-chrome icon set — close, maximize, restore, menu, pin —
        drawn in one consistent style (antialiased, 1.4px round-cap strokes,
        device-pixel-ratio aware) so every title-bar glyph reads as one family
        and stays hairline-sharp on HiDPI displays. `restore` keeps the amber
        tint as a 'panel is maximized' state cue."""
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

        from .theme import ACCENT, CHROME_TEXT_DIM

        col = color or (ACCENT if kind == "restore" else CHROME_TEXT_DIM)
        dpr = self.devicePixelRatioF() or 1.0
        px = QPixmap(round(16 * dpr), round(16 * dpr))
        px.setDevicePixelRatio(dpr)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(col))
        pen.setWidthF(1.4)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if kind == "close":
            p.drawLine(QPointF(5, 5), QPointF(11, 11))
            p.drawLine(QPointF(11, 5), QPointF(5, 11))
        elif kind == "expand":  # diagonal arrows to opposite corners → fill window
            p.drawLine(QPointF(7, 7), QPointF(4, 4))
            p.drawLine(QPointF(4, 4), QPointF(4, 7))
            p.drawLine(QPointF(4, 4), QPointF(7, 4))
            p.drawLine(QPointF(9, 9), QPointF(12, 12))
            p.drawLine(QPointF(12, 12), QPointF(12, 9))
            p.drawLine(QPointF(12, 12), QPointF(9, 12))
        elif kind == "maximize":
            p.drawRect(4, 4, 8, 8)
        elif kind == "restore":
            p.drawRect(QRectF(5.5, 3.5, 6, 6))  # back square (upper-right)
            p.drawRect(QRectF(3.5, 5.5, 6, 6))  # front square (lower-left)
        elif kind == "menu":
            for y in (5, 8, 11):
                p.drawLine(QPointF(4, y), QPointF(12, y))
        elif kind == "pin":
            p.drawEllipse(QPointF(8, 6), 2.6, 2.6)
            p.drawLine(QPointF(8, 8.6), QPointF(8, 12.5))
        p.end()
        return QIcon(px)

    def _register_dock_icons(self) -> None:
        """Replace QtAds' default title-bar icons (tab close, area close, area
        menu, auto-hide pin) with findash's crisp custom set so all panel chrome
        shares one icon language."""
        try:
            ip = QtAds.CDockManager.iconProvider()
            ip.registerCustomIcon(QtAds.TabCloseIcon, self._chrome_icon("close"))
            ip.registerCustomIcon(QtAds.DockAreaCloseIcon, self._chrome_icon("close"))
            ip.registerCustomIcon(QtAds.DockAreaMenuIcon, self._chrome_icon("menu"))
            ip.registerCustomIcon(QtAds.AutoHideIcon, self._chrome_icon("pin"))
        except Exception:
            pass  # icon provider is a nicety; never block startup on it

    def _fade_in(self, widget) -> None:
        """A very slight fade-in on the focal panel so maximize/restore eases
        rather than snapping. Partial start opacity keeps it subtle; the effect
        is removed when done so it never affects normal rendering."""
        if widget is None:
            return
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", self)
        anim.setDuration(150)
        anim.setStartValue(0.5)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda w=widget: w.setGraphicsEffect(None))
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

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
                self.statusBar().showMessage(
                    "Click a panel first, then press F11 to maximize it.", 4000
                )
                return
        self._set_maximized(iid, True)

    def _set_maximized(self, instance_id: str, on: bool) -> None:
        target = self._docks.get(instance_id)
        if target is None:
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
        try:
            for iid, dock in list(self._docks.items()):
                if iid != instance_id:
                    dock.toggleView(False)
        finally:
            self._maximizing = False
        target.toggleView(True)
        target.setAsCurrentTab()
        self._fade_in(target.widget())
        self._maximized_instance = instance_id
        self._esc_shortcut.setEnabled(True)
        self._sync_maximize_action(instance_id, True)
        self.statusBar().showMessage(
            "Panel maximized — press F11 or Esc to restore.", 4000
        )

    def _exit_maximize(self) -> None:
        if not self._maximized_instance:
            return
        iid = self._maximized_instance
        self._maximized_instance = None
        self._esc_shortcut.setEnabled(False)
        self._restore_pre_maximize()
        self._sync_maximize_action(iid, False)
        dock = self._docks.get(iid)
        if dock is not None:
            self._fade_in(dock.widget())

    def _restore_pre_maximize(self) -> None:
        state, self._pre_maximize_state = self._pre_maximize_state, None
        if state is not None:
            try:
                self.dock_manager.restoreState(state)
            except Exception:
                pass

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

    def _wrap_menu_and_bar(self, bar: QWidget) -> QWidget:
        """Stack the menu bar and the symbol command bar."""
        from PySide6.QtWidgets import QMenuBar, QVBoxLayout

        holder = QWidget(self)
        vl = QVBoxLayout(holder)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        self._menubar = QMenuBar(holder)
        vl.addWidget(self._menubar)
        vl.addWidget(bar)
        return holder

    def _build_menus(self) -> None:
        # Panels is the one primary top-level menu; everything else (theme,
        # toggles, layouts, API keys, help, quit) lives under Settings.
        self._m_panels = self._menubar.addMenu("&Panels")
        self._rebuild_panels_menu()

        self._build_settings_menu()

    def _build_settings_menu(self) -> None:
        """The consolidated Settings menu — appearance, accessibility, layouts,
        data keys, help, and quit. Sits beside Panels on the menu bar."""
        from .theme import THEMES, colorblind_enabled, current_theme

        m = self._menubar.addMenu("&Settings")
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
            "Keep findash running in the system tray when the window is closed"
        )
        a_tray.toggled.connect(self._set_close_to_tray)
        self._settings_tray_act = a_tray
        m.addAction(a_tray)

        m.addSeparator()

        # -- named layouts (save / load / import / export / reset) ----------
        self._m_layout = m.addMenu("Layout")
        self._rebuild_layout_menu()

        # -- optional data-source keys --------------------------------------
        a_apis = QAction("API Keys…", self)
        a_apis.triggered.connect(self._show_api_keys)
        m.addAction(a_apis)

        m.addSeparator()

        # -- help / updates / about -----------------------------------------
        a_guide = QAction("Keyboard Shortcuts && Guide…", self)
        a_guide.setShortcut(QKeySequence.StandardKey.HelpContents)
        a_guide.triggered.connect(self._show_onboarding)
        m.addAction(a_guide)
        a_update = QAction("Check for Updates…", self)
        a_update.triggered.connect(self._check_for_updates)
        m.addAction(a_update)
        a_about = QAction("About findash", self)
        a_about.triggered.connect(self._show_about)
        m.addAction(a_about)

        m.addSeparator()

        a_quit = QAction("&Quit", self)
        a_quit.setShortcut(QKeySequence.StandardKey.Quit)
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
            f"{'Enable' if on else 'Disable'} color-blind mode?\n\nfindash needs "
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

    def _show_api_keys(self) -> None:
        from .settings_dialog import ApiKeysDialog

        if ApiKeysDialog(self).exec():
            self.statusBar().showMessage(
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
            "<b>findash works out of the box</b> — quotes, charts, and news "
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
        from .onboarding_dialog import OnboardingDialog

        OnboardingDialog(self).exec()

    def maybe_show_onboarding(self) -> None:
        """First launch only (per QSettings flag): show the guide once."""
        from .onboarding_dialog import OnboardingDialog

        if OnboardingDialog.should_auto_show():
            OnboardingDialog(self).exec()

    def _show_about(self) -> None:
        from . import __version__

        QMessageBox.about(
            self,
            "About findash",
            f"<b>findash</b> — personal market terminal<br>"
            f"Version {__version__}<br><br>"
            "Data: Yahoo Finance / Google News (free, delayed).",
        )

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
        self.statusBar().showMessage(f"Layout saved: {name}", 4000)

    def _load_named_layout(self, name: str) -> None:
        doc = self.layout_store.get(name)
        if doc and self.apply_layout(doc):
            self.statusBar().showMessage(f"Layout loaded: {name}", 4000)

    def _delete_named_layout(self, name: str) -> None:
        resp = QMessageBox.question(
            self, "Delete layout", f"Delete the layout “{name}”?"
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.layout_store.delete(name)
            self._rebuild_layout_menu()
            self.statusBar().showMessage(f"Layout deleted: {name}", 4000)

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
                act.triggered.connect(
                    lambda _=False, pid=meta.id: self.add_panel(pid)
                )
                sub.addAction(act)

    def _on_theme_selected(self, name: str) -> None:
        from .theme import current_theme, set_theme

        if name == current_theme():
            return  # already active (e.g. re-clicking the checked item)
        resp = QMessageBox.question(
            self,
            "Switch theme",
            f"Switch to the {name} theme?\n\nfindash needs to restart to apply "
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
            QProcess.startDetached(sys.executable, ["-m", "findash"])
        self.close()

    def _on_command(self) -> None:
        raw = self._cmd.text().strip()
        if not raw:
            return
        self._cmd.push_history(raw)
        if raw.startswith("/"):
            self._run_slash_command(raw)
        else:
            SymbolContext.instance().set_symbol(
                DEFAULT_GROUP, raw.upper(), source=self
            )
        self._cmd.clear()
        self._cmd.refresh_completions()

    def _command_completions(self) -> list[str]:
        """Candidates for the command bar: /add <panel>, /layout <name>, the
        bare /save & /refresh verbs, and every watchlist symbol currently on
        screen."""
        items = [f"/add {m.id}" for m in PanelRegistry.all()]
        items += [f"/layout {name}" for name in self.layout_store.names()]
        items += ["/save", "/refresh"]
        symbols: set[str] = set()
        for dock in self._docks.values():
            widget = dock.widget()
            if getattr(widget, "panel_id", "") == "watchlist":
                symbols.update(getattr(widget, "_symbols", []) or [])
        items += sorted(symbols)
        return items

    def _run_slash_command(self, raw: str) -> None:
        parts = raw[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd == "add":
            if PanelRegistry.get(arg):
                self.add_panel(arg)
                self.statusBar().showMessage(f"Added panel: {arg}", 3000)
            else:
                self.statusBar().showMessage(f"Unknown panel: {arg or '(none)'}", 3000)
        elif cmd == "layout":
            if arg in self.layout_store.names():
                self._load_named_layout(arg)
            else:
                self.statusBar().showMessage(f"Unknown layout: {arg or '(none)'}", 3000)
        elif cmd == "save":
            if arg:
                self.layout_store.put(arg, self.serialize_layout())
                self._rebuild_layout_menu()
                self.statusBar().showMessage(f"Layout saved: {arg}", 4000)
            else:
                self._save_named_layout()
        elif cmd == "refresh":
            self._refresh_all()
        else:
            self.statusBar().showMessage(f"Unknown command: /{cmd}", 3000)

    # -- panel management --------------------------------------------------------

    def add_panel(
        self,
        panel_id: str,
        instance_id: str | None = None,
        link_group: str | None = None,
        settings: dict | None = None,
        area: QtAds.DockWidgetArea = QtAds.DockWidgetArea.CenterDockWidgetArea,
        target_instance: str | None = None,
    ) -> QtAds.CDockWidget | None:
        """Create a panel dock. ``area`` is global unless ``target_instance``
        names an existing panel instance — then the new dock is placed
        relative to (or tabbed into, with CenterDockWidgetArea) that panel's
        dock area. Lets layouts build precise multi-column arrangements."""
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
        except Exception as exc:
            import traceback

            traceback.print_exc()
            # one broken panel must not blank the whole window — report it to
            # the user and skip; the invalid panel never reaches the dock manager
            self.statusBar().showMessage(
                f"Couldn't open {meta.title}: {exc}", 6000
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
        dock.setFeature(QtAds.CDockWidget.DockWidgetDeleteOnClose, True)
        # Movable (can be dragged to a new dock position) but NOT floatable —
        # dragging a panel relocates and snaps it; it never becomes a window.
        dock.setFeature(QtAds.CDockWidget.DockWidgetMovable, True)
        dock.setFeature(QtAds.CDockWidget.DockWidgetFloatable, False)
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
        if target is not None:
            self.dock_manager.addDockWidget(area, dock, target.dockAreaWidget())
        else:
            self.dock_manager.addDockWidget(area, dock)
        self._docks[instance_id] = dock

        # late join: sync to the group's current symbol
        sym = SymbolContext.instance().symbol(panel.link_group)
        if sym:
            panel._apply_symbol(sym)
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
            self.statusBar().showMessage(
                "This layout was saved by a newer version of findash — "
                "update to load it.",
                6000,
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
        for spec in doc.get("panels", []):
            self.add_panel(
                spec.get("panel_id", ""),
                instance_id=spec.get("instance"),
                link_group=spec.get("link_group"),
                settings=spec.get("settings"),
            )
        state = doc.get("ads_state", "")
        if state:
            from PySide6.QtCore import QByteArray

            try:
                self.dock_manager.restoreState(QByteArray.fromHex(state.encode()))
            except Exception:
                pass
        return True

    # -- layout file sharing (export a saved layout / import one) -------------

    def _export_named_layout(self, name: str) -> None:
        """Write a saved layout to a shareable ``.findashlayout`` file."""
        doc = self.layout_store.get(name)
        if not doc:
            return
        payload = dict(doc)
        payload["name"] = name  # so the recipient's import knows what to call it
        fn, _ = QFileDialog.getSaveFileName(
            self,
            "Export layout",
            f"{name}{LAYOUT_EXT}",
            f"findash layout (*{LAYOUT_EXT});;JSON (*.json)",
        )
        if not fn:
            return
        try:
            Path(fn).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export layout", f"Couldn't save:\n{exc}")
            return
        self.statusBar().showMessage(f"Layout exported: {Path(fn).name}", 5000)

    def _import_layout_file(self) -> None:
        """Import a shared layout file into the saved layouts, then load it."""
        fn, _ = QFileDialog.getOpenFileName(
            self,
            "Import layout",
            "",
            f"findash layout (*{LAYOUT_EXT} *.json);;All files (*)",
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
                f"{path.name} isn't a valid findash layout file.",
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
        self.statusBar().showMessage(f"Layout imported: {name}", 5000)

    def _open_core_four(self) -> None:
        self.add_panel("watchlist", area=QtAds.DockWidgetArea.LeftDockWidgetArea)
        self.add_panel("chart", area=QtAds.DockWidgetArea.CenterDockWidgetArea)
        self.add_panel("news", area=QtAds.DockWidgetArea.RightDockWidgetArea)
        self.add_panel("analyst", area=QtAds.DockWidgetArea.BottomDockWidgetArea)

    def default_startup(self) -> None:
        """Restore the auto-saved last session if there is one. A fresh install
        has none, so it opens to an empty workspace — the user adds panels from
        the Panels menu or loads a saved layout (e.g. via the Layout menu).
        The bundled default layout is available on demand via
        Layout ▸ Reset to Default Layout."""
        last = self.layout_store.get_last()
        if isinstance(last, dict) and last.get("panels"):
            self.apply_layout(last)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Hide to the tray if the user opted in (unless Quit was chosen);
        otherwise auto-save the arrangement and really close."""
        if self._close_to_tray_enabled() and not self._force_quit:
            event.ignore()
            self.hide()
            if self._tray is not None:
                self._tray.showMessage(
                    "findash",
                    "Still running in the tray — right-click the icon to quit.",
                    QSystemTrayIcon.MessageIcon.Information, 4000,
                )
            return
        try:
            self.layout_store.set_last(self.serialize_layout())
        except Exception:
            pass
        try:
            DataHub.instance()._flush_cache()  # persist latest values for next launch
        except Exception:
            pass
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)
