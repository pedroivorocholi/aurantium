"""The join must degrade per-source. One dead upstream may never blank the
panel, and the BIS citation must survive into the payload — it's a licence
condition."""

import pytest

from aurantium.providers.rates import (
    Curve,
    CurvePoint,
    PolicyPoint,
    build_curve_payload,
    build_policy_payload,
)

US = PolicyPoint("US", 4.25, "2026-07-01", 4.50, "2026-06-01", "down")
BR = PolicyPoint("BR", 10.75, "2026-07-01", None, None, "flat")


def test_bis_only_country_renders_without_enrichment():
    payload = build_policy_payload({"BR": BR}, {}, [])
    row = payload["countries"][0]
    assert row["code"] == "BR"
    assert row["policy"] == 10.75
    assert row["short"] is None and row["long"] is None
    assert row["slope"] is None


def test_enrichment_fills_short_and_long():
    payload = build_policy_payload(
        {"US": US}, {"US": {"short": 3.90, "long": 4.21}}, []
    )
    row = payload["countries"][0]
    assert (row["short"], row["long"]) == (3.90, 4.21)
    assert row["slope"] == pytest.approx(31.0)


def test_slope_is_none_when_short_leg_is_a_policy_rate():
    """A policy rate is not an observed 2y. Refuse to compute a slope from it."""
    payload = build_policy_payload({"US": US}, {"US": {"long": 4.21}}, [])
    assert payload["countries"][0]["slope"] is None


def test_bis_down_still_publishes_enrichment():
    payload = build_policy_payload({}, {"US": {"short": 3.9, "long": 4.21}}, ["BIS"])
    assert payload["countries"], "payload must not be empty when a source survives"
    assert payload["partial"] == ["BIS"]


def test_bis_citation_present_whenever_bis_data_is():
    payload = build_policy_payload({"US": US}, {}, [])
    assert "BIS" in payload["sources"]


def test_countries_are_sorted_by_label():
    payload = build_policy_payload({"US": US, "BR": BR}, {}, [])
    labels = [r["label"] for r in payload["countries"]]
    assert labels == sorted(labels)


def test_curve_payload_marks_sparse_curves_incomplete():
    sparse = Curve("GB", (CurvePoint(0.25, 4.0), CurvePoint(10.0, 4.6)),
                   False, ("FRED",), "2026-07-01")
    payload = build_curve_payload(sparse)
    assert payload["complete"] is False
    assert len(payload["points"]) == 2
    assert payload["observed"] == 2


def test_curve_payload_never_adds_points():
    sparse = Curve("GB", (CurvePoint(0.25, 4.0), CurvePoint(10.0, 4.6)),
                   False, ("FRED",), "2026-07-01")
    assert build_curve_payload(sparse)["points"] == [[0.25, 4.0], [10.0, 4.6]]
