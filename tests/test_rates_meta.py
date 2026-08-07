"""The curated country table must stay internally consistent — the same
discipline test_commodities_meta.py applies to the commodity table."""

from aurantium.rates_meta import (
    COUNTRIES,
    CURVE_SOURCES,
    by_code,
    curve_capable,
)


def test_codes_are_unique():
    codes = [c.code for c in COUNTRIES]
    assert len(codes) == len(set(codes))


def test_codes_are_two_letter_upper():
    for c in COUNTRIES:
        assert len(c.code) == 2 and c.code.isupper(), c.code


def test_curve_source_is_a_known_router_key():
    for c in COUNTRIES:
        assert c.curve_source in CURVE_SOURCES, (c.code, c.curve_source)


def test_every_country_declares_a_citation():
    for c in COUNTRIES:
        assert c.citation.strip(), c.code


def test_curve_countries_declare_tenors():
    for c in COUNTRIES:
        if c.curve_source == "none":
            assert c.tenors == ()
        else:
            assert len(c.tenors) >= 2, c.code
            assert list(c.tenors) == sorted(c.tenors), c.code


def test_fred_sourced_countries_name_at_least_one_series():
    for c in COUNTRIES:
        if c.curve_source == "fred":
            assert c.fred_short or c.fred_long, c.code


def test_tier_is_free_or_pro():
    assert {c.tier for c in COUNTRIES} <= {"free", "pro"}


def test_by_code_is_case_insensitive_and_safe():
    assert by_code("us") is by_code("US")
    assert by_code("ZZ") is None
    assert by_code("") is None


def test_curve_capable_excludes_none_sources():
    assert all(c.curve_source != "none" for c in curve_capable())
    assert any(c.code == "US" for c in curve_capable())
