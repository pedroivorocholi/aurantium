"""Portfolio analytics converted into the preferred currency (pure helpers)."""
from aurantium.panels.portfolio import (
    concentration, iso_to_ts, left_out, portfolio_series, sector_weights,
    weighted_beta,
)

BRL = {"PETR4.SA": "BRL", "AAPL": "USD"}


def _to_pref(sym, amt, day):
    return amt * 0.18 if sym.endswith(".SA") else amt


POS = [
    {"symbol": "AAPL", "qty": 10, "cost": 150, "date": "2024-01-02"},
    {"symbol": "PETR4.SA", "qty": 100, "cost": 30, "date": "2024-01-02"},
]
LAST = {"AAPL": 200.0, "PETR4.SA": 38.0}


def test_sector_weights_convert():
    out = sector_weights(POS, LAST, {"AAPL": "Technology", "PETR4.SA": "Energy"},
                         to_pref=_to_pref)
    assert out["Technology"] == 2000.0
    assert abs(out["Energy"] - 684.0) < 1e-9


def test_suffixed_unknown_currency_is_left_out():
    cur = {"AAPL": "USD", "PETR4.SA": None}.get
    to_pref = lambda s, a, d: a if cur(s) == "USD" else None
    out = sector_weights(POS, LAST, {}, to_pref=to_pref)
    assert out == {"Unknown": 2000.0}
    assert left_out(POS, cur) == 1


def test_left_out_ignores_sold_and_known():
    sold = [{"symbol": "X", "qty": 1, "sell_date": "2024-02-01"}]
    assert left_out(sold, lambda s: None) == 0
    assert left_out(POS, BRL.get) == 0


def test_sold_position_uses_sell_date_rate():
    days = []

    def to_pref(sym, amt, day):
        days.append(day)
        return amt

    pos = [
        {"symbol": "AAPL", "qty": 1, "sell_date": "2024-03-04"},
        {"symbol": "MSFT", "qty": 1},
    ]
    last = {"AAPL": 1.0, "MSFT": 1.0}
    sector_weights(pos, last, {}, to_pref=to_pref)
    weighted_beta(pos, last, {"AAPL": 1.0, "MSFT": 1.0}, to_pref=to_pref)
    concentration(pos, last, to_pref=to_pref)
    assert days == ["2024-03-04", None] * 3


def test_none_conversion_is_skipped():
    to_pref = lambda s, a, d: None if s == "PETR4.SA" else a
    assert concentration(POS, LAST, to_pref=to_pref) == 100.0
    assert weighted_beta(POS, LAST, {"AAPL": 2.0, "PETR4.SA": 1.0},
                         to_pref=to_pref) == 2.0


def test_weighted_beta_and_concentration_convert():
    assert abs(concentration(POS, LAST, to_pref=_to_pref) - 2000 / 2684 * 100) < 1e-9
    b = weighted_beta(POS, LAST, {"AAPL": 1.0, "PETR4.SA": 2.0}, to_pref=_to_pref)
    assert abs(b - (2000 * 1 + 684 * 2) / 2684) < 1e-9


def test_series_converts_each_day_at_that_days_rate():
    t1, t2 = iso_to_ts("2024-01-02"), iso_to_ts("2024-01-03")
    pos = [{"symbol": "PETR4.SA", "qty": 10, "buy_ts": t1}]
    hist = {"PETR4.SA": ([t1, t2], [10.0, 10.0])}
    rates = {"2024-01-02": 0.2, "2024-01-03": 0.5}
    ts, vals = portfolio_series(pos, hist, to_pref=lambda s, a, d: a * rates[d])
    assert ts == [t1, t2]
    assert vals == [20.0, 50.0]


def test_series_drops_day_with_missing_rate():
    t1, t2 = iso_to_ts("2024-01-02"), iso_to_ts("2024-01-03")
    pos = [{"symbol": "PETR4.SA", "qty": 1, "buy_ts": t1}]
    hist = {"PETR4.SA": ([t1, t2], [1.0, 1.0])}
    to_pref = lambda s, a, d: None if d == "2024-01-02" else a
    assert portfolio_series(pos, hist, to_pref=to_pref) == ([t2], [1.0])


def test_without_to_pref_behaviour_is_unchanged():
    assert sector_weights(POS, LAST, {}) == {"Unknown": 2000.0 + 3800.0}
    assert concentration(POS, LAST) == 3800 / 5800 * 100
    t1 = iso_to_ts("2024-01-02")
    hist = {"AAPL": ([t1], [5.0])}
    assert portfolio_series(POS[:1], hist) == ([t1], [50.0])
