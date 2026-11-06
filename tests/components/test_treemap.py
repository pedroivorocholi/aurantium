"""Squarified treemap layout: proportional, gap-free, well-shaped tiles."""

from aurantium.components.treemap import layout

# 40 plausible US large-cap market caps in dollars, descending: a few above
# $3T falling to about $150B. Fixed here so the test never touches the network.
CAPS = (
    4.6e12, 3.9e12, 3.6e12, 2.4e12, 2.1e12, 1.9e12, 1.5e12, 1.4e12, 1.1e12,
    9.8e11, 8.5e11, 7.9e11, 7.2e11, 6.6e11, 6.1e11, 5.7e11, 5.2e11, 4.9e11,
    4.6e11, 4.3e11, 4.0e11, 3.8e11, 3.5e11, 3.3e11, 3.1e11, 2.9e11, 2.8e11,
    2.6e11, 2.5e11, 2.4e11, 2.3e11, 2.2e11, 2.1e11, 2.0e11, 1.9e11, 1.8e11,
    1.7e11, 1.6e11, 1.55e11, 1.5e11,
)


def test_areas_are_proportional():
    rects = layout([6, 3, 1], 100, 100)
    assert [round(r[2] * r[3]) for r in rects] == [6000, 3000, 1000]


def test_fills_the_rect_without_overlap():
    w, h, eps = 800, 500, 1e-6
    rects = layout(CAPS, w, h)
    assert abs(sum(r[2] * r[3] for r in rects) - w * h) < 1e-3
    for i, a in enumerate(rects):
        assert a[0] >= -eps and a[1] >= -eps
        assert a[0] + a[2] <= w + eps and a[1] + a[3] <= h + eps
        for b in rects[i + 1:]:
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            assert ox <= eps or oy <= eps


def test_zero_weight_gets_empty_rect():
    rects = layout([5, 3, 0, -2], 100, 50)
    assert len(rects) == 4
    assert rects[2][2:] == (0, 0) and rects[3][2:] == (0, 0)
    assert abs(rects[0][2] * rects[0][3] + rects[1][2] * rects[1][3] - 5000) < 1e-6


def test_degenerate_inputs():
    assert layout([], 100, 100) == []
    assert layout([1, 2], 0, 100) == [(0, 0, 0, 0)] * 2
    assert layout([1, 2], 100, 0) == [(0, 0, 0, 0)] * 2
    assert layout([0, 0], 100, 100) == [(0, 0, 0, 0)] * 2


def test_aspect_ratios_stay_reasonable():
    worst = max(
        max(r[2] / r[3], r[3] / r[2]) for r in layout(CAPS, 800, 500) if r[2] and r[3]
    )
    assert worst < 6
