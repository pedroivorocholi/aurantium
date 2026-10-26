"""Day Brief wiring: topic routing and policy registration, plus the panel's
own render/restore behaviour under an offscreen QApplication."""

import pytest

from aurantium.datahub import DataHub
from aurantium.providers.dayinfo import DayInfoProvider


# -- provider contract ----------------------------------------------------


def test_provider_owns_both_day_topics():
    assert set(DayInfoProvider().topic_patterns()) == {"daystat:*", "dayev:*"}


def test_news_provider_owns_the_ranged_topic():
    from aurantium.providers.news import NewsProvider

    assert "newsr:*" in NewsProvider().topic_patterns()


def test_every_day_topic_pattern_gets_a_policy():
    """A pattern with no policy silently falls back to the 30s/5s default,
    which would re-fetch an immutable past day over and over."""
    import inspect

    from aurantium.providers import register_all_providers

    src = inspect.getsource(register_all_providers)
    for pattern in ("daystat:*", "dayev:*", "newsr:*"):
        assert f'set_policy("{pattern}"' in src, pattern


def test_day_topic_patterns_are_single_segment():
    """Single-segment 'prefix:*' patterns hit DataHub's O(1) lookup; anything
    globbier falls back to a linear fnmatch scan."""
    for pattern in ("daystat:*", "dayev:*", "newsr:*"):
        assert pattern.count(":") == 1 and pattern.endswith(":*")


@pytest.mark.parametrize(
    "topic",
    [
        "daystat:AAPL",  # missing the date
        "daystat:AAPL:2026-03-14:extra",
        "dayev:AAPL:2026-03-14",  # a range was required
        "dayev:AAPL",
    ],
)
def test_malformed_topics_are_reported_not_swallowed(topic, monkeypatch):
    errors = []
    hub = DataHub.instance()
    monkeypatch.setattr(hub, "publish_error", lambda t, m: errors.append((t, m)))
    monkeypatch.setattr(hub, "run_async", lambda fn: pytest.fail("should not fetch"))
    DayInfoProvider().refresh([topic])
    assert errors and errors[0][0] == topic


def test_well_formed_topics_are_dispatched(monkeypatch):
    scheduled = []
    hub = DataHub.instance()
    monkeypatch.setattr(hub, "run_async", lambda fn: scheduled.append(fn))
    monkeypatch.setattr(
        hub, "publish_error", lambda t, m: pytest.fail(f"unexpected error: {m}")
    )
    DayInfoProvider().refresh(
        ["daystat:AAPL:2026-03-14", "dayev:AAPL:2026-03-13..2026-03-15"]
    )
    assert len(scheduled) == 2


# -- the panel ------------------------------------------------------------


@pytest.fixture
def panel(qapp):
    from aurantium.panels.day_brief import DayBriefPanel

    p = DayBriefPanel()
    p.build()
    yield p


DAYSTAT = {
    "symbol": "AAPL",
    "date": "2026-03-13",
    "snapped_from": "2026-03-14",
    "o": 178.20, "h": 179.05, "l": 162.10, "c": 163.20, "v": 142_300_000,
    "pct": -8.42,
    "avg_vol_30": 37_400_000,
    "vol_ratio": 3.8,
    "bench_sym": "^GSPC", "bench_pct": -0.31,
    "sector_sym": "XLK", "sector_pct": -0.55,
    "excess_pct": -8.11,
    "verdict": "stock-specific, not the market",
}


def _stat(panel, key):
    _eyebrow, value, rest = panel._stat_labels[key]
    return value.text(), rest.text()


def test_stat_block_renders_the_session(panel):
    panel._on_daystat(DAYSTAT)
    assert _stat(panel, "MOVE")[0] == "-8.42%"
    assert "O 178.20" in _stat(panel, "MOVE")[1]
    assert _stat(panel, "VOL")[0] == "142.3M"
    assert "3.8" in _stat(panel, "VOL")[1]
    assert _stat(panel, "EXCESS")[0] == "-8.11%"
    assert _stat(panel, "EXCESS")[1] == "stock-specific, not the market"


def test_a_snapped_date_is_announced(panel):
    """The user must never be quietly shown a different day than they asked
    for."""
    panel._on_daystat(DAYSTAT)
    status = panel._status_lbl.text()
    assert "2026-03-14" in status and "2026-03-13" in status


def test_an_exact_session_says_nothing(panel):
    panel._on_daystat(dict(DAYSTAT, snapped_from=""))
    assert panel._status_lbl.text() == ""


def test_missing_benchmark_says_so_rather_than_guessing(panel):
    panel._on_daystat(
        dict(DAYSTAT, bench_sym=None, bench_pct=None, sector_sym=None, excess_pct=None)
    )
    assert "no benchmark" in _stat(panel, "VS")[1]
    assert _stat(panel, "EXCESS")[0] == "—"


def test_empty_payload_clears_instead_of_stale_numbers(panel):
    panel._on_daystat(DAYSTAT)
    panel._on_daystat({"empty": True, "reason": "no trading data"})
    assert _stat(panel, "MOVE")[0] == "—"
    assert panel._status_lbl.text() == "no trading data"


def test_events_render_with_their_glyphs(panel):
    panel._on_events(
        {
            "events": [
                {"date": "2026-03-13", "kind": "earnings", "title": "Earnings",
                 "detail": "EPS 1.42 vs 1.58e"},
                {"date": "2026-03-13", "kind": "rating", "title": "Morgan Stanley",
                 "detail": "Overweight → Equal-Weight", "action": "down"},
            ]
        }
    )
    assert panel.events_table.rowCount() == 2
    assert panel.events_table.item(0, 0).text() == "E"
    assert panel.events_table.item(1, 0).text() == "▼"


def test_no_events_still_says_something(panel):
    panel._on_events({"events": []})
    assert panel.events_table.rowCount() == 1
    assert "no corporate events" in panel.events_table.item(0, 1).text()


def test_news_header_counts_and_pluralises(panel):
    panel._apply_date("2026-03-13", "1D")
    panel._on_news(
        {
            "items": [
                {"title": "Apple cuts China guidance", "publisher": "Reuters",
                 "url": "http://example.invalid/a",
                 "published": "Fri, 13 Mar 2026 09:31:00 GMT"}
            ],
            "hidden": 0,
        }
    )
    assert "1 headline" in panel.news_head.text()
    assert "1 headlines" not in panel.news_head.text()
    assert panel.news_table.rowCount() == 1


def test_language_gate_emptying_the_window_is_named(panel):
    """The gate normally fails open; on a narrow historical window it can
    legitimately return nothing, and a bare empty state would baffle."""
    panel._apply_date("2019-01-03", "1D")
    panel._on_news({"items": [], "hidden": 12})
    assert "12 headlines hidden" in panel._empty.title


def test_no_news_offers_a_wider_window(panel):
    panel._apply_date("2019-01-03", "1D")
    panel._on_news({"items": [], "hidden": 0})
    assert "wider window" in panel._empty.hint


# -- date/window model ----------------------------------------------------


@pytest.mark.parametrize(
    "token,expected",
    [
        ("1D", "14 Mar · 1 day"),
        ("3D", "13 – 15 Mar · 3 days"),
        ("1W", "11 – 17 Mar · 7 days"),
    ],
)
def test_the_resolved_range_agrees_with_the_chip(panel, token, expected):
    """The bug this guards: the sub-line used to say "15 days" under a chip
    labelled 1W, because the token was a radius."""
    panel._apply_date("2026-03-14", token)
    assert panel.range_lbl.text() == expected


def test_stepping_skips_the_weekend(panel):
    panel._apply_date("2026-03-13", "1D")  # a Friday
    panel._step(1)
    assert panel._anchor == "2026-03-16"  # Monday, not Saturday
    panel._step(-1)
    assert panel._anchor == "2026-03-13"


def test_stepping_never_goes_past_today(panel):
    from datetime import date

    panel._apply_date(date.today().isoformat(), "1D")
    panel._step(1)
    assert panel._anchor <= date.today().isoformat()


def test_settings_round_trip(panel):
    panel._apply_date("2026-03-14", "1W")
    assert panel.settings() == {"anchor": "2026-03-14", "window": "1W"}


@pytest.mark.parametrize(
    "junk",
    [None, "a string", [], 42, {"anchor": 123}, {"anchor": "nope"},
     {"window": "ZZZ"}, {"anchor": "2026-03-14", "window": 7}],
)
def test_restore_never_raises(panel, junk):
    panel.restore(junk)


def test_restore_keeps_a_valid_pair(panel):
    panel.restore({"anchor": "2026-03-14", "window": "1M"})
    assert panel.settings() == {"anchor": "2026-03-14", "window": "1M"}


def test_no_symbol_says_so(panel):
    panel._refresh()
    # The shared constant, not a phrasing. This panel used to say "No symbol
    # linked" while six other panels said "No symbol selected" for the identical
    # condition; asserting the constant is what stops them diverging again.
    from aurantium.panel import EMPTY_NO_SYMBOL

    assert panel._empty.title == EMPTY_NO_SYMBOL
