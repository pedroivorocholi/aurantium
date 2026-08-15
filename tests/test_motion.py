"""Motion policy and the tick flash.

aurantium's whole motion surface is small on purpose, and most of it is
invisible when it regresses — a flash that stops firing, or one that fires on
every refresh instead of on a real change, both look like "nothing happened".
"""

import pytest

from aurantium import motion


# -- policy ----------------------------------------------------------------

def test_durations_stay_inside_the_interface_budget():
    """Past ~300ms an interface control starts to feel like it lags the user."""
    for name in ("FLASH_MS", "NOTICE_IN_MS", "NOTICE_OUT_MS"):
        assert getattr(motion, name) <= 300, name


def test_the_notice_leaves_more_slowly_than_it_arrives():
    """It arrives as the system answering the user; it leaves unhurried."""
    assert motion.NOTICE_OUT_MS > motion.NOTICE_IN_MS


def test_reduced_motion_collapses_a_duration_to_zero(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    assert motion.duration(240) == 0


def test_normal_motion_keeps_the_duration(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    assert motion.duration(240) == 240


def test_animations_default_to_on_when_the_os_query_fails(monkeypatch):
    """Failing to read an accessibility setting must not silently strip motion
    from every user."""
    monkeypatch.setattr(
        motion, "_windows_animations_on", lambda: (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setattr(
        motion, "_macos_animations_on", lambda: (_ for _ in ()).throw(OSError())
    )
    assert motion.animations_enabled() is True


# -- the maximize path must not animate ------------------------------------

def test_panel_maximize_has_no_animation():
    """F11/Esc are keyboard-initiated and repeated all day; animating them adds
    perceived latency to the user's own keystroke, and the thing being faded is
    the data they just asked to see larger."""
    import inspect

    from aurantium import app as app_mod

    src = inspect.getsource(app_mod)
    assert "_fade_in" not in src
    # no live opacity-effect animation (the comment explaining its removal
    # names the class, so match the constructor call rather than the word)
    assert "QGraphicsOpacityEffect(" not in src


# -- tick flash ------------------------------------------------------------

@pytest.fixture
def table(qapp, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    from PySide6.QtWidgets import QTableWidgetItem

    from aurantium.components.market_table import MarketTable

    t = MarketTable(1, 1)
    t.setItem(0, 0, QTableWidgetItem("100.00"))
    return t


def _entry(table, item):
    return next((e for e in table._flashes if e[0] is item), None)


def test_flash_tints_the_cell(table):
    item = table.item(0, 0)
    table.flash_cell(item, +1.5)
    assert _entry(table, item) is not None


def test_flash_uses_the_up_color_for_a_rise(table):
    from aurantium.theme import UP

    from PySide6.QtGui import QColor

    item = table.item(0, 0)
    table.flash_cell(item, +1.5)
    assert _entry(table, item)[2] == QColor(UP)


def test_flash_uses_the_down_color_for_a_fall(table):
    from aurantium.theme import DOWN

    from PySide6.QtGui import QColor

    item = table.item(0, 0)
    table.flash_cell(item, -1.5)
    assert _entry(table, item)[2] == QColor(DOWN)


def test_flash_decays_and_releases_the_cell(table):
    item = table.item(0, 0)
    table.flash_cell(item, +1.0)
    for _ in range(200):  # more frames than the decay needs
        table._step_flashes()
    assert _entry(table, item) is None
    assert not table._flash_timer.isActive()


def test_flash_is_skipped_under_reduced_motion(table, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    item = table.item(0, 0)
    table.flash_cell(item, +1.0)
    assert not table._flashes


def test_flash_ignores_a_missing_direction(table):
    table.flash_cell(table.item(0, 0), None)
    assert not table._flashes


def test_flashing_the_same_cell_twice_retargets_rather_than_stacking(table):
    item = table.item(0, 0)
    table.flash_cell(item, +1.0)
    for _ in range(4):
        table._step_flashes()
    table.flash_cell(item, +1.0)
    assert len(table._flashes) == 1
    assert _entry(table, item)[1] == 1.0


def test_a_rebuilt_row_does_not_break_the_decay(table):
    """Rows get torn down and rebuilt under a running flash; the decay must
    drop the dead item rather than raise on it."""
    item = table.item(0, 0)
    table.flash_cell(item, +1.0)
    table.setRowCount(0)  # deletes the item Qt-side
    table._step_flashes()  # must not raise
    table._step_flashes()


# -- the shared fade helper -------------------------------------------------

@pytest.fixture
def widget(qapp, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    w.resize(100, 40)
    return w


def test_fade_in_shows_the_widget_immediately(widget):
    """It has to be on screen for the fade to be visible at all."""
    widget.hide()
    motion.fade(widget, 1.0)
    assert widget.isVisibleTo(widget.parentWidget()) or widget.isVisible()


def test_fade_out_hides_the_widget_when_it_finishes(widget):
    widget.show()
    motion.fade(widget, 0.0, hide_when_done=True)
    anim = motion.fade_animation(widget)
    anim.setCurrentTime(anim.duration())
    assert not widget.isVisible()


def test_fading_the_same_widget_twice_reuses_one_animation(widget):
    """A fresh DeleteWhenStopped animation per call is what broke the notice
    card: the next call stopped an object Qt had already destroyed."""
    widget.show()
    motion.fade(widget, 1.0)
    first = motion.fade_animation(widget)
    motion.fade(widget, 0.0)
    assert motion.fade_animation(widget) is first


def test_a_fade_retargets_from_where_it_is_now(widget):
    """Interrupting mid-fade must continue from the value on screen, not jump
    back to the start — otherwise a quick in/out flickers."""
    widget.show()
    motion.fade(widget, 1.0)
    anim = motion.fade_animation(widget)
    anim.setCurrentTime(anim.duration() // 2)
    midpoint = motion.fade_opacity(widget)
    motion.fade(widget, 0.0)
    assert motion.fade_animation(widget).startValue() == pytest.approx(
        midpoint, abs=0.05
    )


def test_reduced_motion_lands_on_the_end_state_without_animating(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    widget.show()
    motion.fade(widget, 0.0, hide_when_done=True)
    assert not widget.isVisible()


# -- panel entrance ---------------------------------------------------------

def test_a_newly_added_panel_fades_in(qapp, monkeypatch):
    """Adding a panel snapped it into existence at full opacity while the
    splitters shoved its neighbours aside. A short fade gives the eye something
    to follow to the thing that just arrived."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    from aurantium.panel import Panel

    class _P(Panel):
        panel_id = "entrance"
        panel_title = "Entrance"

        def build(self):
            pass

    p = _P()
    p.build()
    motion.fade_in_panel(p)
    assert motion.fade_opacity(p) < 1.0, "should start transparent and rise"
    anim = motion.fade_animation(p)
    assert anim.endValue() == 1.0


def test_the_entrance_starts_visible_enough_to_be_a_fade_not_a_pop(qapp, monkeypatch):
    """Nothing in the real world appears from nothing — starting from zero
    reads as a pop, not an arrival."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    from aurantium.panel import Panel

    class _P(Panel):
        panel_id = "entrance2"
        panel_title = "Entrance2"

        def build(self):
            pass

    p = _P()
    p.build()
    motion.fade_in_panel(p)
    assert motion.fade_opacity(p) >= 0.3


def test_the_entrance_is_skipped_under_reduced_motion(qapp, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    from aurantium.panel import Panel

    class _P(Panel):
        panel_id = "entrance3"
        panel_title = "Entrance3"

        def build(self):
            pass

    p = _P()
    p.build()
    motion.fade_in_panel(p)
    assert motion.fade_opacity(p) == 1.0


# -- first-run dialogs ------------------------------------------------------

def test_a_dialog_fades_in_from_transparent(qapp, monkeypatch):
    """The guide and the workspace chooser are the rare/first-run tier — the
    one place the delight budget belongs. They snapped into existence."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    from PySide6.QtWidgets import QDialog

    d = QDialog()
    motion.fade_in_dialog(d)
    assert d.windowOpacity() < 1.0
    assert motion.dialog_animation(d).endValue() == 1.0


def test_the_dialog_entrance_is_a_modal_length_beat(qapp, monkeypatch):
    """Modals get a longer beat than inline UI — they are a context switch,
    not a control responding to a click."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    from PySide6.QtWidgets import QDialog

    d = QDialog()
    motion.fade_in_dialog(d)
    assert 200 <= motion.dialog_animation(d).duration() <= 500


def test_reduced_motion_leaves_the_dialog_fully_opaque(qapp, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    from PySide6.QtWidgets import QDialog

    d = QDialog()
    motion.fade_in_dialog(d)
    assert d.windowOpacity() == 1.0


# -- review findings --------------------------------------------------------

def test_a_finished_entrance_leaves_no_graphics_effect(qapp, monkeypatch):
    """A QGraphicsOpacityEffect makes Qt render the widget to an offscreen
    pixmap on EVERY paint, for as long as it is installed. Leaving it on a
    panel after a 180ms entrance charges that cost for the rest of the session,
    on exactly the widgets that repaint most."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    w.show()
    motion.fade(w, 1.0)
    anim = motion.fade_animation(w)
    anim.setCurrentTime(anim.duration())
    motion.settle_pending_detach()
    assert w.graphicsEffect() is None


def test_a_faded_out_widget_keeps_its_effect(qapp, monkeypatch):
    """Only detach at full opacity — a widget resting at 0 still needs the
    effect to stay transparent."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    w.show()
    motion.fade(w, 0.0)
    anim = motion.fade_animation(w)
    anim.setCurrentTime(anim.duration())
    motion.settle_pending_detach()
    assert w.graphicsEffect() is not None


def test_fading_again_after_a_detach_still_works(qapp, monkeypatch):
    """The effect and its animation are recreated on demand; a stale reference
    to a deleted effect is the bug that broke the notice card."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    w.show()
    motion.fade(w, 1.0)
    motion.fade_animation(w).setCurrentTime(motion.fade_animation(w).duration())
    motion.settle_pending_detach()
    motion.fade(w, 0.0)  # must not raise
    assert motion.fade_opacity(w) <= 1.0


def test_the_tick_flash_decays_on_the_house_curve(table):
    """It fell off linearly while every other animation used OutQuint — so it
    lingered at mid-intensity and then cut, instead of being bright on the
    frame you look at and gone quickly after."""
    item = table.item(0, 0)
    table.flash_cell(item, +1.0)
    from aurantium.components.market_table import FLASH_MS, _FLASH_TICK_MS

    frames_to_half = 0
    while _entry(table, item) and _entry(table, item)[1] > 0.5:
        table._step_flashes()
        frames_to_half += 1
    linear_frames = (FLASH_MS / 2) / _FLASH_TICK_MS
    assert frames_to_half < linear_frames * 0.8, (
        "a strong ease-out should pass half intensity well before the midpoint"
    )
