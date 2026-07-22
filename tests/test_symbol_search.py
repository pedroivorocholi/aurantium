"""Pure-logic tests for components.symbol_search: local ranking, merge/dedupe,
Yahoo result mapping, and the engine's cache/staleness behavior (with the
remote injected as a stub — no network)."""

from aurantium.components.symbol_search import (
    MAX_ROWS,
    SuggestionEngine,
    SymbolSuggestion,
    local_suggestions,
    merge_suggestions,
    quotes_to_suggestions,
)


def _s(code, label="", category="Equity"):
    return SymbolSuggestion(code, label, category)


# -- local source ----------------------------------------------------------


def test_local_finds_commodity_by_name():
    codes = [s.code for s in local_suggestions("gold")]
    assert "GC=F" in codes


def test_local_finds_treasury_by_plain_english():
    subs = local_suggestions("10 year")
    assert subs and subs[0].code == "^TNX"


def test_local_excludes_fred_series():
    # FRED ids feed fred: topics, not quote: — they must never be suggested
    for query in ("cpi", "unemployment", "fed funds"):
        assert all(s.category != "FRED" for s in local_suggestions(query))


def test_local_watchlist_prefix_match():
    subs = local_suggestions("aa", ["AAPL", "MSFT"])
    assert any(s.code == "AAPL" and s.category == "Watchlist" for s in subs)
    assert all(s.code != "MSFT" for s in subs)


def test_local_empty_query_is_empty():
    assert local_suggestions("", ["AAPL"]) == []


# -- merge -----------------------------------------------------------------


def test_merge_local_first_and_dedupes_case_insensitively():
    merged = merge_suggestions(
        [_s("GC=F", "Gold", "Metals")],
        [_s("gc=f", "Gold Futures", "Future"), _s("GLD", "SPDR Gold", "ETF")],
    )
    assert [(s.code, s.category) for s in merged] == [
        ("GC=F", "Metals"),
        ("GLD", "ETF"),
    ]


def test_merge_caps_rows():
    local = [_s(f"L{i}") for i in range(10)]
    remote = [_s(f"R{i}") for i in range(10)]
    assert len(merge_suggestions(local, remote)) == MAX_ROWS


# -- Yahoo result mapping --------------------------------------------------


def test_quotes_mapping_drops_bad_rows():
    subs = quotes_to_suggestions(
        [
            {"symbol": "NVDA", "shortname": "NVIDIA Corporation", "quoteType": "EQUITY"},
            {"quoteType": "EQUITY"},  # no symbol
            {"symbol": "NVDA26C100", "quoteType": "OPTION"},  # options excluded
            {"symbol": "GLD", "longname": "SPDR Gold Shares", "quoteType": "ETF"},
        ]
    )
    assert [(s.code, s.label, s.category) for s in subs] == [
        ("NVDA", "NVIDIA Corporation", "Equity"),
        ("GLD", "SPDR Gold Shares", "ETF"),
    ]


# -- engine ----------------------------------------------------------------


def _drain(app):
    app.processEvents()


def test_engine_emits_local_then_merged_remote(qapp):
    calls = []
    engine = SuggestionEngine(remote=lambda q: calls.append(q) or [_s("NVDA", "NVIDIA")])
    got = []
    engine.suggestions_ready.connect(lambda q, subs: got.append((q, list(subs))))

    engine.request("nvda")
    assert got and got[0][0] == "nvda"  # instant local emission

    engine._fire_remote()  # skip the debounce timer in tests
    # the worker runs on the global pool; wait for its queued signal
    from PySide6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(5000)
    _drain(qapp)
    assert calls == ["nvda"]
    assert any(s.code == "NVDA" for s in got[-1][1])


def test_engine_drops_stale_remote(qapp):
    engine = SuggestionEngine(remote=lambda q: [_s("OLD")])
    got = []
    engine.suggestions_ready.connect(lambda q, subs: got.append(q))

    engine.request("aa")
    engine._query = "aab"  # user kept typing before the remote landed
    engine._on_remote("aa", [_s("OLD")])
    assert "aa" not in got[1:]  # no second emission for the stale query


def test_engine_serves_cache_without_second_remote_call(qapp):
    calls = []

    def remote(q):
        calls.append(q)
        return [_s("NVDA", "NVIDIA")]

    engine = SuggestionEngine(remote=remote)
    got = []
    engine.suggestions_ready.connect(lambda q, subs: got.append((q, list(subs))))

    engine.request("nvda")
    engine._fire_remote()
    from PySide6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(5000)
    _drain(qapp)
    assert calls == ["nvda"]

    engine.request("nvda")  # cache hit: merged emission, no new remote task
    assert not engine._timer.isActive()
    assert calls == ["nvda"]
    assert any(s.code == "NVDA" for s in got[-1][1])
