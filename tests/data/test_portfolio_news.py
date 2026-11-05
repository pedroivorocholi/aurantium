"""Pure helpers behind the News panel's Portfolio mode."""
from aurantium import portfolio_news as pn


def _pos(sym, qty, cost=None):
    return {"symbol": sym, "qty": qty, "cost": cost, "date": None,
            "sell_date": None, "sell_price": None}


def test_codec_round_trip_odd_symbols():
    book = [{"symbol": s, "weight": 0.25, "sector": None}
            for s in ["EURUSD=X", "^GSPC", "BRK-B", "PETR4.SA"]]
    assert pn.decode_book(pn.encode_book(book)) == book
    assert " " not in pn.encode_book(book)


def test_decode_garbage():
    assert pn.decode_book("garbage") == []
    assert pn.decode_book('{"a": 1}') == []
    assert pn.decode_book("") == []


def test_build_book_price_beats_cost_and_sorts():
    book = pn.build_book(
        [_pos("AAA", 10, 5.0), _pos("BBB", 10, 1.0)],
        {"AAA": 30.0, "BBB": None}, {"AAA": "Technology", "BBB": None})
    assert [h["symbol"] for h in book] == ["AAA", "BBB"]
    assert book[0]["weight"] == 0.97  # 300 / 310
    assert book[1]["weight"] == 0.03  # cost fallback: 10
    assert book[0]["sector"] == "Technology" and book[1]["sector"] is None


def test_build_book_duplicates_summed():
    book = pn.build_book([_pos("AAA", 1, 10.0), _pos("AAA", 1, 10.0),
                          _pos("BBB", 2, 10.0)], {}, {})
    assert {h["symbol"]: h["weight"] for h in book} == {"AAA": 0.5, "BBB": 0.5}


def test_build_book_unvalued_takes_mean_value():
    book = pn.build_book([_pos("A", 1, 60.0), _pos("B", 1, 20.0),
                          _pos("C", 1), _pos("D", 1)], {}, {})
    w = {h["symbol"]: h["weight"] for h in book}
    assert w == {"A": 0.38, "B": 0.12, "C": 0.25, "D": 0.25}


def test_build_book_all_missing_equal():
    book = pn.build_book([_pos("A", 1), _pos("B", 1), _pos("C", 1), _pos("D", 1)],
                         {}, {})
    assert all(h["weight"] == 0.25 for h in book)
    assert pn.build_book([], {}, {}) == []


def test_sector_weights_and_queries():
    book = [{"symbol": "A", "weight": 0.5, "sector": "Technology"},
            {"symbol": "B", "weight": 0.3, "sector": None},
            {"symbol": "C", "weight": 0.05, "sector": "Energy"},
            {"symbol": "D", "weight": 0.15, "sector": "Technology"}]
    sw = pn.sector_weights(book)
    assert sw == {"Technology": 0.65, "Energy": 0.05}
    assert sw["Energy"] < pn.SECTOR_MIN_WEIGHT
    assert set(pn.SECTOR_QUERIES) >= {"Technology", "Financial Services", "Real Estate"}
    assert len(pn.SECTOR_QUERIES) == 11
    assert len(pn.MACRO_QUERIES) == 7
    assert set(pn.MACRO_BOOST) <= {t for t, _ in pn.MACRO_QUERIES}
    assert set(pn.MACRO_BOOST.values()) <= set(pn.SECTOR_QUERIES)


def test_score_orderings():
    s = pn.score_item
    assert s("holding", 0.40, 5) > s("holding", 0.05, 5)
    assert s("macro", 0.0, 2) > s("holding", 0.05, 72)
    assert s("sector", 0.30, 5) > s("macro", 0.0, 5)
    assert s("holding", 0.2, None) < s("holding", 0.2, 500)
    assert s("macro", 0.0, None) < s("macro", 0.0, 500)


def test_reason_for():
    assert pn.reason_for("holding", "NVDA", 0.4) == "NVDA"
    assert pn.reason_for("sector", "Technology", 0.404) == "Tech 40%"
    assert pn.reason_for("sector", "Weird Sector", 0.2) == "Weird Sector 20%"
    assert pn.reason_for("macro", "oil", 0.0) == "Macro"


def test_unvalued_valued_at_mean_and_not_largest():
    book = pn.build_book([_pos("A", 1, 100.0), _pos("B", 1, 50.0), _pos("C", 1)],
                         {}, {})
    w = {h["symbol"]: h["weight"] for h in book}
    assert w == {"A": 0.44, "B": 0.22, "C": 0.33}
    assert book[0]["symbol"] == "A"


def test_zero_qty_dropped_not_unvalued():
    book = pn.build_book([_pos("A", 1, 10.0), _pos("Z", 0, 10.0)], {}, {})
    assert [h["symbol"] for h in book] == ["A"]


def test_nan_price_falls_back_and_negative_qty_by_magnitude():
    book = pn.build_book([_pos("A", 1, 10.0), _pos("S", -3, 10.0),
                          _pos("N", float("nan"), 10.0)],
                         {"A": float("nan"), "S": -5.0}, {})
    w = {h["symbol"]: h["weight"] for h in book}
    assert w == {"S": 0.75, "A": 0.25}
    assert all(0 <= x <= 1 for x in w.values()) and abs(sum(w.values()) - 1) < 0.02


def _rates(table):
    """A ``to_pref`` stand-in: symbol -> rate, ``None`` while a rate is loading."""
    def to_pref(sym, amount, day):
        r = table.get(sym)
        return None if r is None else amount * r
    return to_pref


def test_book_weights_use_converted_values():
    # 100 BRL held at R$1 each is worth $20, not $100
    book = pn.build_book([_pos("PETR4.SA", 100, 1.0), _pos("AAA", 1, 80.0)], {}, {},
                         to_pref=_rates({"PETR4.SA": 0.2, "AAA": 1.0}))
    assert {h["symbol"]: h["weight"] for h in book} == {"AAA": 0.8, "PETR4.SA": 0.2}


def test_position_without_rate_is_left_out():
    book = pn.build_book([_pos("PETR4.SA", 100, 1.0), _pos("AAA", 1, 80.0)], {}, {},
                         to_pref=_rates({"AAA": 1.0}))
    assert [h["symbol"] for h in book] == ["AAA"] and book[0]["weight"] == 1.0
    assert pn.build_book([_pos("PETR4.SA", 1, 1.0)], {}, {}, to_pref=_rates({})) == []


def test_to_pref_none_keeps_raw_behaviour_and_unpriced_still_mean_filled():
    pos = [_pos("A", 1, 60.0), _pos("B", 1, 20.0), _pos("C", 1)]
    assert pn.build_book(pos, {}, {}) == pn.build_book(pos, {}, {},
                                                       to_pref=_rates({"A": 1, "B": 1, "C": 1}))
