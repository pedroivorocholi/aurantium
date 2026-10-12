"""Key Metrics: the valuation maths and the panel's click-for-history flow.

The maths runs on synthetic yfinance-shaped frames, so each ratio can be
checked by hand. The panel tests pin what the user relies on: every shown row
opens a history window, and that window follows the linked symbol.
"""

import numpy as np
import pandas as pd
import pytest

from aurantium import valuation as V

DAYS = pd.bdate_range("2021-01-04", "2026-09-18", tz="America/New_York")


def _prices(price=100.0, div=0.5):
    close = pd.Series(price, index=DAYS)
    divs = pd.Series(0.0, index=DAYS)
    # quarterly dividends on the first business day of Feb/May/Aug/Nov
    paid = set()
    for d in DAYS:
        if d.month in (2, 5, 8, 11) and (d.year, d.month) not in paid:
            paid.add((d.year, d.month))
            divs[d] = div
    return pd.DataFrame({"Close": close, "Dividends": divs})


def _calendar():
    # quarterly reports: actual EPS 1.00 each, estimate 0.95; one upcoming
    dates = pd.date_range("2021-01-28", "2026-10-28", freq="91D", tz="America/New_York")
    est = pd.Series(0.95, index=dates)
    act = pd.Series(1.0, index=dates)
    act[dates > pd.Timestamp("2026-09-18", tz="America/New_York")] = np.nan
    return pd.DataFrame({"EPS Estimate": est, "Reported EPS": act}).iloc[::-1]


def _estimates(q0=1.1, q1=1.1, y0=4.4, y1=4.8):
    return pd.DataFrame({"avg": [q0, q1, y0, y1]}, index=["0q", "+1q", "0y", "+1y"])


def _statements():
    ends = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])
    inc = pd.DataFrame(
        {
            e: {
                "Total Revenue": 1000.0,
                "Gross Profit": 400.0,
                "EBITDA": 250.0,
                "Normalized EBITDA": 260.0,
                "Operating Income": 200.0,
                "Net Income Common Stockholders": 100.0,
            }
            for e in ends
        }
    )
    bal = pd.DataFrame(
        {
            e: {
                "Stockholders Equity": 500.0,
                "Total Assets": 2000.0,
                "Total Debt": 300.0,
                "Cash Cash Equivalents And Short Term Investments": 100.0,
                "Ordinary Shares Number": 10.0,
                "Current Assets": 800.0,
                "Current Liabilities": 400.0,
                "Inventory": 100.0,
                "Retained Earnings": 300.0,
                "Total Liabilities Net Minority Interest": 1500.0,
            }
            for e in ends
        }
    )
    cf = pd.DataFrame({e: {"Free Cash Flow": 80.0} for e in ends})
    return inc, bal, cf


def _raw(**over):
    inc, bal, cf = _statements()
    raw = {
        "symbol": "TEST",
        "info": {"trailingPE": 25.0, "lastFiscalYearEnd": int(pd.Timestamp("2025-12-31").timestamp())},
        "prices": _prices(),
        "earnings_dates": _calendar(),
        "earnings_estimate": _estimates(),
        "income": {"annual": inc, "quarterly": None},
        "balance": {"annual": bal, "quarterly": None},
        "cashflow": {"annual": cf, "quarterly": None},
    }
    raw.update(over)
    return raw


def _m(payload, mid):
    return next(m for m in payload["metrics"] if m["id"] == mid)


def test_price_ratios_from_statements():
    p = V.build_metrics(_raw())
    assert _m(p, "mktcap")["value"] == pytest.approx(1000.0)      # 100 × 10 shares
    assert _m(p, "ev")["value"] == pytest.approx(1200.0)          # + 300 − 100
    assert _m(p, "pb")["value"] == pytest.approx(2.0)             # 100 ÷ (500/10)
    assert _m(p, "ps")["value"] == pytest.approx(1.0)
    assert _m(p, "ev_ebitda")["value"] == pytest.approx(1200 / 250)
    assert _m(p, "fcf_yield")["value"] == pytest.approx(0.08)
    assert _m(p, "adj_ebitda")["value"] == pytest.approx(260.0)
    assert _m(p, "gross_margin")["value"] == pytest.approx(0.4)
    assert _m(p, "roe")["value"] == pytest.approx(0.2)


def test_pe_ttm_uses_last_four_reported_eps():
    p = V.build_metrics(_raw())
    pe = _m(p, "pe_ttm")
    assert pe["value"] == pytest.approx(25.0)                     # 100 ÷ 4 × 1.00
    assert pe["quote"] == 25.0
    assert len(pe["history"]["t"]) > 200                          # weekly, ~5 years
    assert _m(p, "eps_ttm")["value"] == pytest.approx(4.0)


def test_pe_ntm_sums_next_four_quarters():
    p = V.build_metrics(_raw())
    # next four from today: 0q 1.1, +1q 1.1, then FY0 has 3 reported quarters
    # after the FY end, so the ramp over FY1 supplies the rest
    eps = _m(p, "eps_ntm")["value"]
    assert 4.0 < eps < 5.0
    assert _m(p, "pe_ntm")["value"] == pytest.approx(100 / eps)
    # past points use the pre-report consensus (0.95 × 4)
    early = _m(p, "pe_ntm")["history"]["v"][0]
    assert early == pytest.approx(100 / 3.8)


def test_ramp_sums_to_total_and_grows():
    q = V._ramp(2.0, 10.0)
    assert sum(q) == pytest.approx(10.0)
    assert q == sorted(q)
    assert V._ramp(-1.0, 8.0) == [2.0] * 4


def test_dividend_yields():
    p = V.build_metrics(_raw())
    assert _m(p, "dps_ttm")["value"] == pytest.approx(2.0)
    assert _m(p, "div_yield_ttm")["value"] == pytest.approx(0.02)
    assert _m(p, "div_yield_ntm")["value"] == pytest.approx(0.02)
    # a declared (not yet paid) raise shows in the forward yield only
    p2 = V.build_metrics(_raw(info={"dividendRate": 2.4}))
    assert _m(p2, "div_yield_ntm")["value"] == pytest.approx(0.024)
    assert _m(p2, "div_yield_ttm")["value"] == pytest.approx(0.02)


def test_stats_percentile():
    t = list(range(0, 100 * 86400, 86400))
    v = [float(i) for i in range(100)]
    st = V.stats(t, v, days=10**6)
    assert st["pctile"] == 1.0 and st["low"] == 0.0 and st["high"] == 99.0


def test_etf_like_payload_degrades():
    """No calendar, estimates or statements: price-only metrics survive,
    Yahoo's own quotes fill in, nothing raises."""
    p = V.build_metrics(
        {"symbol": "ETF", "info": {"trailingPE": 20.0}, "prices": _prices()}
    )
    assert _m(p, "pe_ttm")["value"] == 20.0
    assert _m(p, "pe_ttm")["history"]["t"] == []
    assert _m(p, "div_yield_ttm")["value"] == pytest.approx(0.02)
    assert _m(p, "revenue")["value"] is None


def test_negative_earnings_blank_the_multiple():
    cal = _calendar()
    cal["Reported EPS"] = cal["Reported EPS"] * -1
    p = V.build_metrics(_raw(earnings_dates=cal, info={}))
    assert _m(p, "pe_ttm")["value"] is None


# -- panel ------------------------------------------------------------------------


@pytest.fixture
def panel(qapp):
    from aurantium.panels.key_metrics import KeyMetricsPanel

    p = KeyMetricsPanel()
    p.build()
    yield p
    if p._dialog is not None:
        p._dialog.close()
    p.deleteLater()


def _row_of(panel, mid):
    from aurantium.panels.key_metrics import _METRIC_ROLE

    for r in range(panel.table.rowCount()):
        item = panel.table.item(r, 0)
        if item is not None and item.data(_METRIC_ROLE) == mid:
            return r
    raise KeyError(mid)


def test_panel_renders_groups_and_opens_history(panel):
    panel._on_metrics(V.build_metrics(_raw()))
    r = _row_of(panel, "pe_ttm")
    assert panel.table.item(r, 1).text() == "25.00x"
    assert panel.table.item(r, 5).text().endswith("%")
    panel._on_cell_clicked(r, 1)
    dlg = panel._dialog
    assert dlg is not None and dlg.isVisible()
    assert dlg.metric_id == "pe_ttm"
    assert dlg.plot.isVisible()
    # clicking a group header does nothing
    panel._on_cell_clicked(0, 0)
    assert dlg.metric_id == "pe_ttm"


def test_history_window_follows_symbol(panel):
    panel._on_metrics(V.build_metrics(_raw()))
    panel._on_cell_clicked(_row_of(panel, "pe_ttm"), 0)
    other = V.build_metrics(_raw(symbol="OTHR", prices=_prices(price=200.0)))
    panel._on_metrics(other)
    assert panel._dialog.title_lbl.text().startswith("OTHR")
    assert panel._dialog._ys[-1] == pytest.approx(50.0)


def test_history_window_ranges_and_copy(panel, qapp):
    panel._on_metrics(V.build_metrics(_raw()))
    dlg = panel.open_history(_m(panel._data, "div_yield_ttm"))
    full = len(dlg._xs)
    dlg._on_range(0)  # 1Y
    assert 40 <= len(dlg._xs) < full
    dlg._copy()
    text = qapp.clipboard().text()
    assert text.startswith("Date\tTEST Div Yield (TTM)")


def test_etf_hides_unreported_rows(panel):
    panel._on_metrics(V.build_metrics({"symbol": "ETF", "info": {}, "prices": _prices()}))
    with pytest.raises(KeyError):
        _row_of(panel, "revenue")
    _row_of(panel, "div_yield_ttm")


# -- expanded catalogue ------------------------------------------------------------


def test_catalogue_is_consistent():
    ids = [m[0] for m in V.CATALOGUE]
    assert len(ids) == len(set(ids)), "duplicate metric id"
    assert {m[2] for m in V.CATALOGUE} <= set(V.GROUPS)
    assert len(ids) >= 90
    assert all(m[4] for m in V.CATALOGUE), "every metric explains its method"


def test_company_gets_the_wide_set():
    p = V.build_metrics(_raw())
    have = {m["id"] for m in p["metrics"] if m["value"] is not None}
    for mid in ("roic", "roce", "current_ratio", "asset_turn", "ret_1y", "vol_1y",
                "earn_yield", "ev_ebit", "fcf_margin", "div_growth", "altman_z"):
        assert mid in have, mid
    assert "Fund" not in p["groups"]
    assert _m(p, "ret_1y")["value"] == pytest.approx(0.0)   # flat price
    assert _m(p, "current_ratio")["value"] == pytest.approx(2.0)     # 800 / 400
    assert _m(p, "quick_ratio")["value"] == pytest.approx(1.75)      # (800-100) / 400
    assert _m(p, "roce")["value"] == pytest.approx(200 / 1600)       # EBIT / (TA − CL)
    # 1.2·0.2 + 1.4·0.15 + 3.3·0.1 + 0.6·(1000/1500) + 1.0·0.5
    assert _m(p, "altman_z")["value"] == pytest.approx(1.68)


def test_fund_payload():
    info = {"quoteType": "ETF", "totalAssets": 5e11, "netExpenseRatio": 0.09,
            "ytdReturn": 12.5, "category": "Large Blend", "navPrice": 101.0}
    p = V.build_metrics({"symbol": "ETF", "info": info, "prices": _prices()})
    assert "Fund" in p["groups"]
    assert _m(p, "expense")["value"] == pytest.approx(0.0009)   # Yahoo reports percent
    assert _m(p, "fund_ytd")["value"] == pytest.approx(0.125)
    assert _m(p, "fund_category")["value"] == "Large Blend"
    assert _m(p, "nav_prem")["value"] == pytest.approx(100 / 101 - 1)
    assert not any(m["id"] == "mktcap" for m in p["metrics"])


def test_group_filter_and_fund_notice(panel):
    info = {"quoteType": "ETF", "totalAssets": 5e11, "category": "Large Blend"}
    panel._on_metrics(V.build_metrics({"symbol": "SPY", "info": info, "prices": _prices()}))
    assert panel.notice_lbl.isVisibleTo(panel)
    assert "ETF" in panel.notice_lbl.text()
    groups = [panel.tabs.tabData(i) for i in range(panel.tabs.count())]
    assert "Fund" in groups and "Profitability" not in groups   # no rows there
    panel.tabs.setCurrentIndex(groups.index("Fund"))
    labels = [panel.table.item(r, 0).text() for r in range(panel.table.rowCount())]
    assert labels[0] == "Fund" and any("Category" in t for t in labels)
    assert panel.settings() == {"group": "Fund", "custom": []}


def test_alpha_is_the_regression_intercept():
    p = V.build_metrics(_raw())
    # a stock returning 0.5%/month + 1.2 × a noisy benchmark: alpha = 6%/yr
    months = pd.date_range("2021-01-31", "2026-08-31", freq="ME", tz="America/New_York")
    bench = pd.Series([100 * 1.01 ** i for i in range(len(months))], index=months)
    rng = np.random.default_rng(0)
    b_ret = bench.pct_change().fillna(0) + rng.normal(0, 0.03, len(months))
    s_ret = 0.005 + 1.2 * b_ret
    bench = 100 * (1 + b_ret).cumprod()
    px = 100 * (1 + s_ret).cumprod()
    a = V.rolling_alpha(px, bench)
    assert a.iloc[-1] == pytest.approx(0.06, abs=1e-6)
    assert V.rolling_beta(px, bench).iloc[-1] == pytest.approx(1.2, abs=1e-6)
    assert any(m["id"] == "alpha" for m in p["metrics"])


def test_tabs_and_custom_tab(panel):
    from aurantium.panels.key_metrics import _METRIC_ROLE, CUSTOM

    panel._on_metrics(V.build_metrics(_raw()))
    datas = [panel.tabs.tabData(i) for i in range(panel.tabs.count())]
    assert datas[0] == "" and datas[-1] == CUSTOM and "Risk" in datas
    # a group tab shows only that group
    panel.tabs.setCurrentIndex(datas.index("Valuation"))
    assert panel.table.item(0, 0).text() == "Valuation"
    with pytest.raises(KeyError):
        _row_of(panel, "roe")
    # add via the row's right-click action, then look at the Custom tab
    r = _row_of(panel, "pe_ttm")
    (text, slot), *_ = panel._row_actions(r)
    assert text == "Add to Custom"
    slot()
    panel.add_custom("roe")
    panel.tabs.setCurrentIndex(datas.index(CUSTOM))
    assert panel.edit_btn.isVisibleTo(panel)
    shown = [panel.table.item(i, 0).data(_METRIC_ROLE) for i in range(panel.table.rowCount())]
    assert shown == ["pe_ttm", "roe"]   # the user's order, no group headers
    # rows still open their history from the Custom tab
    panel._on_cell_clicked(0, 0)
    assert panel._dialog.metric_id == "pe_ttm"
    panel.remove_custom("pe_ttm")
    assert panel.table.rowCount() == 1
    # persisted and restored, unknown ids dropped
    s = panel.settings()
    assert s == {"group": CUSTOM, "custom": ["roe"]}
    panel.restore({"group": CUSTOM, "custom": ["roe", "nope"]})
    assert panel._custom == ["roe"]


def test_custom_dialog_keeps_order(qapp):
    from aurantium.panels.key_metrics import CustomMetricsDialog

    dlg = CustomMetricsDialog(["roe", "pe_ttm"])
    dlg._items["beta"].setCheckState(0, dlg._items["beta"].checkState(0).Checked)
    assert dlg.chosen() == ["roe", "pe_ttm", "beta"]
    dlg._filter("alpha")
    assert not dlg._items["alpha"].isHidden() and dlg._items["roe"].isHidden()
    dlg._clear()
    assert dlg.chosen() == []


def test_company_has_no_notice(panel):
    panel._on_metrics(V.build_metrics(_raw()))
    assert not panel.notice_lbl.isVisibleTo(panel)
