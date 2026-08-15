"""Transient in-window notices — the terminal's feedback channel.

aurantium hides the bottom status bar to buy height for the taller top bar
(logo + command line), which left every ``statusBar().showMessage()`` call
writing into a widget the user never sees: layout saved, undo, refresh,
"unknown command", a panel that failed to open, a fired price alert. All of it
was silent.

This restores that channel as a small notice card that floats over the
bottom-right of the window, above the docks, and retires itself. It is drawn as
terminal chrome — chrome fill, hairline border, monospaced text — not as a
product toast: no shadow stack, no icon badge, no rounded pill.

Design notes:

* **One notice at a time.** A terminal reports the last thing that happened;
  a stack of cards would cover data. A new notice replaces the current one and
  restarts the timer.
* **Never steals focus or blocks work.** It is a plain child widget (not a
  window), so it can't take activation, and clicking it dismisses it rather
  than doing anything.
* **Motion is a fade only.** Sliding a card across live data is a
  distraction; a fade reads as "this appeared" without moving anything the eye
  is tracking. Timings and the curve come from motion.py, and collapse to an
  instant show/hide when the OS asks for reduced motion.
* **Asymmetric.** It arrives promptly (the system answering the user) and
  leaves more slowly (nothing is waiting on it), which is why the two
  durations differ.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from . import motion
from .theme import ACCENT, CHROME, CHROME_BORDER, CHROME_TEXT, MONO_FONT

#: Distance from the window's bottom-right corner, in logical pixels.
MARGIN = 18

#: Default dwell time before the notice fades out.
DEFAULT_MSECS = 4000

#: How far the card travels on its way in. A hint of movement, not a slide —
#: it says "this came from the bottom edge" without dragging the eye across
#: live data on its way there.
RISE = 10


class Notifier(QWidget):
    """A single transient notice anchored to its parent's bottom-right corner.

    Call :meth:`show_message`; call :meth:`reposition` from the parent's
    ``resizeEvent``. Construct it with the main window as ``parent`` — it sits
    over the dock area rather than inside any one panel, because what it
    reports (layouts, undo, refreshes, alerts) is window-level, not panel-level.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("notifier")
        # A bare QWidget ignores `background`/`border` from a stylesheet unless
        # it opts into style-drawn backgrounds — without this the card renders
        # as bare text floating over whatever panel is underneath.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Not a window: it cannot take activation away from the command bar.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        self._label = QLabel("", self)
        self._label.setObjectName("notifierText")
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        lay.addWidget(self._label)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        # One animation for the widget's whole life, retargeted per fade.
        # Creating a throwaway per fade with DeleteWhenStopped, while holding a
        # Python reference to it, meant the next fade called stop() on an object
        # Qt had already destroyed — RuntimeError, thrown before the opacity was
        # set and before the dwell timer started, so every notice after the
        # first was invisible and never retired. Reusing one animation also
        # keeps the retargeting behaviour the fades rely on.
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.finished.connect(self._on_fade_finished)
        self._hide_when_faded = False

        # Enters rising from the bottom edge it is anchored to and leaves back
        # through it, rather than materialising in place. Same reason a toast
        # slides from the edge it lives at: it gives the card somewhere to have
        # come from, and makes the dismissal read as the same object leaving.
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setEasingCurve(motion.EASE_OUT)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

        self._apply_style("info")
        self.hide()

    # -- public API ---------------------------------------------------------

    def show_message(
        self, text: str, msecs: int = DEFAULT_MSECS, level: str = "info"
    ) -> None:
        """Show ``text`` for ``msecs``, replacing any notice already up.

        ``level`` is ``"info"`` (quiet chrome text) or ``"warn"`` (amber) —
        warn is for something the user asked for that didn't happen, and for
        fired alerts.
        """
        text = (text or "").strip()
        if not text:
            return
        self._apply_style(level)
        self._label.setText(text)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._slide_to(self._resting_pos(), rise=True, ms=motion.NOTICE_IN_MS)
        self._fade_to(1.0, motion.NOTICE_IN_MS)
        self._timer.start(max(600, int(msecs)))

    def dismiss(self, quick: bool = False) -> None:
        """Fade out and hide. Safe to call when nothing is showing.

        ``quick`` is for a click: the user asked for it gone, and a system
        response to a deliberate action should snap. The unattended timeout
        keeps the slower exit — nothing is waiting on it.
        """
        self._timer.stop()
        # isHidden(), not isVisible(): isVisible() is False whenever an
        # ancestor is hidden, so a notice raised while the window was still
        # being built would refuse to retire itself.
        if self.isHidden():
            return
        ms = motion.NOTICE_DISMISS_MS if quick else motion.NOTICE_OUT_MS
        self._slide_to(self._resting_pos(), rise=False, ms=ms)
        self._fade_to(0.0, ms, hide_after=True)

    def reposition(self) -> None:
        """Re-anchor to the parent's bottom-right corner."""
        self.move(self._resting_pos())

    def _resting_pos(self) -> QPoint:
        """Where the card sits once it has arrived."""
        parent = self.parentWidget()
        if parent is None:
            return self.pos()
        return QPoint(
            max(MARGIN, parent.width() - self.width() - MARGIN),
            max(MARGIN, parent.height() - self.height() - MARGIN),
        )

    def _slide_to(self, resting: QPoint, rise: bool, ms: int) -> None:
        """Animate between the resting position and an offset one just below
        it. ``rise=True`` moves up into place; ``rise=False`` sinks back out
        the same way, so entry and exit are mirror images."""
        low = QPoint(resting.x(), resting.y() + RISE)
        start, end = (low, resting) if rise else (self.pos(), low)
        self._slide.stop()
        self._slide.setDuration(motion.duration(ms))
        self._slide.setStartValue(start)
        self._slide.setEndValue(end)
        if self._slide.duration() == 0:
            # reduced motion: place it, don't travel
            self.move(resting if rise else low)
            return
        self.move(start)
        self._slide.start()

    # -- internals ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.dismiss(quick=True)
        event.accept()

    def _apply_style(self, level: str) -> None:
        accent = ACCENT if level == "warn" else CHROME_TEXT
        self.setStyleSheet(
            f"QWidget#notifier {{ background: {CHROME};"
            f" border: 1px solid {CHROME_BORDER}; border-radius: 3px; }}"
            f"QLabel#notifierText {{ background: transparent; color: {accent};"
            f' font-family: "{MONO_FONT}"; font-size: 11px; }}'
        )

    def _on_fade_finished(self) -> None:
        if self._hide_when_faded:
            self.hide()

    def _fade_to(self, end: float, duration: int, hide_after: bool = False) -> None:
        """Animate opacity to ``end``. Restarting mid-fade picks up from the
        current opacity, so a burst of notices never flickers."""
        self._anim.stop()
        self._hide_when_faded = hide_after
        self._anim.setDuration(motion.duration(duration))
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(motion.EASE_OUT)
        self._anim.start()
        if self._anim.duration() == 0:
            # reduced motion: a zero-length animation never emits finished(),
            # so land on the end state here rather than leaving a dismissed
            # card sitting on screen at full opacity
            self._effect.setOpacity(end)
            self._on_fade_finished()
