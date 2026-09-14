"""The shared panel-state vocabulary.

``Panel`` used to offer ``set_status(text)`` and nothing else, so every panel
improvised: six spellings of "loading", seventeen phrasings of "empty", and
nine panels that showed nothing at all while fetching. Meanwhile
``MarketTable`` had carried a complete, motion-budgeted loading overlay since
it was written that **no panel had ever called** — the implementation existed,
the wiring did not.

Two things are worth guarding here, and they pull in opposite directions.

**The veil must appear.** That is the feature.

**The veil must never get stuck.** A panel dimmed under "loading…" forever is
strictly worse than one that never dimmed at all: it tells the user to keep
waiting for something that is not coming. Every path that starts a fetch has to
end it — including the error path, which is how a fetch most often ends.
"""

import re
from pathlib import Path

import pytest

from aurantium import panel as panel_mod

PANELS_DIR = Path(__file__).resolve().parent.parent / "aurantium" / "panels"


class _Target:
    """Stands in for a MarketTable: records what the panel asks it to do."""

    def __init__(self):
        self.loading = None
        self.empty = None

    def set_loading(self, on):
        self.loading = on

    def set_empty_text(self, title, hint=""):
        self.empty = (title, hint)


@pytest.fixture
def panel(qapp):
    class _P(panel_mod.Panel):
        def build(self):
            pass

    p = _P()
    p.build()
    yield p
    p.deleteLater()


@pytest.fixture
def wired(panel):
    t = _Target()
    panel.register_state_target(t)
    return panel, t


# -- the veil --------------------------------------------------------------


def test_loading_reaches_the_registered_target(wired):
    """The wiring that did not exist. ``MarketTable.set_loading`` had zero
    production callers before this."""
    p, t = wired
    p.set_loading(True)
    assert t.loading is True
    p.set_loading(False)
    assert t.loading is False


def test_an_error_clears_the_veil(wired):
    """The stuck-veil case, and the likeliest one: a fetch that fails."""
    p, t = wired
    p.set_loading(True)
    p.set_error("HTTPError 429")
    assert t.loading is False
    assert "429" in p._state_lbl.text()


def test_clear_state_clears_the_veil(wired):
    p, t = wired
    p.set_loading(True)
    p.clear_state()
    assert t.loading is False
    assert p._state_lbl.text() == ""


def test_a_panel_with_no_target_still_reports_state(panel):
    """Registration is opt-in, so a panel that registers nothing must degrade
    to header-only state rather than raising."""
    panel.set_loading(True)
    assert panel._state_lbl.text() == panel_mod.LOADING
    panel.set_error("boom")  # must not raise
    panel.clear_state()


def test_empty_text_reaches_the_target(wired):
    p, t = wired
    p.set_empty("No dividend history for AAPL", "Try another symbol")
    assert t.empty == ("No dividend history for AAPL", "Try another symbol")


# -- the two slots ---------------------------------------------------------


def test_status_cannot_erase_a_state(wired):
    """The structural fix. ``profile`` ended every successful load with
    ``set_status(sector or "")``, which blanked any error posted a moment
    earlier — because both lived in the same label."""
    p, _t = wired
    p.set_error("provider down")
    p.set_status("")           # exactly what profile does on success
    assert "provider down" in p._state_lbl.text()
    p.set_status("14 holders")
    assert "provider down" in p._state_lbl.text()


def test_state_cannot_erase_the_status(wired):
    """Disjoint in both directions, or panels lose their row counts."""
    p, _t = wired
    p.set_status("14 holders")
    p.set_loading(True)
    assert p._status_lbl.text() == "14 holders"


def test_severity_drives_the_colour(wired):
    p, _t = wired
    p.set_loading(True)
    assert p._state_lbl.property("severity") == "loading"
    p.set_stale("stale · 4m")
    assert p._state_lbl.property("severity") == "stale"
    p.set_error("nope")
    assert p._state_lbl.property("severity") == "error"
    p.clear_state()
    assert p._state_lbl.property("severity") is None


def test_the_state_slot_hides_when_empty(wired):
    """It sits in a 21px header beside the symbol; an empty label that still
    takes space pushes the link badge around."""
    p, _t = wired
    assert not p._state_lbl.isVisible()
    p.set_loading(True)
    p.clear_state()
    assert not p._state_lbl.isVisible()


# -- no panel may start a fetch it cannot finish ---------------------------


def _panel_sources():
    return sorted(f for f in PANELS_DIR.glob("*.py") if not f.name.startswith("__"))


def test_every_panel_that_shows_a_veil_also_hides_it():
    """Structural guard against the failure mode that matters.

    Crude on purpose: it does not prove the clear runs on every path, only that
    a module which raises the veil has somewhere that lowers it. A panel with a
    ``set_loading(True)`` and no counterpart is unambiguously wrong, and that is
    the mistake this migration could most easily make in one of twenty-four
    files.
    """
    offenders = []
    for src in _panel_sources():
        text = src.read_text(encoding="utf-8")
        if "set_loading(True)" in text and "set_loading(False)" not in text:
            offenders.append(src.name)
    assert not offenders, f"raise the veil but never lower it: {offenders}"


def test_the_retired_loading_strings_are_gone_from_migrated_panels():
    """One spelling. ``day_brief`` had two on adjacent lines."""
    stragglers = []
    for src in _panel_sources():
        text = src.read_text(encoding="utf-8")
        if 'set_status("loading…")' in text:
            stragglers.append(src.name)
    assert not stragglers, f"still writing the old loading string: {stragglers}"


def test_the_shared_vocabulary_exists():
    assert panel_mod.LOADING == "loading…"
    assert panel_mod.NULL_GLYPH == "—"
    assert panel_mod.WARN_GLYPH == "⚠"
    assert panel_mod.EMPTY_NO_SYMBOL
