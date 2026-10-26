"""Tests for the curated symbol catalog behind the editor pickers."""

from aurantium.components.symbol_catalog import (
    FRED_ENTRIES,
    FX_ENTRIES,
    INDEX_ENTRIES,
    SECTOR_ETF_ENTRIES,
    TENOR_ENTRIES,
    CatalogEntry,
    commodity_entries,
    search_catalog,
)

ALL_SLICES = {
    "tenors": TENOR_ENTRIES,
    "indices": INDEX_ENTRIES,
    "fx": FX_ENTRIES,
    "fred": FRED_ENTRIES,
    "sector_etfs": SECTOR_ETF_ENTRIES,
    "commodities": commodity_entries(),
}


def test_plain_english_search_finds_tenor():
    hits = search_catalog(TENOR_ENTRIES, "10 year")
    assert hits, "expected a hit for '10 year'"
    assert hits[0].code == "^TNX"


def test_keyword_search_finds_commodity():
    hits = search_catalog(commodity_entries(), "cattle")
    assert any(h.label == "Live Cattle" for h in hits)


def test_code_prefix_ranks_first():
    hits = search_catalog(INDEX_ENTRIES, "^GS")
    assert hits and hits[0].code == "^GSPC"


def test_label_prefix_beats_substring():
    entries = [
        CatalogEntry("Broad Gold Miners", "RING", "quote", "Test"),
        CatalogEntry("Gold", "GC=F", "quote", "Test"),
    ]
    hits = search_catalog(entries, "gold")
    assert hits[0].code == "GC=F"


def test_empty_query_keeps_order_and_limit():
    hits = search_catalog(INDEX_ENTRIES, "", limit=3)
    assert hits == list(INDEX_ENTRIES[:3])


def test_no_duplicate_codes_within_each_slice():
    for name, entries in ALL_SLICES.items():
        codes = [e.code for e in entries]
        assert len(codes) == len(set(codes)), f"duplicate codes in {name}"


def test_entry_kinds_valid():
    for entries in ALL_SLICES.values():
        for e in entries:
            assert e.kind in ("quote", "fred"), e
    assert all(e.kind == "fred" for e in FRED_ENTRIES)
