"""Integrity tests for the curated commodity metadata."""

import re
from datetime import date

from aurantium.commodities_meta import (
    CATEGORIES,
    COMMODITIES,
    MONTH_CODES,
    by_cftc_market,
    by_symbol,
    contract_symbols,
)

CONTRACT_RE = re.compile(r"^[A-Z]+[FGHJKMNQUVXZ]\d{2}\.(CMX|NYM|CBT|NYB|CME)$")


def test_contract_symbols_spell_correctly():
    for meta in COMMODITIES:
        for sym, label in contract_symbols(meta, 4, today=date(2026, 7, 22)):
            assert CONTRACT_RE.match(sym), f"{meta.label}: bad contract {sym}"
            assert label  # "Mon YY"


def test_unique_roots_symbols_and_markets():
    roots = [m.root for m in COMMODITIES]
    symbols = [m.symbol for m in COMMODITIES]
    markets = [m.cftc_market for m in COMMODITIES if m.cftc_market]
    assert len(roots) == len(set(roots))
    assert len(symbols) == len(set(symbols))
    assert len(markets) == len(set(markets))


def test_categories_and_months_valid():
    for meta in COMMODITIES:
        assert meta.category in CATEGORIES, meta.label
        assert meta.months and all(c in MONTH_CODES for c in meta.months), meta.label


def test_livestock_present_and_resolvable():
    le = by_symbol("LE=F")
    assert le is not None and le.category == "Livestock"
    assert by_cftc_market("live_cattle") is le
    # a spelled contract resolves back to the commodity
    sym, _ = contract_symbols(le, 1, today=date(2026, 7, 22))[0]
    assert by_symbol(sym) is le


def test_provider_map_covers_every_cftc_market():
    from aurantium.providers.econ import _CFTC_MARKETS

    for meta in COMMODITIES:
        if meta.cftc_market:
            assert meta.cftc_market in _CFTC_MARKETS, meta.label
