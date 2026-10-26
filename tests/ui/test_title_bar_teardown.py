"""The window frame outlives its title bar for a moment on the way down.

``MainWindow.nativeEvent`` forwards every Windows message to ``NativeFrame``,
and while Qt destroys the window the title bar (a child) goes first. Hit tests
kept arriving for it: 1.12.0 logged hundreds of "Internal C++ object
(TitleBar) already deleted" errors per shutdown. The frame must hand those
messages back to Qt instead of touching the dead bar.
"""

import ctypes
import sys
from ctypes import wintypes

import pytest
import shiboken6
from PySide6.QtWidgets import QWidget

from aurantium import title_bar

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows frame only")


def test_hit_test_after_the_bar_is_gone_is_left_to_qt(qapp):
    window = QWidget()
    window.resize(400, 300)
    bar = QWidget(window)
    frame = title_bar.NativeFrame(window, bar)
    frame._hwnd = int(window.winId())
    shiboken6.delete(bar)  # destroyed first, as a child, while the window lives on

    msg = wintypes.MSG()
    msg.hWnd = frame._hwnd
    msg.message = title_bar.WM_NCHITTEST
    x, y = 200, 150  # mid-window: past the resize borders, onto the bar check
    msg.lParam = (y << 16) | x
    assert frame.handle(ctypes.addressof(msg)) is None
