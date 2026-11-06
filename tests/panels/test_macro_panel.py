"""The redesigned Macro / Rates panel: gauges, the curve, spreads, positioning.

Data never comes from the network: payloads are published straight onto a
fresh hub (the ``svc`` fixture), exactly as the providers would publish them.
"""

from __future__ import annotations

import re

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from shiboken6 import isValid

from aurantium import panel as panel_module
from aurantium.components import DEFAULT_GAUGES, Gauge
from aurantium.datahub import DataHub
from aurantium.macro_model import SPREAD_REASON_MISSING
from aurantium.theme import DOWN
from aurantium.undo import UndoStack

# -- fixture payloads -------------------------------------------------------------

US_NOW = [
    [0.0833, 4.52], [0.25, 4.48], [0.5, 4.40], [1.0, 4.28], [2.0, 4.31],
    [3.0, 4.18], [5.0, 4.09], [7.0, 4.12], [10.0, 4.22], [20.0, 4.55], [30.0, 4.49],
]
JP_NOW = [[1.0, 0.45], [2.0, 0.62], [5.0, 0.98], [10.0, 1.52], [20.0, 2.21], [30.0, 2.55], [40.0, 2.80]]


def _shift(points, bp):
    return [[y, round(r + bp / 100.0, 4)] for y, r in points]


def _curve(code, points, source, as_of, complete=True):
    return {"code": code, "points": points, "complete": complete, "observed": len(points),
            "sources": [source], "as_of": as_of}


def _hist(code, source, days):
    return {"code": code, "days": days, "sources": [source], "as_of": days[-1][0] if days else ""}


US_DAYS = [
    ["2025-11-05", _shift(US_NOW, 30)],
    ["2026-10-05", _shift(US_NOW, -12)],
    ["2026-10-29", _shift(US_NOW, -3)],
    ["2026-11-04", _shift(US_NOW, -1)],
    ["2026-11-05", US_NOW],
]

POLICY = {
    "countries": [
        {"code": "US", "label": "United States", "policy": 4.00, "prev": 4.25,
         "last_change": "2026-09", "direction": "down", "citation": "BIS · U.S. Treasury",
         "has_curve": True},
        {"code": "JP", "label": "Japan", "policy": 0.50, "prev": 0.25,
         "last_change": "2025-01", "direction": "up", "citation": "BIS · Japan MOF",
         "has_curve": True},
    ],
    "partial": [],
    "sources": ["BIS"],
    "as_of": "2026-10",
}


def _cot(market, net, prev, report="2026-11-03"):
    history = [[f"2025-{(i % 12) + 1:02d}-01", net - 5000 + i * 300, 0] for i in range(40)]
    history.append([report, net, 0])
    return {"market": market, "report_date": report, "noncommercial_net": net,
            "noncommercial_net_prev": prev, "bias": "bullish" if net > 0 else "bearish",
            "history": history}


#: what the pre-redesign panel saved with its defaults (captured from
#: ``git show 23004e2:aurantium/panels/macro.py`` — settings() of a fresh panel),
#: plus the rows a user had added through its Edit dialog
PRE_CHANGE_SETTINGS = {
    "tenors": [
        [0.25, "3M", "^IRX"],
        [5.0, "5Y", "^FVX"],
        [10.0, "10Y", "^TNX"],
        [30.0, "30Y", "^TYX"],
    ],
    "instruments": [
        ["Dollar Index", "DX-Y.NYB"],
        ["10Y Real Yield", "fred:DFII10"],
        ["EUR/USD", "EURUSD=X"],
        ["30Y Treasury Yield", "^TYX"],
        ["10Y Treasury Yield", "^TNX"],
    ],
    "cftc": [
        ["Gold", "gold"],
        ["Silver", "silver"],
        ["Copper", "copper"],
        ["Brent Crude", "brent"],
        ["Henry Hub NatGas", "natgas"],
        ["S&P 500 (E-mini)", "sp500"],
    ],
}


# -- harness ---------------------------------------------------------------------


def _flush():
    QTest.qWait(0)
    QCoreApplication.processEvents()


def _publish(topic, payload):
    DataHub.instance().publish(topic, payload)
    _flush()


def _publish_error(topic, message):
    DataHub.instance().publish_error(topic, message)
    _flush()


@pytest.fixture
def make(svc, qapp, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    UndoStack.instance()._stack.clear()
    made = []

    def _make(size=(900, 900), settings=None):
        from aurantium.panels.macro import MacroPanel

        p = MacroPanel()
        p.build()
        p.set_link_group("A")
        if settings is not None:
            p.restore(settings)
        p.resize(*size)
        p.show()
        _flush()
        made.append(p)
        return p

    yield _make
    for p in made:
        if isValid(p):
            p.close()
            p.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    UndoStack.instance()._stack.clear()


def _feed_us(p=None):
    _publish("rates:policy", POLICY)
    _publish("rates:curve:US", _curve("US", US_NOW, "U.S. Treasury", "2026-11-05"))
    _publish("rates:curvehist:US", _hist("US", "U.S. Treasury", US_DAYS))
    _publish("quote:DX-Y.NYB", {"price": 99.12, "change": -0.2, "change_pct": -0.21})
    _publish("history:DX-Y.NYB:1mo:1d", {"t": [1, 2, 3], "c": [98.0, 98.7, 99.12]})
    _publish("quote:^VIX", {"price": 17.4, "change": 0.5, "change_pct": 2.96})
    for market, net, prev in (("gold", 210000, 205000), ("silver", -12000, -9000),
                              ("copper", 30000, 31000), ("brent", 150000, 150000),
                              ("natgas", -80000, -82000)):
        _publish(f"cftc:{market}", _cot(market, net, prev))


def _index(p, kind, code):
    return next(i for i, g in enumerate(p.gauge_strip.gauges()) if (g.kind, g.code) == (kind, code))


# -- settings migration -----------------------------------------------------------


def test_restore_real_pre_change_settings(make):
    p = make(settings=PRE_CHANGE_SETTINGS)
    s = p.settings()
    assert set(s) == {"country", "comparisons", "gauges", "cftc"}
    assert s["country"] == "US"
    gauges = [tuple(g) for g in s["gauges"]]
    # the defaults first, in order …
    assert gauges[: len(DEFAULT_GAUGES)] == [(g.kind, g.code, g.label) for g in DEFAULT_GAUGES]
    extras = gauges[len(DEFAULT_GAUGES):]
    # … then what the user added that the defaults don't show: the dollar is
    # already a default, the 10Y is the country's own 10Y gauge
    assert ("fred", "DFII10", "10Y Real Yield") in extras
    assert ("quote", "EURUSD=X", "EUR/USD") in extras
    assert ("curve", "US:30", "US 30Y") in extras
    assert not any(g[1] in ("^TNX", "^TYX", "US:10", "DX-Y.NYB") for g in extras)
    assert len(extras) == 3
    # positioning keeps its markets and order
    assert s["cftc"] == PRE_CHANGE_SETTINGS["cftc"]
    # and the panel is subscribed to what it now shows, never a Yahoo yield
    topics = p._topics
    assert "rates:curve:US" in topics and "rates:policy" in topics
    assert "fred:DFII10" in topics and "cftc:sp500" in topics
    assert not any(t.startswith("quote:^T") or t.startswith("quote:^IRX") for t in topics)


def test_restore_round_trips_and_never_raises(make):
    p = make()
    p.restore({"country": "JP", "comparisons": ["1W", "1Y"],
               "gauges": [["quote", "^VIX", "VIX"], ["curve", "*:10", "10Y"]],
               "cftc": [["Gold", "gold"]]})
    s = p.settings()
    assert s["country"] == "JP" and s["comparisons"] == ["1W", "1Y"]
    assert s["gauges"] == [["quote", "^VIX", "VIX"], ["curve", "*:10", "10Y"]]
    assert s["cftc"] == [["Gold", "gold"]]
    for junk in (None, [], "x", {"gauges": "nope", "cftc": 3, "country": 7},
                 {"instruments": [[1], "x", None]}, {"tenors": [["a"]]}):
        p.restore(junk)  # must not raise
    assert p.settings()["country"] == "JP"


def test_legacy_tenors_alone_keep_the_defaults(make):
    p = make(settings={"tenors": [[2.0, "2Y", "^XYZ"]]})
    assert [tuple(g) for g in p.settings()["gauges"]] == [
        (g.kind, g.code, g.label) for g in DEFAULT_GAUGES
    ]


# -- gauges ---------------------------------------------------------------------------


def test_gauges_read_their_sources(make):
    p = make()
    _feed_us()
    strip = p.gauge_strip
    pol = _index(p, "policy", "*")
    assert strip.reading(pol).value == "4.00%"
    assert "cut 25bp" in strip.reading(pol).note and "Oct 2026" in strip.reading(pol).note
    ten = _index(p, "curve", "*:10")
    assert strip.reading(ten).value == "4.22%"
    assert strip.reading(ten).change == "+1bp"
    assert strip.display_label(ten) == "US 10Y"
    s210 = _index(p, "curve", "*:2-10")
    assert strip.reading(s210).value == "-9 bp"
    assert strip.reading(s210).note == "inverted"
    assert strip.note_ink(s210) == DOWN
    assert strip.reading(s210).note_tone == "down"
    dxy = _index(p, "quote", "DX-Y.NYB")
    assert strip.reading(dxy).value == "99.12"
    assert strip.reading(dxy).change == "-0.21%"
    assert strip.reading(dxy).spark == (98.0, 98.7, 99.12)
    # nothing yet for MOVE: its tile is still a skeleton
    assert strip.is_loading(_index(p, "quote", "^MOVE"))


def test_country_switch_moves_rate_gauges_only(make):
    p = make()
    _feed_us()
    strip = p.gauge_strip
    before_market = [(g.code, strip.reading(i)) for i, g in enumerate(strip.gauges()) if g.kind == "quote"]
    p.curve_view.pick_country("JP")
    _publish("rates:curve:JP", _curve("JP", JP_NOW, "Japan MOF", "2026-11-05"))
    assert p.settings()["country"] == "JP"
    assert "rates:curve:JP" in p._topics and "rates:curve:US" not in p._topics
    # the rate gauges now read Japan …
    pol = _index(p, "policy", "*")
    assert strip.display_label(pol) == "BoJ rate"
    assert strip.reading(pol).value == "0.50%"
    ten = _index(p, "curve", "*:10")
    assert strip.display_label(ten) == "JGB 10Y"
    assert strip.reading(ten).value == "1.52%"
    assert strip.display_label(_index(p, "curve", "*:2-10")) == "JGB 2s10s"
    # … the gauges themselves (and so the settings) did not change …
    assert [g for g in strip.gauges()] == list(DEFAULT_GAUGES)
    # … and the market gauges are untouched
    after_market = [(g.code, strip.reading(i)) for i, g in enumerate(strip.gauges()) if g.kind == "quote"]
    assert after_market == before_market
    # Japan's MOF curve has no 3M: the 3m10y spread says so instead of a number
    s3m = p.spreads[1]
    assert s3m.value_text() == "—"
    assert SPREAD_REASON_MISSING.format(tenor="3M") in s3m.toolTip()
    assert p.spreads[0].value_text() == "+90 bp"


def test_country_switch_is_undoable(make):
    p = make()
    p.curve_view.pick_country("JP")
    assert p.settings()["country"] == "JP"
    assert UndoStack.instance().undo() == "switch country"
    assert p.settings()["country"] == "US"
    assert p.curve_view.country() == "US"


def test_gauge_edit_is_undoable(make, monkeypatch):
    from aurantium.components.symbol_catalog import CatalogEntry
    from aurantium.panels import macro as macro_mod

    p = make()
    _feed_us()
    strip = p.gauge_strip
    original = p.settings()["gauges"]

    picks = iter([CatalogEntry("Bund 10Y", "XM:10", "curve", "Rates")])
    monkeypatch.setattr(macro_mod, "open_add_picker", lambda *a, **k: next(picks))
    strip.add_requested.emit()
    assert p.settings()["gauges"][-1] == ["curve", "XM:10", "Bund 10Y"]
    assert "rates:curve:XM" in p._topics   # a fixed-country gauge fetches its curve

    strip.remove_gauge(_index(p, "quote", "^VIX"))
    assert not any(g[1] == "^VIX" for g in p.settings()["gauges"])
    assert strip.move_gauge(0, 3)
    assert p.settings()["gauges"][2][0] == "policy"

    assert UndoStack.instance().undo() == "edit gauges"   # the move
    assert p.settings()["gauges"][0][0] == "policy"
    assert UndoStack.instance().undo() == "edit gauges"   # the remove
    assert any(g[1] == "^VIX" for g in p.settings()["gauges"])
    # the restored tile keeps its reading: no skeleton flash on undo
    assert strip.reading(_index(p, "quote", "^VIX")).value == "17.40"
    assert UndoStack.instance().undo() == "edit gauges"   # the add
    assert p.settings()["gauges"] == original
    assert strip.gauges() == [Gauge(*g) for g in original]
    assert "rates:curve:XM" not in p._topics


def test_picker_offers_fred_only_with_a_key(make, monkeypatch):
    p = make()
    kinds = {e.kind for e in p._picker_catalog()}
    assert "fred" not in kinds and {"curve", "quote", "policy"} <= kinds
    monkeypatch.setenv("FRED_API_KEY", "x")
    assert "fred" in {e.kind for e in p._picker_catalog()}


def test_fred_gauge_shows_its_release(make):
    p = make(settings={"gauges": [["fred", "UNRATE", "Unemployment"]]})
    _publish_error("fred:UNRATE", "FRED_API_KEY not set (free key: fred.stlouisfed.org)")
    assert p.gauge_strip.reading(0).note == "needs a FRED key"
    _publish("fred:UNRATE", {"points": [["2026-08-01", 4.2], ["2026-09-01", 4.3]], "title": "x"})
    r = p.gauge_strip.reading(0)
    assert (r.value, r.change, r.change_sign) == ("4.30", "+0.10", 1)
    assert r.note == "Sep 2026 release"


def test_market_gauge_click_drives_the_link_group_rates_do_not(make):
    p = make()
    p.gauge_strip.gauge_clicked.emit(Gauge("quote", "GC=F", "Gold"))
    assert p.current_symbol == "GC=F"
    p.gauge_strip.gauge_clicked.emit(Gauge("curve", "*:10", "10Y"))
    assert p.current_symbol == "GC=F"


# -- curve, spreads, status ------------------------------------------------------------


def test_curve_states_follow_the_data(make):
    p = make()
    assert p.curve_view.state() == "loading"
    _publish_error("rates:curve:US", "boom")
    assert p.curve_view.state() == "error"
    _publish("rates:curve:US", _curve("US", US_NOW, "U.S. Treasury", "2026-11-05"))
    assert p.curve_view.state() == "curve"
    p.curve_view.pick_country("CN")
    assert p.curve_view.state() == "none"
    assert p.spreads[0].value_text() == "—"


def test_citation_and_as_of_shown(make):
    p = make()
    _feed_us()
    # the curve cites its own source with its own date, without BIS
    src = p.curve_view.source_text()
    assert src.startswith("U.S. Treasury") and "2026-11-05" in src and "BIS" not in src
    # the header names every source with its own as-of — never one global date
    status = p.status_text()
    assert "BIS Oct" in status
    assert re.search(r"U\.S\. Treasury 5 Nov", status)
    assert "CFTC 3 Nov" in status
    assert p._status_lbl.text() == status


def test_spreads_row_reads_state_and_year(make):
    p = make()
    _feed_us()
    two, three = p.spreads
    assert two.name() == "US 2s10s" and two.value_text() == "-9 bp" and two.state() == "inverted"
    assert len(two.series()) == len(US_DAYS)
    assert three.name() == "US 3m10y" and three.value_text() == "-26 bp"


# -- positioning --------------------------------------------------------------------


def test_positioning_rows_and_percentile(make):
    p = make()
    _feed_us()
    bars = p.positioning
    assert bars.markets() == ["gold", "silver", "copper", "brent", "natgas"]
    assert bars.net_text(0) == "+210.0K"
    assert bars.wow_text(0) == "+5.0K"
    assert not bars.marker_rect(0).isNull()


def test_market_click_opens_cot_history_on_that_market(make, monkeypatch):
    calls = []
    monkeypatch.setattr(panel_module, "open_panel_hook", lambda *a: calls.append(a))
    p = make()
    p.positioning.market_clicked.emit("gold")
    assert calls[-1] == ("cot_history", "A", {"market": "gold"})
    assert p.current_symbol == "GC=F"
    p.positioning.market_clicked.emit("sp500")
    assert calls[-1] == ("cot_history", "A", {"market": "sp500"})


def test_every_pickable_market_lands_in_cot_history(svc, qapp):
    """Whatever Macro lists, Speculator Positioning can show — so a click
    never leaves it on the wrong market."""
    from aurantium.panels.cot_history import CotHistoryPanel
    from aurantium.panels.macro import _MARKET_CHOICES

    c = CotHistoryPanel()
    c.build()
    try:
        for market, _label in _MARKET_CHOICES:
            c.restore({"market": market})
            assert c.settings()["market"] == market
        c.on_symbol("^GSPC")
        assert c.settings()["market"] == "sp500"
        c.on_symbol("BTC-USD")
        assert c.settings()["market"] == "bitcoin"
    finally:
        c.close()
        c.deleteLater()


def test_positioning_edit_is_undoable(make, monkeypatch):
    from aurantium.panels import macro as macro_mod

    p = make()
    entries = []

    def pick(_parent, catalog, **_k):
        entries.append([e.code for e in catalog])
        return next(e for e in catalog if e.code == "bitcoin")

    monkeypatch.setattr(macro_mod, "open_add_picker", pick)
    p.positioning.add_requested.emit()
    assert "gold" not in entries[0]          # already listed: not offered again
    assert p.settings()["cftc"][-1] == ["Bitcoin", "bitcoin"]
    assert "cftc:bitcoin" in p._topics
    p.positioning.remove_row(0)
    assert p.settings()["cftc"][0][1] == "silver"
    assert UndoStack.instance().undo() == "edit positioning"
    assert p.settings()["cftc"][0][1] == "gold"
    assert UndoStack.instance().undo() == "edit positioning"
    assert [m for _l, m in p.settings()["cftc"]] == ["gold", "silver", "copper", "brent", "natgas"]


# -- nothing a user would have to decode --------------------------------------------


#: a Yahoo code (^VIX, GC=F, EURUSD=X, DX-Y.NYB), a FRED id (DFII10,
#: IR3TIB01GBM156N, UNRATE…) or the "*" country placeholder
_CODE = re.compile(
    r"\^|=F\b|=X\b|DX-Y|\b(?=[A-Z0-9]*\d)[A-Z0-9]{6,}\b"
    r"|\b(UNRATE|FEDFUNDS|CPIAUCSL|MORTGAGE30US)\b|\*"
)


def test_no_codes_in_visible_text(make):
    p = make()
    p.set_symbol("GC=F")   # a market gauge clicked: the header still names no code
    _feed_us()
    texts = []
    for w in p.findChildren(QLabel) + p.findChildren(QPushButton):
        if w.isVisibleTo(p) and w.text():
            texts.append(w.text())
    strip = p.gauge_strip
    for i in range(len(strip.gauges())):
        texts.append(strip.display_label(i))
        r = strip.reading(i)
        if r is not None:
            texts += [r.note]
    texts += [p.positioning.tooltip_at(p.positioning.row_rect(i).center()).split("\n")[0]
              for i in range(len(p.positioning.markets()))]
    texts += [w.name() for w in p.spreads]
    texts.append(p.curve_view.country_button_text())
    bad = [t for t in texts if _CODE.search(t)]
    assert not bad, bad


# -- fix round 1 ------------------------------------------------------------------


def test_country_switch_never_shows_the_old_countrys_numbers(make):
    p = make()
    _feed_us()
    strip = p.gauge_strip
    p.curve_view.pick_country("JP")          # nothing published for Japan yet
    for code in ("*", "*:10", "*:2-10"):
        i = _index(p, "policy" if code == "*" else "curve", code)
        if code != "*":                       # BIS carries JP already: that one may read
            assert strip.is_loading(i), code
    pol = strip.reading(_index(p, "policy", "*"))
    assert pol is None or pol.value == "0.50%"
    us_numbers = {"4.22%", "-9 bp", "-26 bp", "+1bp"}
    shown = [strip.reading(i) for i in range(len(strip.gauges()))]
    assert not any(r and r.value in us_numbers for r in shown)
    assert all(w.value_text() == "…" for w in p.spreads)
    assert p.curve_view.state() == "loading"
    assert "U.S. Treasury" not in p.status_text()


def test_restore_unreadable_gauges_and_comparisons_fall_back(make):
    p = make(settings={"gauges": [["quote", 5, "x"], "junk"], "comparisons": ["5Y", "1W"]})
    s = p.settings()
    assert [tuple(g) for g in s["gauges"]] == [(g.kind, g.code, g.label) for g in DEFAULT_GAUGES]
    assert s["comparisons"] == ["1W"]


def test_picker_shows_no_codes(make, monkeypatch):
    from aurantium.components import list_editor
    from aurantium.panels import macro as macro_mod

    monkeypatch.setenv("FRED_API_KEY", "x")
    p = make()
    seen = []

    def fake(parent, catalog, **kw):
        dlg = list_editor._AddPickerDialog(catalog, kw.get("allow_free_text", True),
                                           kw.get("title", ""), parent,
                                           show_codes=kw.get("show_codes", True))
        for text in ("", "move", "real", "dollar", "gold"):
            dlg._refresh(text)
            for i in range(dlg.results.count()):
                seen.append(dlg.results.item(i).text())
                seen.append(dlg.results.item(i).toolTip())
        dlg.deleteLater()
        return None

    monkeypatch.setattr(macro_mod, "open_add_picker", fake)
    p.gauge_strip.add_requested.emit()
    p.positioning.add_requested.emit()
    assert seen
    bad = [t for t in seen if _CODE.search(t)]
    assert not bad, bad


@pytest.mark.parametrize("market, symbol", [
    ("gold", "GC=F"), ("sp500", "ES=F"), ("bitcoin", "BTC-USD"), ("euro_fx", "EURUSD=X"),
])
def test_every_positioning_click_broadcasts_a_symbol(make, monkeypatch, market, symbol):
    monkeypatch.setattr(panel_module, "open_panel_hook", lambda *a: None)
    p = make()
    p.positioning.market_clicked.emit(market)
    assert p.current_symbol == symbol


def test_restore_to_another_country_never_asks_for_the_us_curve(svc, qapp):
    from aurantium.panels.macro import MacroPanel

    asked = []
    hub = DataHub.instance()
    real = hub.subscribe

    def spy(owner, topic, *a, **k):
        asked.append(topic)
        return real(owner, topic, *a, **k)

    hub.subscribe = spy
    try:
        p = MacroPanel()
        p.build()
        p.restore({"country": "JP"})
        p.show()
        _flush()
        assert "rates:curve:JP" in asked
        assert not any(t.endswith(":US") for t in asked), asked
        q = MacroPanel()
        q.build()
        assert not [t for t in asked if t.endswith(":US")]
        q.show()                               # no restore: the first show subscribes
        _flush()
        assert "rates:curve:US" in asked
    finally:
        hub.subscribe = real
        for w in (p, q):
            w.close()
            w.deleteLater()


def test_panel_unsubscribe_drops_one_topic(make):
    p = make()
    assert "cftc:gold" in p._topics
    p.unsubscribe("cftc:gold")
    assert "cftc:gold" not in p._topics
    assert "cftc:gold" not in DataHub.instance().subscribed_topics()


def test_adding_a_gauge_already_shown_reveals_it(make, monkeypatch):
    from aurantium.components.symbol_catalog import CatalogEntry
    from aurantium.panels import macro as macro_mod

    p = make()
    before = p.settings()["gauges"]
    # "US 10Y" on a US panel is the country 10Y tile already on the strip
    monkeypatch.setattr(macro_mod, "open_add_picker",
                        lambda *a, **k: CatalogEntry("US 10Y", "US:10", "curve", "Rates"))
    p.gauge_strip.add_requested.emit()
    assert p.settings()["gauges"] == before
    assert p.gauge_strip.highlighted() == _index(p, "curve", "*:10")
    assert not UndoStack.instance().can_undo()


def test_country_entries_add_plainly_named_gauges(make, monkeypatch):
    from aurantium.panels import macro as macro_mod

    p = make(settings={"gauges": [["quote", "^VIX", "VIX"]]})
    entry = next(e for e in p._picker_catalog() if e.code == "*:0.25-10")
    monkeypatch.setattr(macro_mod, "open_add_picker", lambda *a, **k: entry)
    p.gauge_strip.add_requested.emit()
    g = p.gauge_strip.gauges()[-1]
    assert (g.kind, g.code, g.label) == ("curve", "*:0.25-10", "3m10y")
    tip = p.gauge_strip.tooltip_at(p.gauge_strip.tile_rect(1).center())
    assert tip.splitlines()[0] == "US 3m10y"


def test_policy_note_never_says_zero_bp(make):
    p = make()
    flat = {**POLICY, "countries": [{**POLICY["countries"][0], "prev": 4.00}]}
    _publish("rates:policy", flat)
    note = p.gauge_strip.reading(_index(p, "policy", "*")).note
    assert "0bp" not in note
