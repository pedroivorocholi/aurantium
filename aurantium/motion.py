"""Motion policy — one place that decides whether aurantium animates.

The web has ``prefers-reduced-motion``; Windows has the "Show animations in
Windows" switch (Settings ▸ Accessibility ▸ Visual effects), read through
``SystemParametersInfo(SPI_GETCLIENTAREAANIMATION)``. macOS has "Reduce motion"
in Accessibility ▸ Display. Both mean the same thing, and aurantium had been
honouring neither.

Reduced motion is **gentler, not zero**: a transition that carries meaning
survives as an instant state change rather than disappearing. What it must not
do is leave a *stuck* state — an early version held the link flash at full tint
with no duration to decay it, so the header stayed coloured forever. Where the
information already exists elsewhere (the header prints the symbol that
arrived), the honest reduced-motion answer is simply no animation.

The motion budget for the app lives here too. aurantium is an Operate-mode
terminal that a user keeps open all day, so the numbers sit at the fast end:
nothing animates longer than a quarter second, and anything triggered from the
keyboard does not animate at all.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect

#: Durations, in ms. Kept well under the 300ms ceiling for interface motion —
#: past that a control starts to feel like it is lagging the user.
FLASH_MS = 240      # link-group tint decay on a propagated symbol
NOTICE_IN_MS = 110  # notice card appearing
NOTICE_OUT_MS = 220 # notice card retiring on its own timer
NOTICE_DISMISS_MS = 100  # ...but a click gets an immediate answer

#: The house curve. Qt's built-in OutCubic is the weak equivalent of CSS's
#: default ease-out; OutQuint matches the strong cubic-bezier(0.23, 1, 0.32, 1)
#: that UI motion actually wants — it moves immediately, which is the moment
#: the user is watching, then settles.
EASE_OUT = QEasingCurve.Type.OutQuint


def _windows_animations_on() -> bool:
    import ctypes

    SPI_GETCLIENTAREAANIMATION = 0x1042
    enabled = ctypes.c_bool(True)
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
    )
    return bool(enabled.value) if ok else True


def _macos_animations_on() -> bool:
    from PySide6.QtCore import QProcess

    proc = QProcess()
    proc.start(
        "defaults", ["read", "com.apple.universalaccess", "reduceMotion"]
    )
    if not proc.waitForFinished(400):
        return True
    out = bytes(proc.readAllStandardOutput()).decode(errors="replace").strip()
    return out not in ("1", "true", "TRUE")


def animations_enabled() -> bool:
    """Whether the OS wants animation. Defaults to True on any error — a
    failure to read an accessibility setting must not silently strip motion
    from every user."""
    try:
        if sys.platform == "win32":
            return _windows_animations_on()
        if sys.platform == "darwin":
            return _macos_animations_on()
    except Exception:
        pass
    return True


#: Overlay crossfades — an empty state arriving, a loading veil retiring.
OVERLAY_MS = 160
#: A panel's first appearance. Once per panel, so it can afford a beat more.
PANEL_IN_MS = 180


def fade_animation(widget) -> QPropertyAnimation:
    """The one fade animation belonging to ``widget``, created on first use.

    Per-widget and persistent on purpose. Creating a throwaway animation per
    fade with ``DeleteWhenStopped`` while holding a reference to it is how the
    notice card broke: the next fade called ``stop()`` on an object Qt had
    already destroyed, and the RuntimeError killed the fade *and* everything
    after it in the caller. Every fade in the app goes through here so that
    cannot be reintroduced one call site at a time.
    """
    anim = getattr(widget, "_aurantium_fade", None)
    if anim is None:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setEasingCurve(EASE_OUT)
        widget._aurantium_fade = anim
        widget._aurantium_fade_effect = effect
        widget._aurantium_fade_hide = False
        # Connected once, here. Reconnecting per fade meant disconnecting a
        # signal that might have no connections, which PySide reports as a
        # RuntimeWarning rather than raising — easy to leave in by accident.
        anim.finished.connect(lambda w=widget: _settle_fade(w))
    return anim


#: Widgets whose effect is due to be detached on the next event-loop turn.
_pending_detach: list = []


def _settle_fade(widget) -> None:
    if getattr(widget, "_aurantium_fade_hide", False) and fade_opacity(widget) <= 0.01:
        widget.hide()
    if fade_opacity(widget) >= 0.999:
        # Fully opaque: the effect has nothing left to do, and leaving it
        # installed makes Qt render the widget to an offscreen pixmap on every
        # paint for the rest of its life. That is a real cost on a panel full
        # of live tables and charts, paid forever for a 180ms entrance.
        #
        # Deferred rather than immediate: this runs inside the animation's own
        # finished() signal, and setGraphicsEffect(None) destroys the object
        # that animation is driving.
        if widget not in _pending_detach:
            _pending_detach.append(widget)
            QTimer.singleShot(0, lambda w=widget: _detach_fade(w))


def _detach_fade(widget) -> None:
    """Drop a widget's fade effect and animation once it is fully opaque."""
    if widget in _pending_detach:
        _pending_detach.remove(widget)
    try:
        if fade_opacity(widget) < 0.999:
            return  # a new fade started before we got here
        anim = getattr(widget, "_aurantium_fade", None)
        if anim is not None:
            anim.stop()
            anim.setTargetObject(None)
        widget._aurantium_fade = None
        widget._aurantium_fade_effect = None
        widget.setGraphicsEffect(None)
    except RuntimeError:
        pass  # widget already destroyed


def settle_pending_detach() -> None:
    """Run any deferred effect detachments now. For tests, which have no event
    loop turning to deliver the singleShot."""
    for widget in list(_pending_detach):
        _detach_fade(widget)


def fade_opacity(widget) -> float:
    """The widget's current fade opacity (1.0 when it has no effect — either
    never faded, or the effect was detached after landing fully opaque)."""
    effect = getattr(widget, "_aurantium_fade_effect", None)
    if effect is None:
        return 1.0
    try:
        return effect.opacity()
    except RuntimeError:
        return 1.0  # effect destroyed underneath us


def fade(widget, to: float, ms: int = OVERLAY_MS, hide_when_done: bool = False):
    """Fade ``widget`` to ``to`` opacity.

    Fading in shows the widget first, so there is something to fade. Fading out
    optionally hides it at the end, which keeps a fully-transparent overlay
    from swallowing nothing but still occupying the stacking order.

    Restarting mid-fade retargets from the opacity currently on screen rather
    than snapping back to the start, so a quick in-then-out reads as one
    continuous move instead of a flicker.
    """
    anim = fade_animation(widget)
    anim.stop()
    start = fade_opacity(widget)
    widget._aurantium_fade_hide = hide_when_done
    if to > 0.0:
        # There has to be something on screen for the fade to be visible. Keyed
        # on the target, not on start < to: a widget hidden while still at full
        # opacity is the normal case for an overlay that was hidden outright.
        widget.show()

    anim.setDuration(duration(ms))
    anim.setStartValue(start)
    anim.setEndValue(to)
    anim.start()
    if anim.duration() == 0:
        # reduced motion: a zero-length animation emits no finished(), so land
        # on the end state here. The state change still happens; it just does
        # not travel.
        widget._aurantium_fade_effect.setOpacity(to)
        _settle_fade(widget)
    return anim


#: Where a panel's entrance starts from. Not 0.0 — nothing in the real world
#: appears out of nothing, and a fade from zero reads as a pop rather than as
#: something arriving.
PANEL_IN_FROM = 0.45


def fade_in_panel(panel) -> None:
    """Fade a freshly-added panel in.

    Adding a panel snapped it into existence at full opacity while the
    splitters shoved its neighbours aside in the same frame — a lot of change
    with nothing to tell the eye which part was the new thing. Once per panel,
    so it can afford a beat that per-interaction motion could not.

    Deliberately *not* used when restoring a saved layout: a dozen panels
    fading in at startup delays the data the user opened the app to see.
    """
    if not animations_enabled():
        return
    fade_animation(panel)  # ensure the effect exists before seeding it
    panel._aurantium_fade_effect.setOpacity(PANEL_IN_FROM)
    fade(panel, 1.0, ms=PANEL_IN_MS)


#: A modal is a context switch, not a control answering a click, so it gets a
#: longer beat than inline UI does.
DIALOG_IN_MS = 220
#: Dialogs start visible enough to read as arriving rather than popping.
DIALOG_IN_FROM = 0.4


def dialog_animation(dialog) -> QPropertyAnimation:
    """The one entrance animation belonging to ``dialog``."""
    anim = getattr(dialog, "_aurantium_dialog_fade", None)
    if anim is None:
        anim = QPropertyAnimation(dialog, b"windowOpacity", dialog)
        anim.setEasingCurve(EASE_OUT)
        dialog._aurantium_dialog_fade = anim
    return anim


def fade_in_dialog(dialog) -> None:
    """Fade a first-run dialog in.

    Reserved for the rare tier — the F1 guide and the first-run workspace
    chooser — where a beat of arrival is welcome. Deliberately not applied to
    working dialogs like API Keys: there the user came to do a task, and any
    delay before they can start is a cost with nothing bought.

    Window opacity rather than a scale: a modal is not anchored to a trigger,
    so it has no origin to grow from, and scaling a top-level window on Windows
    is neither smooth nor native-looking.
    """
    if not animations_enabled():
        return
    anim = dialog_animation(dialog)
    anim.stop()
    dialog.setWindowOpacity(DIALOG_IN_FROM)
    anim.setDuration(duration(DIALOG_IN_MS))
    anim.setStartValue(DIALOG_IN_FROM)
    anim.setEndValue(1.0)
    anim.start()


def duration(ms: int) -> int:
    """The duration to actually use — 0 when the OS asked for reduced motion,
    which makes a QPropertyAnimation jump straight to its end value rather than
    being skipped entirely. The state change still happens; it just doesn't
    travel."""
    return ms if animations_enabled() else 0
