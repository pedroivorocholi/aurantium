"""Every panel ships with its quick guide, and the F1 window can't drift.

The rule: a panel declares a ``GUIDE`` (``guide.PanelGuide``) next to its own
code, the F1 window generates the panel's page from it, and the panel's ``?``
opens that page. A registered panel without a guide is effectively a hidden
one — nobody is told what it does — so this suite fails on any registered
panel (aliases excluded) whose guide is missing or empty, and on any F1 page
that points at a panel that no longer exists.

It also holds the Shortcuts page to the code in both directions: every key
sequence the app binds is listed, and nothing is listed that isn't bound.
"""

from __future__ import annotations

import importlib.util

import pytest

from aurantium import guide
from aurantium.guide import PanelGuide

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture(scope="module")
def registry():
    from aurantium.panel import PanelRegistry, discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    return PanelRegistry


def _guide_of(cls) -> PanelGuide | None:
    g = getattr(cls, "GUIDE", None)
    return g if isinstance(g, PanelGuide) else None


# -- every panel has a guide ----------------------------------------------------

def test_every_registered_panel_declares_a_guide(registry):
    missing = [m.id for m in registry.all() if _guide_of(m.cls) is None]
    assert not missing, f"panels without a GUIDE: {missing}"


def test_no_guide_is_empty(registry):
    empty = []
    for meta in registry.all():
        g = _guide_of(meta.cls)
        if g is None or g.is_empty() or not (g.links or g.interactions or g.fields):
            empty.append(meta.id)
    assert not empty, f"panels with an empty GUIDE: {empty}"


def test_every_hosted_tab_declares_a_guide(registry):
    missing = []
    for meta in registry.all():
        for key, _label, child in getattr(meta.cls, "HOSTED_TABS", ()):
            g = _guide_of(child)
            if g is None or g.is_empty():
                missing.append(f"{meta.id}:{key}")
    assert not missing, f"hosted tabs without a GUIDE: {missing}"


def test_aliases_are_not_panels_of_their_own(registry):
    """Retired ids resolve to a host's tab; they never get a page."""
    page_ids = {p.id for p in guide.build_pages()}
    for alias in registry._aliases:
        assert alias not in page_ids


def test_the_example_panel_shows_forkers_the_pattern():
    from aurantium.paths import EXT_DIR

    path = EXT_DIR / "user_panels" / "_example_macro.py"
    spec = importlib.util.spec_from_file_location("_guide_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    classes = [v for v in vars(module).values()
               if isinstance(v, type) and getattr(v, "panel_id", "") == "example_macro"]
    assert classes and _guide_of(classes[0]) is not None
    assert not _guide_of(classes[0]).is_empty()


# -- every F1 page points at something real -------------------------------------

def test_every_panel_page_has_a_matching_panel(registry):
    for page in guide.build_pages():
        if page.id in guide.STATIC_IDS:
            continue
        host, _, tab = page.id.partition(":")
        meta = registry.get(host)
        assert meta is not None, f"F1 page {page.id!r} names no registered panel"
        if tab:
            keys = [k for k, _l, _c in getattr(meta.cls, "HOSTED_TABS", ())]
            assert tab in keys, f"F1 page {page.id!r} names no tab of {host!r}"


def test_every_registered_panel_has_a_page(registry):
    page_ids = {p.id for p in guide.build_pages()}
    for meta in registry.all():
        assert meta.id in page_ids


def test_the_dialog_opens_on_a_panel_page_and_resolves_aliases(registry):
    from aurantium.onboarding_dialog import OnboardingDialog

    dlg = OnboardingDialog(None, "watchlist")
    assert dlg.current_section() == "watchlist"
    # a retired id opens its host's tab page
    assert dlg.show_section("key_metrics")
    assert dlg.current_section() == "company:key_metrics"
    # every page renders
    for sid in dlg.section_ids():
        assert dlg.show_section(sid)
        assert dlg._browser.toPlainText().strip()
    dlg.deleteLater()


def test_search_finds_a_panel_by_what_it_does(registry):
    from aurantium.onboarding_dialog import OnboardingDialog

    dlg = OnboardingDialog(None)
    dlg._search.setText("contango")
    visible = [sid for sid, item in dlg._items.items() if not item.isHidden()]
    assert "futures_curve" in visible
    assert "watchlist" not in visible
    dlg.deleteLater()


def test_a_panel_question_mark_opens_its_own_page(registry, monkeypatch):
    opened = []
    monkeypatch.setattr(guide, "open_guide_hook", opened.append)
    from aurantium.panels.company import CompanyPanel
    from aurantium.panels.watchlist import WatchlistPanel

    w = WatchlistPanel()
    w.build()
    w._guide_btn.click()
    c = CompanyPanel()
    c.build()
    c.show_tab("fundamentals")
    c._guide_btn.click()
    assert opened == ["watchlist", "company:fundamentals"]


# -- the Shortcuts page matches the bindings --------------------------------------

def _app_bindings(registry) -> set[str]:
    from PySide6.QtGui import QAction, QKeySequence, QShortcut

    from aurantium.app import MainWindow
    from aurantium.panels.chart import ChartPanel

    win = MainWindow()
    chart = ChartPanel()
    chart.build()
    keys: set[str] = set()
    fmt = QKeySequence.SequenceFormat.PortableText
    for owner in (win, chart):
        for act in owner.findChildren(QAction):
            for seq in act.shortcuts():
                if not seq.isEmpty():
                    keys.add(seq.toString(fmt))
        for sc in owner.findChildren(QShortcut):
            if not sc.key().isEmpty():
                keys.add(sc.key().toString(fmt))
    win._notifier.deleteLater()
    win.deleteLater()
    return keys


def test_every_bound_shortcut_is_on_the_shortcuts_page(registry):
    listed = guide.bound_shortcuts()
    missing = sorted(_app_bindings(registry) - listed)
    assert not missing, f"bound but not in the F1 Shortcuts page: {missing}"


def test_nothing_on_the_shortcuts_page_is_unbound(registry):
    bound = _app_bindings(registry)
    phantom = sorted(guide.bound_shortcuts() - bound)
    assert not phantom, f"listed in F1 but bound nowhere: {phantom}"
