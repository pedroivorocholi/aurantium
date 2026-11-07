"""Heatmap universes and the pure tile model: rows/quotes in, sized tiles out."""

from aurantium import heatmap_model as hm
from aurantium import heatmap_universes as hu
from aurantium.components.symbol_catalog import SECTOR_ETF_ENTRIES
from aurantium.providers import screen


def _row(sym, cap, pct=1.0, price=10.0, name=None):
    return {"symbol": sym, "name": name or f"{sym} Inc", "market_cap": cap,
            "price": price, "change_pct": pct, "currency": "USD"}


def _quote(sym, pct=2.0, price=5.0):
    return {"symbol": sym, "name": f"{sym} Fund", "price": price, "change": 0.1,
            "change_pct": pct, "prev_close": 4.9, "volume": 1, "currency": "USD"}


def test_universes_are_consistent():
    assert tuple(k for k, _l, _e in hu.SECTORS) == screen.SECTORS
    assert {e for _k, _l, e in hu.SECTORS} == {c.code for c in SECTOR_ETF_ENTRIES}
    assert len(hu.INDEXES["dow"][2]) == 30 and len(hu.INDEXES["sp500"][2]) > 450
    assert hu.MEMBERS_AS_OF
    assert hu.view_label("sp500") == "S&P 500 · largest 40"
    assert hu.view_label("Technology") == "Technology"
    assert [hu.view_label(v) for v in ("sectors", "portfolio", "watchlist", "custom")] == [
        "Sectors", "Portfolio", "Watchlist", "Custom"]


def test_share_classes_merge():
    rows = [_row("GOOGL", 2e12, pct=1.0, price=100), _row("GOOG", 1e12, pct=9.0, price=101),
            _row("AAPL", 1e12)]
    tiles = {t.symbol: t for t in hm.tiles_from_screen(rows, 10)}
    assert set(tiles) == {"GOOGL", "AAPL"}
    assert tiles["GOOGL"].weight == 2e12  # whole-company cap on each class: max, not sum
    assert tiles["GOOGL"].change_pct == 1.0
    assert "Market cap 2.0T" in tiles["GOOGL"].tooltip and "Price 100.00" in tiles["GOOGL"].tooltip
    # an alias with no kept symbol present is shown under the kept symbol
    only = hm.tiles_from_screen([_row("GOOG", 1e12)], 10)
    assert [t.symbol for t in only] == ["GOOGL"]


def test_missing_members_are_skipped():
    rows = [_row("A", 5e9), _row("B", None), {"symbol": "C", "market_cap": 0}]
    assert [t.symbol for t in hm.tiles_from_screen(rows, 10)] == ["A"]


def test_limit_keeps_largest():
    rows = [_row("S", 1e9), _row("L", 9e9), _row("M", 5e9)]
    assert [t.symbol for t in hm.tiles_from_screen(rows, 2)] == ["L", "M"]


def test_non_equities_take_median_cap():
    rows = [_row("A", 1e9), _row("B", 3e9), _row("C", 9e9)]
    tiles = hm.tiles_mixed(["C", "SPY", "A", "B", "NEW"], rows, {"SPY": _quote("SPY", 0.5)})
    assert [t.symbol for t in tiles] == ["C", "SPY", "A", "B", "NEW"]
    by = {t.symbol: t for t in tiles}
    assert by["SPY"].weight == 3e9 and by["SPY"].change_pct == 0.5 and not by["SPY"].loading
    assert by["C"].weight == 9e9
    # Ruling E: missing from rows and from quotes -> kept, loading, median weight
    assert by["NEW"].loading and by["NEW"].weight == 3e9 and by["NEW"].change_pct is None


def test_all_non_equities_are_equal():
    tiles = hm.tiles_mixed(["SPY", "QQQ"], [], {"SPY": _quote("SPY")})
    assert [t.weight for t in tiles] == [1.0, 1.0]
    assert tiles[1].loading


def test_mixed_missing_equity_uses_quote():
    tiles = hm.tiles_mixed(["A", "SMALL"], [_row("A", 4e9)], {"SMALL": _quote("SMALL", -1.5)})
    assert tiles[1].weight == 4e9 and tiles[1].change_pct == -1.5


def test_mixed_alias_without_row_uses_its_own_quote():
    tiles = hm.tiles_mixed(["GOOG"], [], {"GOOG": _quote("GOOG", 1.7)})
    assert [t.symbol for t in tiles] == ["GOOGL"]
    assert not tiles[0].loading and tiles[0].change_pct == 1.7


def test_sectors_equal_until_caps_arrive():
    quotes = {"XLK": _quote("XLK", 1.2)}
    tiles = hm.tiles_sectors(quotes, {})
    assert len(tiles) == 11 and {t.weight for t in tiles} == {1.0}
    tk = next(t for t in tiles if t.symbol == "XLK")
    assert tk.sector_key == "Technology" and tk.change_pct == 1.2
    assert next(t for t in tiles if t.symbol == "XLE").loading
    assert hm.tiles_sectors({"XLK": {"price": 1.0}}, {})[9].loading  # quote without a change
    assert {t.weight for t in hm.tiles_sectors(quotes, {"Technology": 5e12})} == {1.0}
    full = {k: 1e12 for k, _l, _e in hu.SECTORS} | {"Technology": 5e12}
    sized = hm.tiles_sectors(quotes, full)
    w = {t.sector_key: t.weight for t in sized}
    assert w["Technology"] == 5e12 and w["Energy"] == 1e12


def test_portfolio_tiles_convert_and_flag_loading():
    positions = [
        {"symbol": "AAA", "qty": 10, "sell_date": None},
        {"symbol": "AAA", "qty": 5, "sell_date": None},
        {"symbol": "BBB", "qty": -2, "sell_date": None},
        {"symbol": "CCC", "qty": 1, "sell_date": None},
        {"symbol": "DDD", "qty": 1, "sell_date": None},
        {"symbol": "OLD", "qty": 9, "sell_date": "2026-01-01"},
    ]
    quotes = {"AAA": _quote("AAA", 1.0, 2.0), "BBB": _quote("BBB", -3.0, 10.0),
              "CCC": _quote("CCC", 0.0, 1.0)}
    tiles = {t.symbol: t for t in hm.tiles_portfolio(
        positions, quotes, lambda s, v: None if s == "CCC" else v * 2)}
    assert set(tiles) == {"AAA", "BBB", "CCC", "DDD"}
    assert tiles["AAA"].weight == 15 * 2.0 * 2 and tiles["BBB"].weight == 2 * 10.0 * 2
    assert tiles["CCC"].loading and tiles["CCC"].weight == 0
    assert tiles["DDD"].loading and tiles["DDD"].weight == 0


def test_a_long_sector_name_tiles_under_its_short_label():
    """A mid-size tile should show a name, not fall back to the ETF ticker;
    the tooltip keeps the full name."""
    from aurantium.heatmap_model import tiles_sectors

    comm = next(t for t in tiles_sectors({}, {}) if t.symbol == "XLC")
    assert comm.label == "Comm Services"
    assert comm.tooltip.startswith("Communication Services (XLC)")
    assert hu.view_label("Communication Services") == "Communication Services"


# -- international indexes ----------------------------------------------------

#: key -> (accepted member-count range, Yahoo suffixes every member must carry)
_INTERNATIONAL = {
    "ibov": ((70, 95), (".SA",)),
    "ftse": ((95, 105), (".L",)),
    "dax": ((40, 40), (".DE", ".PA")),  # Airbus trades in Paris
    "cac": ((40, 40), (".PA", ".AS")),  # ArcelorMittal trades in Amsterdam
    "sx5e": ((50, 50), (".DE", ".PA", ".AS", ".MI", ".MC", ".BR", ".HE", ".IR")),
    "n225": ((225, 225), (".T",)),
    "hsi": ((70, 105), (".HK",)),
    "tsx60": ((55, 62), (".TO",)),
}


def test_international_indexes_listed_in_order():
    assert list(hu.INDEXES) == ["sp500", "ndx", "dow", *_INTERNATIONAL]
    for key, ((lo, hi), suffixes) in _INTERNATIONAL.items():
        label, n, members = hu.INDEXES[key]
        assert lo <= len(members) <= hi, (key, len(members))
        assert n == 40 and len(set(members)) == len(members)
        assert all(m.endswith(suffixes) for m in members), key
        assert hu.view_label(key) == f"{label} · largest 40"
    assert hu.INDEXES["ibov"][0] == "Ibovespa" and hu.INDEXES["dax"][0] == "DAX 40"
    # Yahoo's own spellings: four-digit Hong Kong codes, dashed class shares
    assert "0700.HK" in hu.INDEXES["hsi"][2] and "0005.HK" in hu.INDEXES["hsi"][2]
    assert "RCI-B.TO" in hu.INDEXES["tsx60"][2] and "PETR4.SA" in hu.INDEXES["ibov"][2]
    # every index sits under exactly one region of the view selector
    regions = [r for r, _keys in hu.INDEX_REGIONS]
    assert regions == ["United States", "Brazil", "Europe", "Asia", "Canada"]
    assert [k for _r, keys in hu.INDEX_REGIONS for k in keys] == list(hu.INDEXES)


def _cur_row(sym, cap, cur, pct=1.0):
    return {"symbol": sym, "name": sym, "market_cap": cap, "price": 10.0,
            "change_pct": pct, "currency": cur}


def test_mixed_currency_caps_are_converted_before_layout():
    rows = [_cur_row("AAPL", 3e12, "USD"), _cur_row("PETR4.SA", 6e11, "BRL"),
            _cur_row("SHEL.L", 2e11, "GBp")]
    rates = {("BRL", "USD"): 0.2, ("GBP", "USD"): 1.25, ("USD", "GBP"): 0.8}
    seen = []

    def convert(src, dst, cap):
        """Caps arrive in the listing's base unit; ``None`` while a rate loads."""
        seen.append((src, dst))
        rate = 1.0 if src == dst else rates.get((src, dst))
        return None if rate is None else cap * rate

    syms = ["AAPL", "PETR4.SA", "SHEL.L"]
    by = {t.symbol: t for t in hm.tiles_mixed(syms, rows, {}, convert)}
    assert by["PETR4.SA"].weight == 6e11 * 0.2 and not by["PETR4.SA"].loading
    assert by["SHEL.L"].weight == 2e11 * 1.25  # GBp quotes, GBP caps: no 1/100
    assert by["AAPL"].weight == 3e12
    assert ("GBp", "USD") not in seen
    assert "BRL" in by["PETR4.SA"].tooltip  # the cap is shown in its own currency
    # no BRL rate yet: that tile waits rather than sizing reais as dollars
    del rates[("BRL", "USD")]
    by = {t.symbol: t for t in hm.tiles_mixed(syms, rows, {}, convert)}
    assert by["PETR4.SA"].loading and by["PETR4.SA"].change_pct is None
    assert by["PETR4.SA"].weight != 6e11
    assert not by["AAPL"].loading and not by["SHEL.L"].loading
    # one currency throughout: no conversion asked for at all
    seen.clear()
    tiles = hm.tiles_from_screen([_cur_row("A.SA", 2e9, "BRL"), _cur_row("B.SA", 1e9, "BRL")],
                                 40, convert)
    assert [t.weight for t in tiles] == [2e9, 1e9] and seen == []
    # a mostly-pounds index converts its odd dollar listing into pounds, the
    # fewest rates to wait on, and ranks by the converted cap
    ftse = [_cur_row("X.L", 1e9, "GBp"), _cur_row("Z.L", 5e8, "GBp"),
            _cur_row("Y.L", 1.1e9, "USD")]
    tiles = hm.tiles_from_screen(ftse, 40, convert)
    assert [t.symbol for t in tiles] == ["X.L", "Y.L", "Z.L"]
    assert tiles[1].weight == 1.1e9 * 0.8 and set(seen) == {("USD", "GBP")}
