"""Tests for the pure bounds math behind chart view clamping."""

from aurantium.components.plot_utils import view_limits


def test_normal_span_padded():
    limits = view_limits([0.0, 10.0], [1.0, 3.0], pad=0.1)
    assert limits["xMin"] == -1.0 and limits["xMax"] == 11.0
    assert limits["yMin"] == 0.8 and abs(limits["yMax"] - 3.2) < 1e-9


def test_single_point_gets_unit_pad():
    limits = view_limits([5.0], [2.0])
    assert limits["xMin"] < 5.0 < limits["xMax"]
    assert limits["yMin"] < 2.0 < limits["yMax"]


def test_empty_returns_none():
    assert view_limits([], []) is None
