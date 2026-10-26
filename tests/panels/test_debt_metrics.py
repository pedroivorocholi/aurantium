"""Financials ▸ Debt: the derived credit view.

Pins the arithmetic (average-debt cost, ×4 annualization for quarters, net
debt), the label fallbacks Yahoo's varying filers need, and that metrics a
filer doesn't report are dropped rather than shown as empty rows.
"""

import pytest

from aurantium.panel import NULL_GLYPH
from aurantium.panels.fundamentals import PCT, RATIO, _fmt_kind, debt_block


def _block(columns, rows):
    return {"columns": columns, "rows": rows}


COLS = ["2025-12-31", "2024-12-31"]


def _data(period="annual", income_extra=None):
    income_rows = [
        ["Interest Expense", 50.0, 40.0],
        ["EBIT", 500.0, 400.0],
        ["EBITDA", 600.0, 480.0],
    ] + (income_extra or [])
    return {
        "balance": {period: _block(COLS, [
            ["Total Debt", 1100.0, 900.0],
            ["Current Debt And Capital Lease Obligation", 100.0, 100.0],
            ["Long Term Debt And Capital Lease Obligation", 1000.0, 800.0],
            ["Capital Lease Obligations", 100.0, 100.0],
            ["Cash Cash Equivalents And Short Term Investments", 300.0, 200.0],
            ["Stockholders Equity", 2200.0, 2000.0],
        ])},
        "income": {period: _block(COLS, income_rows)},
        "cashflow": {period: _block(COLS, [
            ["Interest Paid Supplemental Data", -45.0, -38.0],
        ])},
    }


def _row(block, label):
    for row, kind in zip(block["rows"], block["kinds"]):
        if row[0] == label:
            return row[1:], kind
    raise KeyError(label)


def test_cost_of_debt_uses_average_debt():
    b = debt_block(_data(), "annual")
    cost, kind = _row(b, "Implied Cost of Debt")
    assert kind == PCT
    assert cost[0] == pytest.approx(50 / 1000)  # avg(1100, 900)
    assert cost[1] == pytest.approx(40 / 900)   # no prior period: ending debt
    ex, _ = _row(b, "Implied Cost ex Leases")
    assert ex[0] == pytest.approx(50 / 900)     # avg(1000, 800)
    cash, _ = _row(b, "Implied Cost (Cash Basis)")
    assert cash[0] == pytest.approx(45 / 1000)  # sign of cash paid ignored


def test_quarterly_flows_are_annualized():
    b = debt_block(_data("quarterly"), "quarterly")
    cost, _ = _row(b, "Implied Cost of Debt")
    assert cost[0] == pytest.approx(200 / 1000)
    lev, kind = _row(b, "Total Debt / EBITDA")
    assert kind == RATIO
    assert lev[0] == pytest.approx(1100 / 2400)
    # coverage is a same-period ratio — scale cancels
    cov, _ = _row(b, "Interest Coverage (EBIT)")
    assert cov[0] == pytest.approx(10.0)


def test_structure_and_leverage():
    b = debt_block(_data(), "annual")
    assert _row(b, "Net Debt")[0][0] == 800.0
    assert _row(b, "Net Debt / EBITDA")[0][0] == pytest.approx(800 / 600)
    assert _row(b, "Debt / Equity")[0][0] == pytest.approx(0.5)
    assert _row(b, "Debt / Capital")[0][0] == pytest.approx(1100 / 3300)


def test_negative_ebitda_blanks_leverage_multiple():
    data = _data()
    data["income"]["annual"]["rows"][2] = ["EBITDA", -10.0, 480.0]
    lev, _ = _row(debt_block(data, "annual"), "Total Debt / EBITDA")
    assert lev[0] is None and lev[1] is not None


def test_unreported_metrics_are_dropped():
    b = debt_block(_data(), "annual")
    labels = [r[0] for r in b["rows"]]
    assert "Debt Issued" not in labels
    assert len(b["notes"]) == len(b["rows"]) == len(b["kinds"])


def test_label_fallbacks_and_missing_total():
    data = _data()
    data["balance"]["annual"]["rows"] = [
        ["Current Debt", 50.0, 50.0],
        ["Long Term Debt", 450.0, 350.0],
    ]
    data["income"]["annual"]["rows"] = [["Interest Expense Non Operating", 25.0, 20.0]]
    b = debt_block(data, "annual")
    assert _row(b, "Total Debt")[0] == [500.0, 400.0]
    assert _row(b, "Implied Cost of Debt")[0][0] == pytest.approx(25 / 450)


def test_empty_payload():
    assert debt_block({}, "annual")["rows"] == []


def test_formatting():
    assert _fmt_kind(0.0512, PCT) == "5.12%"
    assert _fmt_kind(3.456, RATIO) == "3.46x"
    assert _fmt_kind(None, PCT) == NULL_GLYPH
