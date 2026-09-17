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
    QRectF,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QPushButton, QToolButton, QWidget

from . import motion
from .theme import PRESS_ALPHA, RADIUS_SM

#: Attribute holding a widget's scrim, so it is created at most once.
_ATTR = "_aurantium_press_scrim"

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

    def __init__(self, target: QWidget, radius: int) -> None:
        super().__init__(target)
        self._press = 0.0
        self._radius = radius
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
        if event.type() == QEvent.Type.Resize and obj is self.parentWidget():
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


def scrim_of(widget) -> _PressScrim | None:
    """``widget``'s scrim if one has ever been created, else None."""
    return getattr(widget, _ATTR, None)


def press_level(widget) -> float:
    """How far ``widget`` is currently sunk, 0.0–1.0. 0.0 when it has never
    been pressed. For tests and for the state-gallery harness."""
    scrim = scrim_of(widget)
    if scrim is None:
        return 0.0
    try:
        return scrim.get_press()
    except RuntimeError:
        return 0.0


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
