"""The main window's one title bar, VS Code style.

[logo] [Panels] [Settings] … [search box, centred] … [– □ ×]

On Windows the window is frameless, but it stays a *native* window: Qt's
``FramelessWindowHint`` drops the frame, and ``NativeFrame`` puts the styles
that carry the native behaviour back (``WS_THICKFRAME`` for edge resize, Aero
Snap and Win+arrow keys, ``WS_CAPTION`` for the minimize/maximize animations,
the box bits so the taskbar can minimize and restore it). ``WM_NCCALCSIZE``
then hands the whole window to the client area, so nothing native is drawn,
and ``WM_NCHITTEST`` tells Windows which of our pixels are edges, caption and
maximize button. Returning ``HTMAXBUTTON`` over our maximize button is what
makes Windows 11 show its Snap Layouts flyout there.

Everywhere else (macOS, Linux) the native frame stays exactly as it was; the
bar keeps the logo, menus and command bar and drops its caption buttons.

No frameless-window library: it is a few dozen lines of ctypes.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from . import icons

IS_WINDOWS = sys.platform == "win32"

#: Height of the bar. It used to be two rows (menu bar, then the command bar);
#: the command bar now sits inside this one. VS Code's is 35.
HEIGHT = 34
#: Width of a caption button, as on Windows 11 and VS Code.
CAPTION_W = 46
#: Grab band for resizing from the window edge, in logical pixels.
RESIZE_BORDER = 6
#: The command bar's width when the window has room for it.
COMMAND_MAX_W = 720
COMMAND_MIN_W = 220


class TitleBar(QWidget):
    """Logo, the Panels and Settings icon menus, the centred command bar and
    (Windows only) the caption buttons."""

    def __init__(self, window, command: QWidget, logo: QWidget | None) -> None:
        super().__init__(window)
        # keeps the old menu row's chrome fill and bottom hairline (theme.py)
        self.setObjectName("menuBarRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)
        self._window = window
        self._command = command
        command.setParent(self)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._logo = logo
        if logo is not None:
            logo.setParent(self)
            lay.addWidget(logo)
            lay.addSpacing(6)
        self.panels_button = self._menu_button("panels", "Panels")
        self.settings_button = self._menu_button("settings", "Settings")
        lay.addWidget(self.panels_button)
        lay.addWidget(self.settings_button)
        self._left_end = lay.count()
        lay.addStretch(1)

        self.min_button = self.max_button = self.close_button = None
        if IS_WINDOWS:
            self.min_button = self._caption_button("titleMin", "minimize", "Minimize")
            self.max_button = self._caption_button("titleMax", "maximize", "Maximize")
            self.close_button = self._caption_button("titleClose", "close", "Close")
            self.min_button.clicked.connect(self.minimize)
            self.max_button.clicked.connect(self.toggle_maximized)
            self.close_button.clicked.connect(window.close)
            for b in (self.min_button, self.max_button, self.close_button):
                lay.addWidget(b)
            from .theme import CHROME_TEXT_DIM

            # white on the red hover fill, as Windows and VS Code draw it
            self.close_button.setIcon(
                icons.two_tone_icon(
                    "close", CHROME_TEXT_DIM, "#ffffff", icons.device_pixel_ratio(self)
                )
            )

    # -- construction -----------------------------------------------------------

    def _icon(self, kind: str):
        from .theme import CHROME_TEXT_DIM

        return icons.icon(kind, CHROME_TEXT_DIM, icons.device_pixel_ratio(self))

    def _menu_button(self, kind: str, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("titleMenuButton")
        b.setIcon(self._icon(kind))
        b.setIconSize(QSize(icons.SIZE, icons.SIZE))
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return b

    def _caption_button(self, name: str, kind: str, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName(name)
        b.setIcon(self._icon(kind))
        b.setIconSize(QSize(icons.SIZE, icons.SIZE))
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setFixedSize(CAPTION_W, HEIGHT)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    def set_menus(self, panels: QMenu, settings: QMenu) -> None:
        self.panels_button.setMenu(panels)
        self.settings_button.setMenu(settings)

    # -- window state -----------------------------------------------------------

    def toggle_maximized(self) -> None:
        """Maximize or restore. On Windows this goes through the same system
        command a caption double-click sends: Qt's showNormal() on a frameless
        window updated Qt's state but left the native window zoomed."""
        w = self._window
        if IS_WINDOWS:
            hwnd = int(w.winId())
            cmd = SC_RESTORE if _user32.IsZoomed(hwnd) else SC_MAXIMIZE
            _user32.SendMessageW(hwnd, WM_SYSCOMMAND, cmd, 0)
        elif w.isMaximized():
            w.showNormal()
        else:
            w.showMaximized()

    def minimize(self) -> None:
        if IS_WINDOWS:
            _user32.SendMessageW(int(self._window.winId()), WM_SYSCOMMAND, SC_MINIMIZE, 0)
        else:
            self._window.showMinimized()

    def sync_window_state(self) -> None:
        """Maximize shows the restore glyph while the window is maximized."""
        if self.max_button is None:
            return
        maxed = self._window.isMaximized()
        self.max_button.setIcon(self._icon("restore" if maxed else "maximize"))
        self.max_button.setToolTip("Restore Down" if maxed else "Maximize")

    def set_max_button_state(self, hover: bool, pressed: bool = False) -> None:
        """Hover/press for the maximize button, which Windows owns while it
        reports HTMAXBUTTON — Qt sees no mouse events over it then."""
        b = self.max_button
        if b is None:
            return
        if b.property("ncHover") == hover and b.property("ncPressed") == pressed:
            return
        b.setProperty("ncHover", hover)
        b.setProperty("ncPressed", pressed)
        b.style().unpolish(b)
        b.style().polish(b)

    # -- layout -----------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._place_command()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._place_command()

    def _place_command(self) -> None:
        """Centre the command bar on the window, as VS Code centres its search,
        shrinking it to the gap between the side groups when it can't."""
        lay = self.layout()
        lay.activate()
        left = 0
        for i in range(self._left_end):
            item = lay.itemAt(i)
            if item is not None and item.geometry().isValid():
                left = max(left, item.geometry().right())
        right = self.width()
        for b in (self.min_button,):
            if b is not None:
                right = b.geometry().left()
        gap = 12
        cx = self.width() // 2
        half = min(cx - left - gap, right - cx - gap, COMMAND_MAX_W // 2)
        h = min(self._command.sizeHint().height(), self.height())
        y = (self.height() - h) // 2
        if half * 2 >= COMMAND_MIN_W:
            self._command.setGeometry(cx - half, y, half * 2, h)
        else:
            self._command.setGeometry(left + gap, y, max(0, right - left - 2 * gap), h)

    # -- hit testing ------------------------------------------------------------

    def is_drag_area(self, local: QPoint) -> bool:
        """Empty bar and the logo drag the window; buttons and the text field
        do not."""
        if not self.rect().contains(local):
            return False
        child = self.childAt(local)
        return child is None or child is self._logo or child is self._command

    def over_max_button(self, local: QPoint) -> bool:
        b = self.max_button
        return b is not None and b.isVisible() and b.geometry().contains(local)


# -- native frame (Windows) ---------------------------------------------------

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    try:
        _dwmapi = ctypes.windll.dwmapi
    except OSError:  # pragma: no cover - dwmapi ships with every Windows since Vista
        _dwmapi = None

    WM_NCCALCSIZE = 0x0083
    WM_NCHITTEST = 0x0084
    WM_NCMOUSEMOVE = 0x00A0
    WM_NCLBUTTONDOWN = 0x00A1
    WM_NCLBUTTONUP = 0x00A2
    WM_NCMOUSELEAVE = 0x02A2
    WM_SYSCOMMAND = 0x0112
    SC_MINIMIZE, SC_MAXIMIZE, SC_RESTORE = 0xF020, 0xF030, 0xF120

    HTCLIENT, HTCAPTION, HTMAXBUTTON = 1, 2, 9
    HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
    HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

    GWL_STYLE = -16
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_SYSMENU = 0x00080000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    _NATIVE_STYLES = WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX

    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x0001, 0x0002, 0x0004
    SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0010, 0x0020
    MONITOR_DEFAULTTONEAREST = 2
    TME_LEAVE, TME_NONCLIENT = 0x00000002, 0x00000010

    class _NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    class _MARGINS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_int) for n in ("left", "right", "top", "bottom")]

    class _APPBARDATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uCallbackMessage", wintypes.UINT),
            ("uEdge", wintypes.UINT),
            ("rc", wintypes.RECT),
            ("lParam", wintypes.LPARAM),
        ]

    ABM_GETAUTOHIDEBAREX = 0x0000000B
    #: Strip left free on an auto-hide taskbar's edge (as Windows Terminal does)
    AUTOHIDE_GAP = 2

    class _TRACKMOUSEEVENT(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("hwndTrack", wintypes.HWND),
            ("dwHoverTime", wintypes.DWORD),
        ]

    _GetWindowLong = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
    _SetWindowLong = getattr(_user32, "SetWindowLongPtrW", _user32.SetWindowLongW)
    _GetWindowLong.restype = ctypes.c_ssize_t
    _GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
    _SetWindowLong.restype = ctypes.c_ssize_t
    _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
    _user32.IsZoomed.argtypes = [wintypes.HWND]
    _user32.SendMessageW.restype = ctypes.c_ssize_t
    _user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]


def _leave_room_for_autohide_taskbar(rect, monitor) -> None:
    """With an auto-hide taskbar the work area is the whole monitor, and a
    window covering all of it reads as full screen: Windows will not slide the
    taskbar up over it, and Qt reports it as WindowFullScreen. Leaving a
    couple of pixels free on the taskbar's edge keeps it a maximized window."""
    shell32 = ctypes.windll.shell32
    for edge, side in ((0, "left"), (1, "top"), (2, "right"), (3, "bottom")):
        abd = _APPBARDATA()
        abd.cbSize = ctypes.sizeof(_APPBARDATA)
        abd.uEdge = edge
        abd.rc = monitor
        if shell32.SHAppBarMessage(ABM_GETAUTOHIDEBAREX, ctypes.byref(abd)):
            if side == "left":
                rect.left += AUTOHIDE_GAP
            elif side == "top":
                rect.top += AUTOHIDE_GAP
            elif side == "right":
                rect.right -= AUTOHIDE_GAP
            else:
                rect.bottom -= AUTOHIDE_GAP


class NativeFrame:
    """The Windows half of the frameless title bar. ``MainWindow`` forwards
    ``nativeEvent`` here; on any other platform every method is a no-op."""

    def __init__(self, window, bar: TitleBar) -> None:
        self._window = window
        self._bar = bar
        self._hwnd = 0
        self._max_pressed = False

    @staticmethod
    def window_flags(flags):
        """Flags for the main window: frameless on Windows only."""
        if IS_WINDOWS:
            return flags | Qt.WindowType.FramelessWindowHint
        return flags

    def attach(self) -> None:
        """Give the native window back its resize / snap / caption styles. Run
        whenever the window has a (new) native handle."""
        if not IS_WINDOWS:
            return
        hwnd = int(self._window.winId())
        if hwnd == self._hwnd:
            return
        self._hwnd = hwnd
        style = _GetWindowLong(hwnd, GWL_STYLE)
        _SetWindowLong(hwnd, GWL_STYLE, style | _NATIVE_STYLES)
        if _dwmapi is not None:
            # a one-pixel sliver of DWM frame keeps the native drop shadow
            margins = _MARGINS(1, 1, 1, 1)
            try:
                _dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
            except OSError:
                pass
        _user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )

    def handle(self, message) -> tuple[bool, int] | None:
        """Answer one native message, or None to let Qt have it."""
        if not IS_WINDOWS or not self._hwnd:
            return None
        msg = wintypes.MSG.from_address(int(message))
        if msg.hWnd != self._hwnd:
            return None
        m = msg.message
        if m == WM_NCCALCSIZE:
            if msg.wParam and _user32.IsZoomed(msg.hWnd):
                # A maximized window overhangs the monitor by its (now
                # invisible) frame; clamp the client to the work area so
                # nothing is cut off and the taskbar stays visible.
                params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
                mon = _user32.MonitorFromWindow(msg.hWnd, MONITOR_DEFAULTTONEAREST)
                info = _MONITORINFO()
                info.cbSize = ctypes.sizeof(_MONITORINFO)
                if mon and _user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                    params.rgrc[0] = info.rcWork
                    _leave_room_for_autohide_taskbar(params.rgrc[0], info.rcMonitor)
            return True, 0  # no native frame: the client is the whole window
        if m == WM_NCHITTEST:
            hit = self._hit_test(msg.lParam)
            self._bar.set_max_button_state(hit == HTMAXBUTTON, self._max_pressed)
            return True, hit
        if m == WM_NCMOUSEMOVE:
            if msg.wParam == HTMAXBUTTON:
                self._track_nc_leave()
            else:
                self._max_pressed = False
            self._bar.set_max_button_state(msg.wParam == HTMAXBUTTON, self._max_pressed)
            return None
        if m == WM_NCMOUSELEAVE:
            self._max_pressed = False
            self._bar.set_max_button_state(False)
            return None
        if m == WM_NCLBUTTONDOWN and msg.wParam == HTMAXBUTTON:
            self._max_pressed = True
            self._bar.set_max_button_state(True, True)
            return True, 0  # ours to draw; stop Windows painting a classic button
        if m == WM_NCLBUTTONUP and msg.wParam == HTMAXBUTTON:
            fire = self._max_pressed
            self._max_pressed = False
            self._bar.set_max_button_state(True, False)
            if fire:
                self._bar.toggle_maximized()
            return True, 0
        return None

    def _hit_test(self, lparam: int) -> int:
        w = self._window
        # the point Windows asks about, in physical screen pixels → logical
        # window coordinates (the client is the whole window)
        pt = wintypes.POINT(
            ctypes.c_short(lparam & 0xFFFF).value,
            ctypes.c_short((lparam >> 16) & 0xFFFF).value,
        )
        _user32.ScreenToClient(self._hwnd, ctypes.byref(pt))
        dpr = w.devicePixelRatioF() or 1.0
        pos = QPoint(int(pt.x / dpr), int(pt.y / dpr))
        x, y = pos.x(), pos.y()
        if not w.isMaximized():
            b = RESIZE_BORDER
            left, right = x < b, x >= w.width() - b
            top, bottom = y < b, y >= w.height() - b
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM
        local = self._bar.mapFrom(w, pos)
        if self._bar.over_max_button(local):
            return HTMAXBUTTON
        if self._bar.is_drag_area(local):
            return HTCAPTION
        return HTCLIENT

    def _track_nc_leave(self) -> None:
        tme = _TRACKMOUSEEVENT()
        tme.cbSize = ctypes.sizeof(_TRACKMOUSEEVENT)
        tme.dwFlags = TME_LEAVE | TME_NONCLIENT
        tme.hwndTrack = self._hwnd
        tme.dwHoverTime = 0
        _user32.TrackMouseEvent(ctypes.byref(tme))
