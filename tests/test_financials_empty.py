"""Financials never sits blank: an empty statement says why — a fund, an
index, or simply nothing from Yahoo — and names no API key, because none is
involved."""

import pytest


@pytest.fixture
def panel(qapp):
    from aurantium.panels.fundamentals import FundamentalsPanel

    p = FundamentalsPanel()
    p.build()
    p._ctx_symbol = None
    yield p
    p.deleteLater()


def _empty_payload(**extra):
    blk = {"columns": [], "rows": []}
    d = {k: {"annual": blk, "quarterly": blk} for k in ("income", "balance", "cashflow")}
    d.update(extra)
    return d


def _with_symbol(panel, monkeypatch, sym):
    from aurantium.panel import Panel

    monkeypatch.setattr(Panel, "current_symbol", property(lambda self: sym))


def test_etf_explains_itself(panel, monkeypatch):
    _with_symbol(panel, monkeypatch, "SPY")
    panel._data = _empty_payload(quote_type="ETF")
    title, hint = panel._empty_reason()
    assert "SPY is an ETF" in title
    assert "Key Metrics" in hint
    assert "API" not in title + hint


def test_company_without_statements(panel, monkeypatch):
    _with_symbol(panel, monkeypatch, "NEWCO")
    panel._data = _empty_payload(quote_type="EQUITY")
    title, hint = panel._empty_reason()
    assert title == "No annual income statement for NEWCO"
    assert "no API key needed" in hint


def test_old_cached_empty_payload_is_refetched_once():
    from aurantium.panels.fundamentals import FundamentalsPanel as F

    assert F._needs_reason_refetch(_empty_payload())
    assert not F._needs_reason_refetch(_empty_payload(quote_type="ETF"))
    full = _empty_payload()
    full["income"]["annual"] = {"columns": ["2025"], "rows": [["Revenue", 1.0]]}
    assert not F._needs_reason_refetch(full)
