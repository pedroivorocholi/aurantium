"""Press feedback — the ``:active`` travel Qt stylesheets cannot express.

Every interactive control in aurantium had a *colour* for each state and no
motion whatsoever between them — and one of those colours was not doing any
work. Measured on the dark theme, a pressed button swapped to ``#1a2129``
against a resting fill of ``#1b2530``: one level, four and seven. On screen
that is the same colour, so pressing a button looked like not pressing it. The
control acknowledged nothing.

This module gives every button one physical answer to a click, and it is the
same answer everywhere: **the control sinks into shadow under the pointer, and
eases back out when released.**


Why a tonal sink and not a scale
--------------------------------

Apple's press response is a scale-down plus a dim. Qt has neither
``transform`` nor ``transition``: QSS supports no scale at all, so the options
were

1. **Paint the controls ourselves.** A scale means overriding ``paintEvent`` on
   every button class and drawing fill, border, radius, text and icon by hand —
   which throws away every state rule in ``theme.py``'s stylesheet and makes
   the design system two systems that have to agree.
2. **Animate the widget's geometry.** A widget inside a layout that shrinks by
   two pixels drags its neighbours' repaint with it, and in a dense terminal
   with a dozen chips in a row that is a visible reflow, not a press.
3. **Animate the tone.** Which is the other half of what Apple actually does,
   and the half that survives being lifted onto a different toolkit.

So aurantium presses by tone. It is also the honest cue for this product: a
terminal's controls read as hardware, and hardware keys go *into shadow* when
you push them. Hover lifts the control one step toward the light; pressing it
takes it clearly below its own resting tone — which is what the old rule failed
to do. Lift and sink are opposite directions from a common origin, so all three
of resting, hover and pressed stay separable at a glance.

Why an overlay child widget
---------------------------

The tone has to animate, so it cannot live in the stylesheet: driving a colour
through a per-widget ``setStyleSheet`` at 60fps is a full QSS reparse per
frame. ``QGraphicsEffect`` is out for the reason ``motion.py`` already records
— an installed effect makes Qt render that widget to an offscreen pixmap on
every paint, for the rest of its life.

What is left is a transparent child widget sitting on top of the control,
painting one rounded rectangle of black at an animated alpha. It costs one
extra widget and one ``fillPath`` per frame *while a press is in flight*, and
nothing at all otherwise. Crucially it composes with the stylesheet rather than
replacing it: hover, checked, focused and disabled keep being drawn by
``theme.py`` underneath, and the scrim darkens whatever is there — including an
amber checked fill, which sinks to the same deep amber ``:checked:hover``
already uses.

The animation itself follows the house pattern in ``panel.py``'s
``_HeaderStrip``: a Qt ``Property(float)``, one long-lived
``QPropertyAnimation`` retargeted per press, and a ``paintEvent``. Retargeting
rather than recreating is what lets a fast double-click read as two presses
instead of one smear, and it is the bug ``_HeaderStrip``'s docstring records.

Pressed is deliberately *not* a stylesheet colour
-------------------------------------------------

``theme.py``'s ``:pressed`` rules now hold the fill steady at the hover tone
instead of swapping it. If they swapped it, the swap would land on the first
frame and the eased sink underneath would be invisible — a snap dressed in an
animation. One mechanism owns the pressed state, and it is the animated one.

The exception is a press that never involves the mouse. Space on a focused
button sets ``:pressed`` with no ``MouseButtonPress``, so no scrim fires and
the stylesheet's hover-tone rule is the whole feedback. That is intended:
``motion.py``'s budget says keyboard-triggered actions do not animate.

Rows, tabs, sections and marks
------------------------------

Most of what a user clicks in a terminal is not a button: it is a watchlist
row, a news headline, a sortable column header, a tab, a heatmap tile, a mark
on the events lane. Those had no press response at all, so the same sink now
covers them — the *same* scrim, curve, durations and alpha — but over only the
part that was pressed: the row, the cell, the section, the tab, the mark.

One scrim per host (an item view's viewport, a tab bar, a custom widget),
retargeted to the pressed region on each press. Never one per row: a 500-row
table costs one extra widget, not 500, and nothing at all while idle. Where
the region comes from:

* item views — the pressed row (the cell, for a table that selects cells);
  only when the view does something with a click (it selects, or the item is
  checkable), so a read-only table does not pretend to be a button;
* header sections — when the header is clickable (sortable);
* tab bars — the pressed tab;
* anything else — a ``press_region(pos)`` method on the widget (or on the
  plot widget whose viewport was pressed), returning ``(QRect, radius)`` in
  the pressed widget's coordinates, or None. The events lane uses it to sink
  just the mark under the pointer.

Reduced motion
--------------

Durations go through ``motion.duration()``, which returns 0 when the OS asked
for reduced motion. A zero-length ``QPropertyAnimation`` emits no ``finished``
and does not always write its end value, so — exactly as ``motion.fade`` does —
the end value is written by hand on that path. The press still reads as a
press; it simply does not travel.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEvent,
    QObject,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QPushButton,
    QTabBar,
    QTableView,
    QToolButton,
    QWidget,
)

from . import motion
from .theme import PRESS_ALPHA, RADIUS_SM

#: Attribute holding a widget's scrim, so it is created at most once.
_ATTR = "_aurantium_press_scrim"
#: Attribute holding a host's *region* scrim (rows, tabs, sections, marks).
_REGION_ATTR = "_aurantium_press_region_scrim"

#: Class-name fragment for the QtAds dock tab. Matched by name rather than by
#: ``isinstance``: PySide6-QtAds is an optional import in several entry points
#: (tests build widgets without it), and a filter that has to import a docking
#: library to decide whether a QPushButton is a button is the wrong dependency.
_TAB_CLASS = "CDockWidgetTab"


class _PressScrim(QWidget):
    """The animated shadow that sits on top of one control while it is pressed.

    Paints nothing at rest. Transparent to the mouse, so the control below
    keeps receiving every event exactly as before this widget existed.
    """

    def __init__(self, target: QWidget, radius: int, region: bool = False) -> None:
        super().__init__(target)
        self._press = 0.0
        self._radius = radius
        #: A region scrim covers one row/tab/section/mark of its host, set per
        #: press by ``press_region_in``; a plain scrim covers the whole host.
        self._region: QRect | None = QRect() if region else None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        # NOT WA_OpaquePaintEvent: the point is to composite over whatever the
        # stylesheet already painted underneath.
        self._anim = QPropertyAnimation(self, b"press", self)
        self._anim.setEasingCurve(motion.EASE_OUT)
        # Teardown is keyed on the animation ending, never on the value being
        # zero. See _settle() for why that distinction is the whole feature.
        self._anim.finished.connect(self._settle)
        # A control can change size *because* it was clicked, so the scrim has
        # to follow it rather than keep the geometry it was given at mouse-down.
        target.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        """Track the control's geometry for as long as the scrim exists.

        A QtAds dock tab is what found this: activating a tab reveals its close
        button, so the tab jumps from 88px to 108px on the very press that
        activates it. Sizing the scrim once, at mouse-down, left the shadow
        20px short of the tab for the whole press — which reads as a rendering
        fault, not as feedback.

        Watching for the resize rather than re-measuring every animation frame
        is what covers a *held* press: the animation finishes at full shadow
        and stops, so there are no more frames to re-measure in, and a control
        that resizes after that would otherwise stay wrong until release.
        """
        if (
            event.type() == QEvent.Type.Resize
            and obj is self.parentWidget()
            and self._region is None
        ):
            self.setGeometry(obj.rect())
        return False  # never consume: this observes, it does not intercept

    # -- the animated property (house pattern: see panel._HeaderStrip) -------

    def get_press(self) -> float:
        return self._press

    def set_press(self, value: float) -> None:
        self._press = float(value)
        self.update()

    def _settle(self) -> None:
        """Retire the scrim once a press has fully decayed.

        This must key on the *animation finishing*, not on the value reaching
        zero, and the difference is not academic — keying it on the value is
        the bug that made this whole module invisible in the running app.

        ``QPropertyAnimation.start()`` writes its start value synchronously,
        and the start value of a press-in is 0.0. A ``hide()`` inside the
        setter therefore fired one line after ``_run`` showed the scrim: the
        property went on animating 0 -> 1 perfectly while the widget carrying
        it was hidden, so a press painted nothing. Every test passed, because
        every test asserted the property.

        ``motion.py``'s ``_settle_fade`` already had this shape for the same
        reason. This is the second time the codebase has needed it.
        """
        if self._press <= 0.001:
            self.hide()  # nothing to paint, and nothing to composite

    #: 0 = resting, 1 = fully sunk. Animated, never assigned directly outside
    #: this module except by the state-gallery harness and its tests.
    press = Property(float, get_press, set_press)

    # -- transitions ---------------------------------------------------------

    def _run(self, to: float, ms: int) -> None:
        self._anim.stop()
        if self._region is None:
            self.setGeometry(self.parentWidget().rect())
        self._anim.setDuration(motion.duration(ms))
        self._anim.setStartValue(self._press)
        self._anim.setEndValue(to)
        self._anim.start()
        if to > 0.0:
            # After start(), deliberately. start() writes the start value
            # synchronously and a hidden widget stays hidden through every
            # frame that follows, so showing first and starting second is what
            # made the press invisible.
            self.show()
            self.raise_()  # dock tabs have children; buttons do not
        if self._anim.duration() == 0:
            # Reduced motion: land on the state without travelling to it. A
            # zero-length animation emits no finished(), so settle by hand.
            self.set_press(to)
            self._settle()

    def press_in(self) -> None:
        self._run(1.0, motion.PRESS_IN_MS)

    def press_region_in(self, rect: QRect, radius: int) -> None:
        """Sink just ``rect`` of the host. A new region starts from rest: the
        shadow moving from the last row to this one would read as a slide."""
        if self._region != rect:
            self._anim.stop()
            self.set_press(0.0)
        self._region = QRect(rect)
        self._radius = radius
        self.setGeometry(rect)
        self.press_in()

    def press_out(self) -> None:
        self._run(0.0, motion.PRESS_OUT_MS)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._press <= 0.001:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        shade = QColor(0, 0, 0)
        shade.setAlphaF(PRESS_ALPHA * self._press)
        if self._radius > 0:
            path = QPainterPath()
            # Inset by half a pixel so the antialiased corner sits inside the
            # control's own border instead of feathering past it.
            path.addRoundedRect(
                QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                self._radius,
                self._radius,
            )
            p.fillPath(path, shade)
        else:
            p.fillRect(self.rect(), shade)
        p.end()


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------

#: Set this dynamic property (to a corner radius) on any widget that should
#: sink when clicked like a button; clear it (``None``) to opt back out.
PRESS_RADIUS_PROPERTY = "pressRadius"


def corner_radius(widget) -> int | None:
    """The scrim radius for ``widget``, or None if it takes no press feedback.

    Buttons take the control radius so the shadow follows their corners.
    Dock tabs are square-cornered trapezoid-less rectangles in this theme, so
    they take 0. Everything else — checkboxes, radios, text inputs, labels,
    item views — is excluded on purpose: their press target is a 12px indicator
    or a text caret, and a shadow over the whole widget would be pointing at
    the wrong thing.
    """
    if isinstance(widget, (QPushButton, QToolButton)):
        return RADIUS_SM
    # A non-editable combo is a button that opens a list. An editable one is a
    # text field first, and is excluded with the other text inputs.
    if isinstance(widget, QComboBox) and not widget.isEditable():
        return RADIUS_SM
    # Opt-in for clickable surfaces that aren't buttons (the Profile's stat
    # tiles): a widget sets the ``pressRadius`` property while it is
    # clickable and gets the same sink every button does.
    try:
        opt_in = widget.property(PRESS_RADIUS_PROPERTY)
    except (AttributeError, RuntimeError):
        opt_in = None
    if opt_in is not None:
        return int(opt_in)
    try:
        if _TAB_CLASS in widget.metaObject().className():
            return 0
    except (AttributeError, RuntimeError):
        pass
    return None


def attach(widget, radius: int | None = None) -> _PressScrim | None:
    """The scrim belonging to ``widget``, created on first use.

    Returns None for a widget that takes no press feedback. Per-widget and
    persistent, for the reason ``motion.fade_animation`` records: a throwaway
    animation per interaction, held by a Python reference, is how the notice
    card broke.
    """
    existing = getattr(widget, _ATTR, None)
    if existing is not None:
        try:
            existing.objectName()  # cheap liveness probe
            return existing
        except RuntimeError:
            pass  # C++ side destroyed underneath us; fall through and rebuild
    if radius is None:
        radius = corner_radius(widget)
    if radius is None:
        return None
    scrim = _PressScrim(widget, radius)
    setattr(widget, _ATTR, scrim)
    return scrim


def _row_region(view: QAbstractItemView, viewport: QWidget, pos) -> QRect | None:
    index = view.indexAt(pos)
    if not index.isValid():
        return None
    flags = index.flags()
    if not flags & Qt.ItemFlag.ItemIsEnabled:
        return None
    selects = view.selectionMode() != QAbstractItemView.SelectionMode.NoSelection
    # a check box the user can see: QTableWidgetItem sets ItemIsUserCheckable
    # by default, with no box drawn and nothing for a click to toggle
    checkable = bool(flags & Qt.ItemFlag.ItemIsUserCheckable) and (
        index.data(Qt.ItemDataRole.CheckStateRole) is not None
    )
    if not (selects or checkable):
        return None  # a read-only view: a click does nothing, so nothing sinks
    rect = view.visualRect(index)
    if isinstance(view, QTableView) and (
        view.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectItems
    ):
        return rect  # the cell: that is what this table acts on
    return QRect(0, rect.top(), viewport.width(), rect.height())


def _section_region(header: QHeaderView, pos) -> QRect | None:
    if not header.sectionsClickable():
        return None
    horizontal = header.orientation() == Qt.Orientation.Horizontal
    logical = header.logicalIndexAt(pos)
    if logical < 0:
        return None
    start = header.sectionViewportPosition(logical)
    size = header.sectionSize(logical)
    vp = header.viewport()
    if horizontal:
        return QRect(start, 0, size, vp.height())
    return QRect(0, start, vp.width(), size)


def press_region(widget, pos) -> tuple[QWidget, QRect, int] | None:
    """Where a press at ``pos`` (in ``widget``'s coordinates) should sink, for
    surfaces whose clickable part is smaller than the widget: ``(host, rect,
    radius)`` with ``rect`` in ``host``'s coordinates, or None."""
    parent = widget.parentWidget()
    if isinstance(parent, QHeaderView) and parent.viewport() is widget:
        rect = _section_region(parent, pos)
        return (widget, rect, 0) if rect is not None else None
    if isinstance(widget, QHeaderView):
        return None  # presses arrive on its viewport, handled above
    if isinstance(parent, QAbstractItemView) and parent.viewport() is widget:
        rect = _row_region(parent, widget, pos)
        return (widget, rect, 0) if rect is not None else None
    if isinstance(widget, QTabBar):
        i = widget.tabAt(pos)
        if i < 0 or not widget.isTabEnabled(i):
            return None
        return widget, widget.tabRect(i), 0
    for owner in (widget, parent):
        try:
            # not getattr's default: pyqtgraph's PlotWidget forwards unknown
            # attributes to its PlotItem and raises NameError, not
            # AttributeError, for one neither has
            hook = owner.__dict__.get("press_region") if owner is not None else None
        except (AttributeError, RuntimeError):
            hook = None
        if callable(hook):
            local = pos if owner is widget else widget.mapTo(owner, pos)
            found = hook(local)
            if found is None:
                return None
            rect, radius = found
            if owner is not widget:
                rect = QRect(owner.mapTo(widget, rect.topLeft()), rect.size())
            return widget, rect, radius
    return None


def region_scrim(host: QWidget) -> _PressScrim:
    """``host``'s region scrim, created on first use — one per host, however
    many rows it holds."""
    existing = getattr(host, _REGION_ATTR, None)
    if existing is not None:
        try:
            existing.objectName()
            return existing
        except RuntimeError:
            pass
    scrim = _PressScrim(host, 0, region=True)
    setattr(host, _REGION_ATTR, scrim)
    return scrim


def scrim_of(widget) -> _PressScrim | None:
    """``widget``'s scrim if one has ever been created, else None."""
    return getattr(widget, _ATTR, None)


def press_level(widget) -> float:
    """How far ``widget`` is currently sunk, 0.0–1.0. 0.0 when it has never
    been pressed. For an item view this is its pressed row (the scrim lives
    on the viewport). For tests and for the state-gallery harness."""
    hosts = [widget]
    viewport = getattr(widget, "viewport", None)
    if callable(viewport):
        hosts.append(viewport())
    level = 0.0
    for host in hosts:
        for scrim in (scrim_of(host), getattr(host, _REGION_ATTR, None)):
            if scrim is None:
                continue
            try:
                level = max(level, scrim.get_press())
            except RuntimeError:
                pass
    return level


# --------------------------------------------------------------------------
# The application-wide filter
# --------------------------------------------------------------------------

class PressFeedbackFilter(QObject):
    """Maintains press feedback for every eligible control in the application.

    Install once, on the ``QApplication`` — the same shape as ``focus.py``, and
    for the same reason: the alternative is a call at every site that builds a
    button, which is a rule nobody remembers on the fourteenth panel.

    The release is handled globally rather than on the pressed widget. A tab
    dragged out of its dock area, a button whose popup menu opens under the
    cursor, a press that ends over a different widget — none of those deliver a
    ``MouseButtonRelease`` to the widget that got the press, and each one would
    otherwise leave a control stuck in shadow.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active: _PressScrim | None = None
        #: The widget the current press belongs to. Kept alongside its scrim
        #: because teardown has to know *whose* press it is cancelling — see
        #: ``_end_if_owned``.
        self._target: QWidget | None = None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            self._begin(obj, event)
        elif kind in (
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        ):
            self._end()
        elif kind in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate):
            # Only when it is the pressed control that went away, NOT when
            # anything in the application did — see _end_if_owned.
            self._end_if_owned(obj)
        return False  # never consume: this observes, it does not intercept

    def _begin(self, obj, event) -> None:
        try:
            if event.button() != Qt.MouseButton.LeftButton:
                return
            if not isinstance(obj, QWidget) or not obj.isEnabled():
                return
            region = press_region(obj, event.position().toPoint())
            if region is not None:
                host, rect, radius = region
                self._end()  # a press that never got its release
                scrim = region_scrim(host)
                scrim.press_region_in(rect, radius)
                self._active = scrim
                self._target = obj
                return
            scrim = attach(obj)
            if scrim is None:
                return
            self._end()  # a press that never got its release
            scrim.press_in()
            self._active = scrim
            self._target = obj
        except (AttributeError, RuntimeError):
            self._active = None
            self._target = None

    def _end_if_owned(self, obj) -> None:
        """Cancel the press only if ``obj`` is the pressed control, or contains it.

        This filter is installed on the QApplication, so it sees ``Hide`` and
        ``WindowDeactivate`` for **every object in the application**, not just
        the one under the pointer. Cancelling on all of them is what made dock
        tabs look broken: clicking an inactive tab makes QtAds hide the
        *outgoing* tab's close button, that unrelated ``Hide`` arrived one event
        after the press began, and the scrim was torn down before it painted a
        frame. Clicking the already-current tab hides nothing, so that case
        worked — which is an excellent disguise for the bug.

        The narrow case the handling is actually for remains: whatever the click
        did removed the control itself (a dialog closing, a panel being
        destroyed, the window deactivating on Alt-Tab), so no release will ever
        arrive and the press would otherwise stick at full shadow forever.
        """
        target = self._target
        if target is None:
            return
        try:
            if obj is target or (
                isinstance(obj, QWidget) and obj.isAncestorOf(target)
            ):
                self._end()
        except RuntimeError:
            self._end()  # target destroyed underneath us — nothing to keep

    def _end(self) -> None:
        scrim, self._active = self._active, None
        self._target = None
        if scrim is None:
            return
        try:
            scrim.press_out()
        except RuntimeError:
            pass  # widget destroyed by whatever the click did


def install(app) -> PressFeedbackFilter:
    """Install the filter on ``app`` and return it.

    The returned object must be kept alive: an event filter that gets garbage
    collected stops filtering, silently, and every control simply goes back to
    acknowledging nothing.
    """
    flt = PressFeedbackFilter(app)
    app.installEventFilter(flt)
    return flt
