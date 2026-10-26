"""The panel header strip — where cross-panel symbol linking becomes visible.

Every panel prints the symbol it is currently showing, and flashes its header
in the link group's color when that symbol arrived from *another* panel. With
several panels open, those two things are what tell the user which name each
panel is on and that a click over there landed over here.
"""

import pytest

from aurantium.symbol_context import DEFAULT_GROUP, GROUP_COLORS, UNLINKED


@pytest.fixture
def panel(qapp):
    from aurantium.panel import Panel
    from aurantium.symbol_context import SymbolContext

    # a fresh context per test: the singleton is shared process-wide and would
    # otherwise leak one test's symbol into the next
    SymbolContext._inst = None

    class _P(Panel):
        panel_id = "test_panel"
        panel_title = "Test"

        def build(self) -> None:
            pass

    p = _P()
    p.build()
    yield p
    SymbolContext._inst = None


@pytest.fixture
def motion_on(monkeypatch):
    """Pin OS animation policy ON for tests that assert the flash travels.

    ``motion.animations_enabled()`` reads a real accessibility setting —
    ``SPI_GETCLIENTAREAANIMATION`` on Windows — so whether a flash has any
    amplitude at all depends on the machine the suite runs on. A headless CI
    runner reports client-area animations *off*, ``motion.duration()`` returns
    0, and ``pulse()`` correctly lands on 0.0 with nothing to decay.

    That is right behaviour and wrong test isolation: these tests are about the
    flash mechanism, not about the OS policy, and the policy has its own tests
    below. Left unpinned they pass on a developer desktop and fail in CI with
    ``assert 0.0 > 0.0``, which is exactly what happened the first time the
    release workflow's test gate ever ran.
    """
    from aurantium import motion

    monkeypatch.setattr(motion, "animations_enabled", lambda: True)


def test_header_starts_with_no_symbol(panel):
    assert panel._symbol_lbl.text() == ""


def test_header_prints_the_active_symbol(panel):
    panel.set_symbol("aapl")
    assert panel._symbol_lbl.text() == "AAPL"


def test_a_symbol_from_the_link_group_flashes_the_header(panel, motion_on):
    from aurantium.symbol_context import SymbolContext

    SymbolContext.instance().set_symbol(DEFAULT_GROUP, "MSFT", source=None)
    assert panel._symbol_lbl.text() == "MSFT"
    assert panel._header.flash > 0.0


def test_the_panel_does_not_flash_at_its_own_click(panel):
    """The user is already looking at the click they just made; flashing there
    would spend the signal on the one panel that doesn't need it."""
    panel.set_symbol("MSFT")
    assert panel._header.flash == 0.0


def test_an_unlinked_panel_ignores_the_group(panel):
    from aurantium.symbol_context import SymbolContext

    panel.set_link_group(UNLINKED)
    SymbolContext.instance().set_symbol(DEFAULT_GROUP, "TSLA", source=None)
    assert panel._symbol_lbl.text() == ""
    assert panel._header.flash == 0.0


def test_the_flash_uses_the_link_groups_own_color(panel):
    from PySide6.QtGui import QColor

    from aurantium.symbol_context import SymbolContext

    panel.set_link_group("B")
    SymbolContext.instance().set_symbol("B", "NVDA", source=None)
    assert panel._header._tint == QColor(GROUP_COLORS["B"])


def test_the_badge_is_outlined_not_filled(panel):
    """The link badge is the least important thing on screen; a solid amber
    chip made it the loudest, competing with the data for the accent color."""
    from PySide6.QtGui import QColor

    c = panel._badge.colors()
    assert c["fill"].alpha() == 0
    assert c["border"] == QColor(GROUP_COLORS[DEFAULT_GROUP])
    assert c["content"] == QColor(GROUP_COLORS[DEFAULT_GROUP])


def test_the_badge_has_no_hardcoded_colors_when_unlinked(panel):
    """The unlinked state used to hardcode dark-theme greys, which were wrong
    on the light theme."""
    from PySide6.QtGui import QColor

    from aurantium.theme import BORDER_STRONG, FG_MUTED

    panel.set_link_group(UNLINKED)
    c = panel._badge.colors()
    assert c["content"] == QColor(FG_MUTED)
    assert c["border"] == QColor(BORDER_STRONG)


def test_the_unlinked_badge_is_a_link_glyph_not_a_dash(panel):
    panel.set_link_group(UNLINKED)
    assert panel._badge.text() == ""
    assert panel._badge._group == UNLINKED
    linked_w = None
    panel.set_link_group("B")
    linked_w = panel._badge.sizeHint().width()
    panel.set_link_group(UNLINKED)
    assert panel._badge.sizeHint().width() < linked_w  # glyph only, no letter


def test_hovered_the_glyph_turns_with_the_letter(panel):
    """Hovered, the badge fills with the group colour; the glyph used to keep
    that same colour and vanish into the fill while the letter turned dark."""
    from PySide6.QtGui import QColor

    from aurantium.theme import BG

    panel.set_link_group("B")
    panel._badge.set_hover(1.0)
    c = panel._badge.colors()
    assert c["fill"] == QColor(GROUP_COLORS["B"])
    assert c["content"] == QColor(BG)  # glyph and letter are both painted in it


def test_the_glyph_is_painted_in_the_content_colour(panel, qapp):
    from PySide6.QtGui import QColor

    from aurantium.theme import BG

    panel.set_link_group("B")
    badge = panel._badge
    badge.resize(badge.sizeHint())
    badge.set_hover(1.0)
    img = badge.grab().toImage()
    fill, bg = QColor(GROUP_COLORS["B"]), QColor(BG)
    # inside the glyph's box there are pixels near BG, not only the fill
    box = [img.pixelColor(x, y) for x in range(4, 4 + badge.ICON) for y in range(2, img.height() - 2)]
    dist = lambda a, b: abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
    assert any(dist(px, bg) < dist(px, fill) for px in box)


def test_the_hover_fill_eases_in_and_out(panel, motion_on):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent

    badge = panel._badge
    badge.enterEvent(QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))
    assert badge._anim.state() == badge._anim.State.Running
    assert badge._anim.endValue() == 1.0 and badge.hover < 1.0
    badge._anim.setCurrentTime(badge._anim.duration() // 2)
    mid = badge.hover
    assert 0.0 < mid < 1.0
    badge.leaveEvent(QEvent(QEvent.Type.Leave))
    # leaving mid-fill retargets from where it is, never snapping to full first
    assert badge._anim.startValue() == mid and badge._anim.endValue() == 0.0


def test_reduced_motion_fills_at_once(panel, monkeypatch):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent

    from aurantium import motion

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    panel._badge.enterEvent(QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))
    assert panel._badge.hover == 1.0


def test_a_second_flash_survives_the_first_animation_being_collected(panel, motion_on):
    """Same DeleteWhenStopped defect as the notifier: pulsing twice called
    .stop() on an animation Qt had already destroyed, so the second and every
    later propagated symbol raised instead of flashing."""
    from PySide6.QtCore import QCoreApplication, QEvent

    panel._header.pulse("#f5a623")
    # DeleteWhenStopped defers the delete; the event loop is what destroys it
    panel._header._anim.stop()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    panel._header.pulse("#4a90d9")  # must not raise

    assert panel._header.flash > 0.0


def test_reduced_motion_leaves_no_lingering_tint(panel, monkeypatch):
    """The reduced-motion branch used to set the tint to full and leave it
    there — nothing ever cleared it, so the header stayed permanently coloured
    after the first propagated symbol. A persistent artifact is worse than no
    animation: the user asked for less motion and got a stuck visual state."""
    from aurantium import motion

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    panel._header.pulse("#f5a623")
    assert panel._header.flash == 0.0


def test_reduced_motion_still_shows_which_symbol_arrived(panel, monkeypatch):
    """Dropping the tint loses no information: the header prints the symbol,
    which is the thing the flash was pointing at."""
    from aurantium import motion
    from aurantium.symbol_context import SymbolContext

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    SymbolContext.instance().set_symbol(DEFAULT_GROUP, "MSFT", source=None)
    assert panel._symbol_lbl.text() == "MSFT"
