"""Pure geometry for per-panel divider drags (aurantium.tiling)."""

from aurantium.tiling import (Leaf, Rect, Split, build_tree, movable_segment,
                              move_segment, shape, snap_delta)

# Pedro layout (column-major, gap 4, window 1280x736).
PEDRO = {
    "topic": Rect(0, 0, 314, 368), "news": Rect(0, 372, 314, 364),
    "chart": Rect(318, 0, 617, 466),
    "analyst": Rect(318, 470, 306, 266), "company": Rect(628, 470, 307, 266),
    "watch": Rect(939, 0, 341, 368), "comm": Rect(939, 372, 341, 364),
}
# Aligned 2x2 (800x600, gap 4).
GRID = {
    "a": Rect(0, 0, 398, 298), "b": Rect(402, 0, 398, 298),
    "c": Rect(0, 302, 398, 298), "d": Rect(402, 302, 398, 298),
}
# Same, but the right column's line sits 2px lower: still a crossing.
GRID_OFF2 = dict(GRID, b=Rect(402, 0, 398, 300), d=Rect(402, 304, 398, 296))
MINS = {k: (80, 60) for k in PEDRO}
# Pinwheel (gap 0): no full-length cut in either direction.
PINWHEEL = {
    "A": Rect(0, 0, 200, 100), "B": Rect(200, 0, 100, 200),
    "C": Rect(100, 200, 200, 100), "D": Rect(0, 100, 100, 200),
    "E": Rect(100, 100, 100, 100),
}

seg_news_right = movable_segment(PEDRO, "v", 314, along=600, gap=4)
seg_news_top = movable_segment(PEDRO, "h", 368, along=100, gap=4)


def changed(old, new):
    return {k for k in old if old[k] != new[k]}


def test_news_right_edge_chains_topic_chart_analyst_not_company():
    seg = movable_segment(PEDRO, "v", 314, along=600, gap=4)
    assert seg.before == {"topic", "news"} and seg.after == {"chart", "analyst"}
    new, d = move_segment(PEDRO, seg, 40, 4, MINS)
    assert d == 40 and new["company"] == PEDRO["company"]
    assert new["news"].w == 354 and new["topic"].w == 354
    assert new["chart"].x == 358 and new["analyst"].w == 266


def test_commodities_top_moves_only_watchlist():
    seg = movable_segment(PEDRO, "h", 368, along=1100, gap=4)
    new, _ = move_segment(PEDRO, seg, -50, 4, MINS)
    assert changed(PEDRO, new) == {"watch", "comm"}


def test_chart_bottom_moves_analyst_and_company_together():
    seg = movable_segment(PEDRO, "h", 466, along=400, gap=4)
    assert seg.before == {"chart"} and seg.after == {"analyst", "company"}


def test_grid_2x2_segment_stops_at_crossing():   # case 1: step allowed
    seg = movable_segment(GRID, "h", 298, along=100, gap=4)
    assert seg.before == {"a"} and seg.after == {"c"}


def test_shift_whole_line_crosses_crossing():
    seg = movable_segment(GRID, "h", 298, along=100, gap=4, whole_line=True)
    assert seg.before == {"a", "b"} and seg.after == {"c", "d"}


def test_news_height_independent_of_analyst():    # case 5
    seg = movable_segment(PEDRO, "h", 368, along=100, gap=4)
    assert seg.before == {"topic"} and seg.after == {"news"}


def test_alignment_within_tolerance_is_a_crossing():
    seg = movable_segment(GRID_OFF2, "h", 298, along=100, gap=4)
    assert seg.before == {"a"} and seg.after == {"c"}


def test_clamped_to_minimum():
    new, d = move_segment(PEDRO, seg_news_right, 10_000, 4, MINS)
    assert new["analyst"].w == MINS["analyst"][0] and d < 10_000


def test_no_neighbour_returns_none():
    assert movable_segment({"x": Rect(0, 0, 800, 600)}, "v", 800, 300, 4) is None


def test_snap_pulls_to_nearby_parallel_edge():
    # news top at 368, analyst top at 466: dragging news top to 462 snaps to 466
    assert snap_delta(PEDRO, seg_news_top, 94, 4) == 98


def test_snap_ignores_far_edges():
    assert snap_delta(PEDRO, seg_news_top, 60, 4) == 60


def test_pedro_tree_matches_saved_structure():
    t = build_tree(PEDRO, 4)
    assert shape(t) == ("h", (("v", ("topic", "news")),
                              ("v", ("chart", ("h", ("analyst", "company")))),
                              ("v", ("watch", "comm"))))
    assert t.sizes == (314, 617, 341)


def test_stepped_grid_builds_columns():
    stepped = {**GRID, "a": Rect(0, 0, 398, 340), "c": Rect(0, 344, 398, 256)}
    assert shape(build_tree(stepped, 4))[0] == "h"


def test_aligned_grid_respects_prefer():
    rows_first = Split("v", (Split("h", (Leaf("a"), Leaf("b")), (398, 398)),
                             Split("h", (Leaf("c"), Leaf("d")), (398, 398))),
                       (298, 298))
    assert shape(build_tree(GRID, 4, prefer=rows_first))[0] == "v"
    assert shape(build_tree(GRID, 4))[0] == "h"   # default


def test_pinwheel_returns_none():
    assert build_tree(PINWHEEL, 0) is None


def test_area_already_below_minimum_does_not_jump():
    # news is already 40px narrower than its minimum; a zero move stays put and
    # it can grow but not shrink further
    seg = movable_segment(PEDRO, "v", 314, along=600, gap=4)
    mins = dict(MINS, news=(354, 60))
    assert move_segment(PEDRO, seg, 0, 4, mins)[1] == 0
    assert move_segment(PEDRO, seg, -10, 4, mins)[1] == 0
    assert move_segment(PEDRO, seg, 10, 4, mins)[1] == 10
