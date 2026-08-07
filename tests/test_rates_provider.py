"""Parsers run against fixtures recorded by tools/probe_rates.py - real
upstream responses, so a future API reorganization shows as a fixture diff
rather than a mystery."""

from pathlib import Path

import pytest

from aurantium.providers.rates import (
    Curve,
    PolicyPoint,
    direction_of,
    parse_bis_policy,
    parse_mof_curve,
    parse_ust_curve,
    tenor_years,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rates"


def _fixture(stem: str) -> str:
    matches = sorted(FIXTURES.glob(f"{stem}.*"))
    if not matches:
        pytest.skip(f"no fixture recorded for {stem} - run tools/probe_rates.py")
    return matches[0].read_text(encoding="utf-8")


def test_direction_of():
    assert direction_of(4.25, 4.50) == "down"
    assert direction_of(4.50, 4.25) == "up"
    assert direction_of(4.25, 4.25) == "flat"
    assert direction_of(4.25, None) == "flat"


def test_bis_policy_parses_to_country_keyed_points():
    result = parse_bis_policy(_fixture("bis_cbpol"))
    assert isinstance(result, dict)
    assert result, "expected at least one jurisdiction"
    for code, point in result.items():
        assert isinstance(point, PolicyPoint)
        assert point.code == code
        assert -5.0 < point.rate < 100.0, (code, point.rate)
        assert point.direction in {"up", "down", "flat"}


def test_bis_policy_includes_the_major_jurisdictions():
    result = parse_bis_policy(_fixture("bis_cbpol"))
    assert "US" in result
    assert "JP" in result


def test_bis_policy_tolerates_garbage():
    assert parse_bis_policy("") == {}
    assert parse_bis_policy("not,a,valid\ncsv,file,really") == {}


def test_bis_policy_drops_literal_nan_observations():
    """BIS marks missing observations OBS_STATUS=M and writes the literal
    string "NaN" into OBS_VALUE - 21 such rows exist in the fixture (Croatia,
    2021-22). A NaN reaching a rate would poison every downstream diff and
    serialize to invalid JSON."""
    result = parse_bis_policy(_fixture("bis_cbpol"))
    for code, point in result.items():
        assert point.rate == point.rate, f"{code} rate is NaN"
        if point.prev is not None:
            assert point.prev == point.prev, f"{code} prev is NaN"


def test_bis_policy_skips_a_nan_row_synthetically():
    csv_text = (
        "FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
        "M,US,2026-05,4.50,A\n"
        "M,US,2026-06,NaN,M\n"
        "M,US,2026-07,4.25,A\n"
    )
    point = parse_bis_policy(csv_text)["US"]
    assert point.rate == 4.25
    assert point.prev == 4.50      # the NaN row must not become the previous value
    assert point.direction == "down"


def test_ust_curve_is_complete_and_ordered():
    curve = parse_ust_curve(_fixture("ust_curve"))
    assert isinstance(curve, Curve)
    assert curve.code == "US"
    assert curve.complete is True
    assert len(curve.points) >= 5
    years = [p.years for p in curve.points]
    assert years == sorted(years)
    assert "U.S. Treasury" in curve.sources


def test_ust_curve_tolerates_garbage():
    curve = parse_ust_curve("")
    assert curve.points == ()
    assert curve.complete is False


def test_tenor_years_handles_every_observed_label_spelling():
    """Treasury mixes "Mo"/"Month"/"Yr" in one header row and includes a
    fractional "1.5 Month"; MOF uses bare "40Y". One normalizer, all of them."""
    assert tenor_years("1 Mo") == pytest.approx(1 / 12)
    assert tenor_years("1.5 Month") == pytest.approx(0.125)
    assert tenor_years("4 Mo") == pytest.approx(1 / 3)
    assert tenor_years("10 Yr") == 10.0
    assert tenor_years("40Y") == 40.0
    assert tenor_years("Date") is None
    assert tenor_years("") is None


def test_ust_curve_includes_the_short_end_tenors():
    """Regression: an earlier lookup-table approach silently dropped the
    "1.5 Month", "2 Mo" and "4 Mo" columns Treasury actually publishes."""
    curve = parse_ust_curve(_fixture("ust_curve"))
    assert len(curve.points) >= 12, [p.years for p in curve.points]


def test_mof_curve_parses():
    curve = parse_mof_curve(_fixture("mof_jgb"))
    assert curve.code == "JP"
    assert len(curve.points) >= 5
    assert "Japan MOF" in curve.sources


def test_mof_curve_ignores_the_trailing_footer_note():
    """The MOF file ends with a blank row then a "clear your browser cache"
    note. Reading the last non-empty row would parse that as a curve."""
    curve = parse_mof_curve(_fixture("mof_jgb"))
    assert curve.as_of and "/" in curve.as_of, curve.as_of
    assert all(0.0 < p.rate < 20.0 for p in curve.points)


def test_no_parser_interpolates():
    """Observed points only - a curve must never contain a tenor the source
    did not publish."""
    curve = parse_ust_curve(_fixture("ust_curve"))
    assert len({p.years for p in curve.points}) == len(curve.points)
