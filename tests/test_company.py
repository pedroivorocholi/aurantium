"""Company panel: Profile, Key Metrics, Financials and Earnings as tabs of one
panel. Pins what the merge promises: the old ids still open (on the right
tab), saved layouts fold into one panel, only the visible tab fetches, and
the Profile's tiles drill into the deeper tabs."""

import pytest

from aurantium import valuation as V


@pytest.fixture
def company(qapp):
    from aurantium.panels.company import CompanyPanel

    p = CompanyPanel()
    p.build()
    yield p
    p.close()
    p.deleteLater()


def _record_symbols(company):
    seen = {}
    for key, child in company.children_by_key.items():
        child.on_symbol = lambda s, k=key: seen.setdefault(k, []).append(s)
    return seen


def test_registry_lists_company_not_the_four(qapp):
    from aurantium.panel import PanelRegistry
    import aurantium.panels.company  # noqa: F401

    assert PanelRegistry.get("company") is not None
    for old in ("profile", "key_metrics", "fundamentals", "earnings"):
        assert PanelRegistry.get(old) is None
        assert PanelRegistry.resolve(old) == ("company", {"tab": old})
        assert old in PanelRegistry.known_ids()


def test_only_the_visible_tab_fetches(company):
    seen = _record_symbols(company)
    company._apply_symbol("AAPL")
    assert seen == {"profile": ["AAPL"]}
    company.show_tab("earnings")
    assert seen["earnings"] == ["AAPL"]
    assert "key_metrics" not in seen
    # a new symbol goes to the tab on screen; others catch up when shown
    company._apply_symbol("MSFT")
    assert seen["earnings"] == ["AAPL", "MSFT"]
    company.show_tab("profile")
    assert seen["profile"] == ["AAPL", "MSFT"]


def test_children_are_unlinked_and_headerless(company):
    from aurantium.panel import UNLINKED

    for child in company.children_by_key.values():
        assert child._host is company
        assert child.link_group == UNLINKED
        assert child._header.isHidden()


def test_status_follows_the_visible_tab(company):
    km = company.child("key_metrics")
    km.set_status("hidden tab")
    assert company._status_lbl.text() == ""
    company.show_tab("key_metrics")
    assert company._status_lbl.text() == "hidden tab"
    km.set_error("boom")
    assert "boom" in company._state_lbl.text()


def test_profile_tile_drills_into_key_metrics(company):
    from aurantium.panels.key_metrics import KeyMetricsPanel

    _record_symbols(company)
    km = company.child("key_metrics")
    km._on_metrics(V.build_metrics({"symbol": "TEST", "info": {}}))
    prof = company.child("profile")
    prof._on_profile({"name": "Test Co", "pe_trailing": 25.0, "beta": 1.1, "employees": 10})
    assert prof.tiles["pe"].clickable and prof.tiles["beta"].clickable
    assert not prof.tiles["employees"].clickable          # nowhere to go
    assert not prof.tiles["mktcap"].clickable             # no figure to explain
    prof.open_tile("beta")
    assert company.current_key == "key_metrics"
    assert isinstance(company.child("key_metrics"), KeyMetricsPanel)
    assert km._group == "Risk"
    prof.open_tile("eps")  # no EPS figure → not clickable, but the route exists
    assert company.current_key == "earnings"


def test_profile_renders_and_clears(company):
    prof = company.child("profile")
    prof._on_profile({
        "name": "Apple Inc.", "sector": "Technology", "industry": "Consumer Electronics",
        "website": "https://www.apple.com", "market_cap": 3.1e12, "dividend_yield_pct": 0.44,
        "week52_low": 100.0, "week52_high": 200.0,
        "officers": [{"name": "Tim Cook", "title": "CEO"}],
    })
    assert prof.tiles["mktcap"].value.text() == "3.10T"
    assert prof.tiles["div"].value.text() == "0.44%"
    assert "apple.com" in prof.web_lbl.text()
    assert "Technology › Consumer Electronics" in prof.sector_lbl.text()
    prof._on_quote({"price": 150.0, "change": 1.5, "change_pct": 1.0})
    assert prof.range_bar.position() == pytest.approx(0.5)
    assert prof.price_lbl.text() == "150.00"
    prof._clear()
    assert prof.tiles["mktcap"].value.text() == "—"
    assert prof.range_bar.position() is None


def test_settings_round_trip(qapp, company):
    from aurantium.panels.company import CompanyPanel

    company.show_tab("key_metrics")
    company.child("key_metrics").add_custom("alpha")
    s = company.settings()
    assert s["tab"] == "key_metrics"
    other = CompanyPanel()
    other.build()
    other.restore(s)
    assert other.current_key == "key_metrics"
    assert other.child("key_metrics")._custom == ["alpha"]
    other.close()


def test_migrate_layout_folds_per_link_group():
    from aurantium.panels.company import migrate_layout

    doc = {"panels": [
        {"instance": "chart#1", "panel_id": "chart", "link_group": "A"},
        {"instance": "fundamentals#1", "panel_id": "fundamentals", "link_group": "A",
         "settings": {"statement": "balance", "period": "quarterly"}},
        {"instance": "key_metrics#1", "panel_id": "key_metrics", "link_group": "A",
         "settings": {"group": "Risk"}},
        {"instance": "profile#1", "panel_id": "profile", "link_group": "B"},
    ]}
    new = migrate_layout(doc)
    assert doc["panels"][1]["panel_id"] == "fundamentals"  # input untouched
    ids = [(p["instance"], p["panel_id"]) for p in new["panels"]]
    assert ids == [("chart#1", "chart"), ("fundamentals#1", "company"), ("profile#1", "company")]
    a = new["panels"][1]["settings"]
    assert a["tab"] == "fundamentals"
    assert a["tabs"] == {"fundamentals": {"statement": "balance", "period": "quarterly"},
                         "key_metrics": {"group": "Risk"}}
    assert new["panels"][2]["settings"] == {"tab": "profile", "tabs": {}}
    assert migrate_layout({"panels": [{"panel_id": "chart"}]})["panels"][0]["panel_id"] == "chart"


def test_shipped_presets_still_load(qapp):
    from aurantium.panel import discover_panels
    from aurantium.paths import EXT_DIR
    from aurantium.presets import available_presets

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    names = {p.name for p in available_presets()}
    assert "Equity Research" in names
