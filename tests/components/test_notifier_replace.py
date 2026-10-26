"""A notice replacing one already on screen swaps its text in place.

It used to restart the slide from below — the card dropped and rose again,
reading as a second card arriving. Now a card that is up stays put and only
its text crossfades. A card already on its way out still arrives fresh.
"""

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from aurantium import motion


@pytest.fixture(params=[True, False], ids=["anim-on", "anim-off"])
def animate(request, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: request.param)
    return request.param


@pytest.fixture
def notifier(qapp, animate):
    from aurantium.notifier import Notifier

    parent = QWidget()
    parent.resize(900, 600)
    parent.show()
    n = Notifier(parent)
    yield n
    parent.close()
    parent.deleteLater()


def _land(n):
    for anim in (n._anim, n._slide, getattr(n._label, "_aurantium_fade", None)):
        if anim is not None and anim.state() == anim.State.Running:
            anim.setCurrentTime(anim.duration())
    motion.settle_pending_detach()
    QApplication.processEvents()


def test_a_replacement_does_not_restart_the_slide(notifier, animate):
    notifier.show_message("first")
    _land(notifier)
    notifier.show_message("second")
    assert notifier._slide.state() != notifier._slide.State.Running
    assert notifier.pos().y() == notifier._resting_pos().y()
    assert notifier._label.text() == "second"
    if animate:
        assert motion.fade_opacity(notifier._label) < 1.0  # the text arrives
    _land(notifier)
    assert motion.fade_opacity(notifier._label) == pytest.approx(1.0)
    assert notifier._effect.opacity() == pytest.approx(1.0)
    assert notifier.pos() == notifier._resting_pos()


def test_a_card_on_its_way_out_arrives_fresh(notifier, animate):
    notifier.show_message("first")
    _land(notifier)
    notifier.dismiss()
    notifier.show_message("second")
    if animate:
        assert notifier._slide.state() == notifier._slide.State.Running
    _land(notifier)
    assert not notifier.isHidden()
    assert notifier._effect.opacity() == pytest.approx(1.0)
    assert notifier.pos() == notifier._resting_pos()
