"""The transient notice card — aurantium's feedback channel.

The window has no visible status bar, so every "layout saved" / "nothing to
undo" / "unknown command" the app reports goes through Notifier. A regression
here is silent by definition, hence these.
"""

import pytest


@pytest.fixture
def host(qapp):
    from PySide6.QtWidgets import QWidget

    from aurantium.notifier import Notifier

    parent = QWidget()
    parent.resize(900, 600)
    return parent, Notifier(parent)


def test_starts_hidden(host):
    _, notifier = host
    assert not notifier.isVisibleTo(notifier.parentWidget())


def test_shows_the_message(host):
    parent, notifier = host
    notifier.show_message("Layout saved: Macro Desk")
    assert notifier.isVisibleTo(parent)
    assert notifier._label.text() == "Layout saved: Macro Desk"


def test_blank_messages_are_ignored(host):
    parent, notifier = host
    notifier.show_message("   ")
    assert not notifier.isVisibleTo(parent)


def test_a_new_message_replaces_the_previous_one(host):
    """One notice at a time — a stack of cards would cover live data."""
    parent, notifier = host
    notifier.show_message("first")
    notifier.show_message("second")
    assert notifier._label.text() == "second"


def test_warn_level_uses_the_accent_color(host):
    from aurantium.theme import ACCENT

    _, notifier = host
    notifier.show_message("Unknown command: /pnl", level="warn")
    assert ACCENT in notifier.styleSheet()


def test_it_anchors_to_the_bottom_right_corner(host):
    parent, notifier = host
    notifier.show_message("Refreshing 12 feeds…")
    assert notifier.geometry().right() < parent.width()
    assert notifier.geometry().bottom() < parent.height()
    # near the corner rather than adrift in the middle of the window
    assert parent.width() - notifier.geometry().right() < 40
    assert parent.height() - notifier.geometry().bottom() < 40


def test_it_follows_a_resize(host):
    parent, notifier = host
    notifier.show_message("Layout saved")
    parent.resize(1400, 900)
    notifier.reposition()
    assert parent.width() - notifier.geometry().right() < 40
    assert parent.height() - notifier.geometry().bottom() < 40


def test_dismiss_is_safe_when_nothing_is_showing(host):
    _, notifier = host
    notifier.dismiss()  # must not raise


# -- regression: the animation object outliving its C++ peer ----------------

def _collect(anim):
    """Stop an animation and let Qt actually destroy it.

    DeleteWhenStopped schedules the delete via deleteLater, which only runs
    when the event loop processes deferred-delete events — so a bare stop() in
    a test leaves the C++ object alive and the bug invisible."""
    from PySide6.QtCore import QCoreApplication, QEvent

    anim.stop()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_a_second_notice_still_shows_after_the_first_fade_finished(host):
    """QPropertyAnimation was started with DeleteWhenStopped while a Python
    reference was kept, so the next show_message() called .stop() on a deleted
    C++ object and raised. It raised *before* the opacity fade and before the
    dwell timer started, so every notice after the first one was invisible and
    never retired — the exact failure the notifier exists to prevent."""
    parent, notifier = host
    notifier.show_message("first")
    _collect(notifier._anim)

    notifier.show_message("second")  # must not raise

    assert notifier._label.text() == "second"
    assert notifier.isVisibleTo(parent)
    assert notifier._timer.isActive(), "the dwell timer must start, or it never retires"


def test_the_fade_actually_reaches_full_opacity(host):
    """Guards the same failure from the other side: a notice that shows but
    never animates its opacity up is invisible on screen."""
    _, notifier = host
    notifier.show_message("first")
    _collect(notifier._anim)
    notifier.show_message("second")
    assert notifier._anim.endValue() == 1.0


# -- spatial: it arrives from the edge it lives at --------------------------

def test_the_notice_rises_into_place(host):
    """It fades in place, which reads as materialising out of nowhere. It sits
    against the bottom edge, so it should come from there — a short rise gives
    it somewhere to have come from."""
    parent, notifier = host
    notifier.show_message("Layout saved")
    anim = notifier._slide
    assert anim.startValue().y() > anim.endValue().y(), "should move upward"
    assert anim.startValue().y() - anim.endValue().y() <= 16, (
        "a hint of travel, not a slide across the panel"
    )


def test_it_leaves_the_way_it_came(host):
    """Exit mirrors entry — the canonical toast behaviour, and what makes a
    dismissal feel like the same object leaving rather than a new one."""
    parent, notifier = host
    notifier.show_message("Layout saved")
    resting = notifier._slide.endValue().y()
    notifier.dismiss()
    assert notifier._slide.endValue().y() > resting, "should sink back down"


def test_the_resting_position_is_still_the_anchored_corner(host):
    parent, notifier = host
    notifier.show_message("Layout saved")
    notifier._slide.setCurrentTime(notifier._slide.duration())
    assert parent.height() - notifier.geometry().bottom() < 40


def test_reduced_motion_places_it_without_travel(host, monkeypatch):
    from aurantium import motion

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    parent, notifier = host
    notifier.show_message("Layout saved")
    assert parent.height() - notifier.geometry().bottom() < 40


def test_clicking_to_dismiss_is_faster_than_the_auto_timeout(host):
    """Standard 9 — a deliberate action gets a snappy system response. Waiting
    220ms for a card to go after you clicked it is the app answering slowly to
    a direct request; the unattended timeout can take its time."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from aurantium import motion

    _, notifier = host
    notifier.show_message("Layout saved")
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(5, 5),
        QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    notifier.mousePressEvent(event)
    assert notifier._anim.duration() < motion.NOTICE_OUT_MS
