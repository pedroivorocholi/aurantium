from PySide6.QtCore import QSettings

from aurantium.portfolio_store import PortfolioStore, normalize_positions


def _store(tmp_path):
    return PortfolioStore(QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat))


def test_add_persists_across_instances(tmp_path):
    _store(tmp_path).add("AAPL", 10, 150, "2026-11-01")
    assert _store(tmp_path).positions()[0] == {
        "symbol": "AAPL", "qty": 10.0, "cost": 150.0, "date": "2026-11-01",
        "sell_date": None, "sell_price": None,
    }


def test_open_positions_excludes_sold(tmp_path):
    s = _store(tmp_path)
    s.add("AAPL", 1, 100, "2026-11-01")
    s.add("NVDA", 1, 100, "2026-11-01")
    s.mark_sold([0], {"AAPL": 190.0}, "2026-11-02")
    assert [p["symbol"] for p in s.open_positions()] == ["NVDA"]
    assert s.positions()[0]["sell_price"] == 190.0


def test_mark_sold_twice_keeps_first_sale(tmp_path):
    s = _store(tmp_path)
    s.add("AAPL", 1, 100, "2026-11-01")
    s.mark_sold([0], {"AAPL": 190.0}, "2026-11-02")
    s.mark_sold([0], {"AAPL": 200.0}, "2026-11-03")
    p = s.positions()[0]
    assert (p["sell_price"], p["sell_date"]) == (190.0, "2026-11-02")


def test_remove_and_changed_signal(tmp_path):
    s = _store(tmp_path)
    hits = []
    s.changed.connect(lambda: hits.append(1))
    s.add("AAPL", 1, 100, "2026-11-01")
    assert len(hits) == 1
    s.remove([0])
    assert len(hits) == 2
    assert s.is_empty()


def test_fill_market_cost_only_fills_missing(tmp_path):
    s = _store(tmp_path)
    s.add("AAPL", 1, None, "2026-11-01")
    s.add("AAPL", 1, 5, "2026-11-01")
    hits = []
    s.changed.connect(lambda: hits.append(1))
    s.fill_market_cost("AAPL", 120.0)
    assert [p["cost"] for p in s.positions()] == [120.0, 5.0]
    assert len(hits) == 1
    s.fill_market_cost("AAPL", 130.0)
    assert len(hits) == 1


def test_normalize_accepts_legacy_lists():
    out = normalize_positions([
        ["aapl", 2, 100], ["", 1, 1], ["X", "bad", 1],
        ["MSFT", 1, None, "2026-01-01", "2026-02-01", 410.5],
    ])
    assert [p["symbol"] for p in out] == ["AAPL", "MSFT"]
    assert out[1]["sell_date"] == "2026-02-01" and out[1]["sell_price"] == 410.5
    assert out[0]["cost"] == 100.0 and out[0]["date"] is None


def test_positions_returns_copies(tmp_path):
    s = _store(tmp_path)
    s.add("AAPL", 1, 100, "2026-11-01")
    s.positions()[0]["symbol"] = "ZZZ"
    assert s.positions()[0]["symbol"] == "AAPL"
