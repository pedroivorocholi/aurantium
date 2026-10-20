"""Transitions between states — the selection that moves instead of jumping.

A tab change used to be a cut: the amber underline vanished from one tab and
appeared under another in the same frame, so nothing told the eye where the
selection had gone. Same for the dock tabs' active box, a chip's checked fill,
and a menu that was simply there. This module is the one place those moves are
animated, so every surface travels on the same curve and the same budget
(``motion.py``).

What animates, and what never does
----------------------------------

Colour, opacity, and an *indicator's* position and size. Never the layout
geometry of content: a dense panel reflowing for 180ms is worse than a cut.
So the moving thing is always an overlay the layout knows nothing about:

* ``SlidingIndicator`` — a small painted widget laid over (or under) a tab
  strip. Its geometry is the only thing that moves. A new target retargets the
  running animation from where the indicator *is*, so rapid clicks redirect
  it rather than queueing or snapping back.
* QTabBar (company, portfolio, the list editor's tabs) — the amber underline
  is an indicator; the stylesheet's static underline is made transparent on a
  bar that has one (``slidingIndicator`` property). The incoming page fades
  up from ``PAGE_IN_FROM``.
* QtAds dock tabs — the active tab's box (fill + underline) is an indicator
  under the tabs, in the tabs' own container, so a tab dragged along the strip
  carries it along.
* Checkable buttons (chips, segmented selectors) — on a mouse press the
  pressed chip and its checkable siblings are snapshotted; whichever changed
  checked state by the release crossfades from its snapshot. A keyboard toggle
  or a programmatic ``setChecked`` stays instant: the motion budget does not
  animate keyboard actions, and a restore is not an event.
* Menus and dropdowns — fade in. Not out: a menu closes because the user
  picked something, and the eye is already on the result.

Skipped, with reasons: hover colours (QSS states cannot be interpolated
without custom-painting every control, and an all-day terminal wants hover
answered instantly) and expanding/collapsing sections (layout geometry).

Reduced motion
--------------

``motion.duration`` returns 0 when the OS asks for less motion. Every
transition here lands on its final state immediately in that case — the
indicator under the right tab, the chip filled, the menu opaque.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QMenu,
    QPushButton,
    QTabBar,
    QTabWidget,
    QWidget,
)

from . import motion, theme

#: Dynamic property set on a tab strip that carries a sliding indicator; the
#: stylesheet turns the strip's static selected-underline transparent on it.
PROPERTY = "slidingIndicator"
#: Height of the selected-tab underline, matching the stylesheet's 2px border.
UNDERLINE_PX = 2


class SlidingIndicator(QWidget):
    """A painted overlay whose geometry travels to each new target.

    ``paint(painter, rect)`` draws it; the widget itself never takes a mouse
    event, so drags, clicks and double-clicks reach whatever is underneath.
    """

    def __init__(self, host: QWidget, paint: Callable[[QPainter, QRect], None]) -> None:
        super().__init__(host)
        self._paint = paint
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setEasingCurve(motion.EASE_OUT)
        self._target = QRect()
        self.hide()

    def target(self) -> QRect:
        return QRect(self._target)

    def animating(self) -> bool:
        return self._anim.state() == QPropertyAnimation.State.Running

    def slide_to(self, rect: QRect) -> None:
        """Travel to ``rect`` from wherever the indicator is right now."""
        if rect.isEmpty():
            self._anim.stop()
            self._target = QRect()
            self.hide()
            return
        if not self.isVisible() or self.geometry().isEmpty():
            self.snap_to(rect)  # nothing on screen to travel from
            return
        if rect == self._target and (self.animating() or self.geometry() == rect):
            return
        self._target = QRect(rect)
        self._anim.stop()
        ms = motion.duration(motion.INDICATOR_MS)
        if ms == 0:
            self.setGeometry(rect)
            return
        self._anim.setDuration(ms)
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect)
        self._anim.start()

    def snap_to(self, rect: QRect) -> None:
        """Be at ``rect`` now — a resize or a relayout, not a selection."""
        self._anim.stop()
        self._target = QRect(rect)
        if rect.isEmpty():
            self.hide()
            return
        self.setGeometry(rect)
        self.show()

    def follow(self, rect: QRect) -> None:
        """The target moved under us (layout, drag): retarget a running slide,
        or snap if there is none."""
        if rect == self._target:
            return
        if self.animating():
            self.slide_to(rect)
        else:
            self.snap_to(rect)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        self._paint(p, self.rect())
        p.end()


_DIRECT = Qt.FindChildOption.FindDirectChildrenOnly

#: Strong references to every live tracker. A tracker is a Python QObject
#: parented to a tab bar QtAds created in C++; the only thing anchoring its
#: Python half was that bar's transient wrapper, so a garbage collection
#: freed it (attributes and all) while Qt went on calling its event filter
#: and its indicator's paintEvent. Released when the C++ object goes.
_LIVE: dict[int, QObject] = {}


def _keep_alive(tracker: QObject) -> None:
    key = id(tracker)
    _LIVE[key] = tracker
    tracker.destroyed.connect(lambda *_: _LIVE.pop(key, None))


def indicator_of(strip) -> Optional[SlidingIndicator]:
    """The sliding indicator serving a QTabBar or a QtAds dock tab bar, or
    None. Looked up as a Qt child rather than a Python attribute: QtAds
    creates its tab bars in C++, and their Python wrappers do not keep
    attributes alive."""
    tracker = strip.findChild(_TabBarTracker, "", _DIRECT) or strip.findChild(
        _DockTabTracker, "", _DIRECT
    )
    return tracker.indicator if tracker is not None else None


def _mark(strip: QWidget) -> None:
    """Turn the strip's static selected-underline transparent (theme.py)."""
    strip.setProperty(PROPERTY, True)
    strip.style().unpolish(strip)
    strip.style().polish(strip)
    strip.update()


# -- QTabBar: the underline ---------------------------------------------------


class _TabBarTracker(QObject):
    """Keeps a QTabBar's underline indicator on its current tab."""

    def __init__(self, bar: QTabBar) -> None:
        super().__init__(bar)
        _keep_alive(self)
        self._bar = bar
        self.indicator = SlidingIndicator(bar, self._paint)
        self.indicator.setObjectName("tabIndicator")
        bar.installEventFilter(self)
        bar.currentChanged.connect(self._on_current)
        _mark(bar)
        self.indicator.snap_to(self._rect(bar.currentIndex()))

    def _paint(self, p: QPainter, rect: QRect) -> None:
        keyboard = bool(self._bar.property("kbFocus"))
        p.fillRect(rect, QColor(theme.FOCUS if keyboard else theme.ACCENT))

    def _rect(self, index: int) -> QRect:
        if index < 0 or not self._bar.isTabVisible(index):
            return QRect()
        tab = self._bar.tabRect(index)
        if tab.isEmpty():
            return QRect()
        return QRect(tab.left(), tab.bottom() + 1 - UNDERLINE_PX, tab.width(), UNDERLINE_PX)

    def _on_current(self, index: int) -> None:
        self.indicator.slide_to(self._rect(index))
        fade_in_page(self._bar, index)

    #: What the underline has to answer. Checked before anything else is
    #: touched: a tracker still receives its strip's events while Qt destroys
    #: it, after its Python attributes are already gone.
    _KINDS = frozenset((
        QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.ChildAdded,
        QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest,
        QEvent.Type.FontChange, QEvent.Type.StyleChange, QEvent.Type.Paint,
    ))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        kind = event.type()
        if kind not in self._KINDS:
            return False
        try:
            self._handle(kind, event)
        except (AttributeError, RuntimeError):
            pass  # torn down underneath us
        return False

    def _handle(self, kind, event) -> None:
        if kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.indicator.update()  # keyboard focus recolours it
        elif kind == QEvent.Type.ChildAdded and event.child() is not self.indicator:
            # a tab's close button arrived on top of it; the press scrim is
            # left alone — the live press belongs above the underline
            from . import press

            if not isinstance(event.child(), press._PressScrim):
                QTimer.singleShot(0, self._raise_indicator)
        elif kind in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
            QEvent.Type.Paint,  # tabs inserted, removed, renamed, elided
        ):
            self.indicator.follow(self._rect(self._bar.currentIndex()))

    def _raise_indicator(self) -> None:
        try:
            self.indicator.raise_()
        except RuntimeError:
            pass


def slide_tab_bar(bar: QTabBar) -> SlidingIndicator:
    """Give ``bar`` a sliding underline. Idempotent."""
    existing = bar.findChild(_TabBarTracker, "", _DIRECT)
    if existing is not None:
        return existing.indicator
    return _TabBarTracker(bar).indicator


def fade_in_page(bar: QTabBar, index: int) -> None:
    """Fade a tab widget's incoming page up from ``PAGE_IN_FROM``. Opacity
    only — the page is laid out at its final size from the first frame."""
    tabs = bar.parentWidget()
    if not isinstance(tabs, QTabWidget) or tabs.tabBar() is not bar:
        return
    page = tabs.widget(index)
    if page is None or not motion.animations_enabled() or not page.isVisible():
        return
    motion.fade_animation(page)  # make sure the effect exists to seed it
    page._aurantium_fade_effect.setOpacity(
        min(motion.fade_opacity(page), motion.PAGE_IN_FROM)
    )
    motion.fade(page, 1.0, ms=motion.OVERLAY_MS)


# -- QtAds dock tabs: the active box ---------------------------------------------


def _tabs_of(bar) -> list:
    try:
        return [bar.tab(i) for i in range(bar.count())]
    except RuntimeError:
        return []


class _DockTabTracker(QObject):
    """Keeps a dock area's active-tab box under its current tab."""

    def __init__(self, bar) -> None:
        super().__init__(bar)
        _keep_alive(self)
        self._bar = bar
        self._container: Optional[QWidget] = None
        self._watched: Optional[QWidget] = None
        self.indicator: Optional[SlidingIndicator] = None
        bar.currentChanged.connect(self._on_current)
        bar.installEventFilter(self)
        _mark(bar)
        self._sync(animate=False)

    def _paint(self, p: QPainter, rect: QRect) -> None:
        p.fillRect(rect, QColor(theme.CHROME_HOVER))
        p.fillRect(
            QRect(rect.left(), rect.bottom() + 1 - UNDERLINE_PX, rect.width(), UNDERLINE_PX),
            QColor(theme.ACCENT),
        )

    def _current_tab(self):
        i = self._bar.currentIndex()
        if i < 0 or i >= self._bar.count():
            return None
        tab = self._bar.tab(i)
        return tab if tab is not None and not tab.isHidden() else None

    def _ensure_indicator(self, container: QWidget) -> SlidingIndicator:
        if self.indicator is None or self._container is not container:
            if self.indicator is not None:
                self.indicator.deleteLater()
            self._container = container
            self.indicator = SlidingIndicator(container, self._paint)
            self.indicator.setObjectName("dockTabIndicator")
            container.installEventFilter(self)
        self.indicator.lower()  # beneath the tabs: they paint their labels over it
        return self.indicator

    def _watch(self, tab) -> None:
        if tab is self._watched:
            return
        if self._watched is not None:
            try:
                self._watched.removeEventFilter(self)
            except RuntimeError:
                pass
        self._watched = tab
        if tab is not None:
            tab.installEventFilter(self)

    def _sync(self, animate: bool) -> None:
        tab = self._current_tab()
        self._watch(tab)
        if tab is None:
            if self.indicator is not None:
                self.indicator.snap_to(QRect())
            return
        ind = self._ensure_indicator(tab.parentWidget())
        if animate:
            ind.slide_to(tab.geometry())
        else:
            ind.follow(tab.geometry())

    def _on_current(self, _index: int) -> None:
        self._sync(animate=True)

    _KINDS = frozenset((
        QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show,
        QEvent.Type.LayoutRequest, QEvent.Type.ChildAdded,
    ))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        kind = event.type()
        if kind not in self._KINDS:
            return False  # before any attribute: see _TabBarTracker._KINDS
        try:
            if obj is self._watched and kind in (QEvent.Type.Move, QEvent.Type.Resize):
                # the active tab grew (its close button appeared) or was dragged
                if self.indicator is not None:
                    self.indicator.follow(obj.geometry())
            elif kind in (QEvent.Type.Show, QEvent.Type.LayoutRequest, QEvent.Type.ChildAdded):
                QTimer.singleShot(0, self._sync_later)
        except (AttributeError, RuntimeError):
            pass  # torn down underneath us
        return False

    def _sync_later(self) -> None:
        try:
            self._sync(animate=False)
        except (AttributeError, RuntimeError):
            pass  # area destroyed in the meantime


def slide_dock_tabs(bar) -> None:
    """Give a QtAds ``CDockAreaTabBar`` a sliding active-tab box. Idempotent."""
    if bar.findChild(_DockTabTracker, "", _DIRECT) is None:
        _DockTabTracker(bar)


# -- checkable buttons: crossfade the checked fill --------------------------------


class _Snapshot(QWidget):
    """A button's previous look, laid over it and fading out."""

    def __init__(self, button: QWidget, pixmap) -> None:
        super().__init__(button)
        self._pixmap = pixmap
        self._opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(button.rect())
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(motion.EASE_OUT)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.valueChanged.connect(self._set_opacity)
        self._anim.finished.connect(self.deleteLater)

    def opacity(self) -> float:
        return self._opacity

    def _set_opacity(self, value) -> None:
        self._opacity = float(value)
        self.update()

    def start(self) -> None:
        ms = motion.duration(motion.CROSSFADE_MS)
        if ms == 0:
            self.deleteLater()
            return
        self.show()
        self.raise_()
        # the press scrim (press.py) belongs on top: it is the live response
        from . import press

        scrim = press.scrim_of(self.parentWidget())
        if scrim is not None:
            self.stackUnder(scrim)
        self._anim.setDuration(ms)
        self._anim.start()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.setOpacity(self._opacity)
        p.drawPixmap(self.rect(), self._pixmap)
        p.end()


def _checkable_group(button: QAbstractButton) -> list:
    """The pressed button plus its checkable sibling buttons — whichever of
    them changes checked state with this click."""
    group = button.group()
    if group is not None:
        return [b for b in group.buttons() if b.isVisible()]
    parent = button.parentWidget()
    if parent is None:
        return [button]
    siblings = [
        w for w in parent.findChildren(QPushButton, options=Qt.FindChildOption.FindDirectChildrenOnly)
        if w.isCheckable() and w.isVisible()
    ]
    return siblings or [button]


def _take_snapshot(button: QWidget):
    # Grabbed with any fade already running on it, so a click during a
    # crossfade starts from what is on screen rather than snapping back.
    pixmap = button.grab()
    for old in button.findChildren(_Snapshot, options=Qt.FindChildOption.FindDirectChildrenOnly):
        old.deleteLater()
    return pixmap


# -- the app-wide filter ------------------------------------------------------------


class TransitionFilter(QObject):
    """Application-wide: attaches indicators as tab strips are polished,
    snapshots checkable buttons on a press, fades popups in."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pending: dict = {}  # button -> (checked, pixmap)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        kind = event.type()
        try:
            if kind == QEvent.Type.Polish:
                if isinstance(obj, QTabBar):
                    slide_tab_bar(obj)
                elif obj.metaObject().className() == "ads::CDockAreaTabBar":
                    slide_dock_tabs(obj)
            elif kind == QEvent.Type.MouseButtonPress:
                if (
                    isinstance(obj, QPushButton)
                    and obj.isCheckable()
                    and obj.isEnabled()
                    and event.button() == Qt.MouseButton.LeftButton
                ):
                    self._snapshot(obj)
            elif kind == QEvent.Type.MouseButtonRelease:
                if self._pending:
                    # after the click has toggled it (and any exclusive
                    # neighbour), not before
                    QTimer.singleShot(0, self._crossfade)
            elif kind == QEvent.Type.Show:
                if isinstance(obj, QMenu) or (
                    isinstance(obj, QWidget)
                    and obj.metaObject().className() == "QComboBoxPrivateContainer"
                ):
                    fade_in_popup(obj)
        except RuntimeError:
            pass  # destroyed mid-event
        return False

    def _snapshot(self, button: QPushButton) -> None:
        self._pending = {}
        if not motion.animations_enabled():
            return
        for b in _checkable_group(button):
            self._pending[b] = (b.isChecked(), _take_snapshot(b))

    def _crossfade(self) -> None:
        pending, self._pending = self._pending, {}
        for button, (was, pixmap) in pending.items():
            try:
                if button.isChecked() != was and button.isVisible():
                    _Snapshot(button, pixmap).start()
            except RuntimeError:
                pass


def fade_in_popup(popup: QWidget) -> None:
    if not motion.animations_enabled():
        return
    anim = motion.dialog_animation(popup)  # one windowOpacity animation per window
    anim.stop()
    popup.setWindowOpacity(motion.POPUP_IN_FROM)
    anim.setDuration(motion.duration(motion.POPUP_IN_MS))
    anim.setStartValue(motion.POPUP_IN_FROM)
    anim.setEndValue(1.0)
    anim.start()


def install(app) -> TransitionFilter:
    """Install on ``app``; keep the returned object alive."""
    flt = TransitionFilter(app)
    app.installEventFilter(flt)
    return flt
