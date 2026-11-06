"""rates:curvehist:<CC> - the multi-day curve behind the 1W / 1M / 1Y
comparison lines and the spread sparklines.

Parsers and the provider run against fixtures recorded from the live
upstreams (tests/fixtures/rates/*_hist_*); nothing here touches the network."""

import datetime
from pathlib import Path

import pytest

from aurantium.providers import rates
from aurantium.providers.rates import (
    Curve,
    CurvePoint,
    RatesProvider,
    parse_mof_history,
    parse_ust_history,
)
from aurantium.rates_meta import COUNTRIES

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rates"
TODAY = datetime.date(2026, 11, 5)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class _FakeHub:
    def __init__(self):
        self.published: dict = {}
        self.errors: dict = {}

    def publish(self, topic, value):
        self.published[topic] = value

    def publish_error(self, topic, error):
        self.errors[topic] = error

    def run_async(self, fn):
        fn()


@pytest.fixture
def hub(monkeypatch):
    fake = _FakeHub()
    monkeypatch.setattr(rates.DataHub, "instance", classmethod(lambda cls: fake))
    rates._HIST_CACHE.clear()
    yield fake
    rates._HIST_CACHE.clear()


def _stub(monkeypatch, routes: dict):
    """Route _get_text by URL substring; unmatched or Exception values fail."""
    calls: list[str] = []

    def fake(url, **kwargs):
        calls.append(url)
        for needle, body in routes.items():
            if needle in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise OSError(f"unexpected url {url}")

    monkeypatch.setattr(rates, "_get_text", fake)
    return calls


def _ust_routes(**override):
    routes = {
        "/2026/all": _read("ust_hist_2026.csv"),
        "/2025/all": _read("ust_hist_2025.csv"),
    }
    routes.update(override)
    return routes


def _run(code, today=TODAY):
    RatesProvider()._fetch_curvehist(f"rates:curvehist:{code}", code, today=today)


def _iso_dates(payload):
    return [d for d, _ in payload["days"]]


# -- parsers ---------------------------------------------------------------


def test_parse_ust_history_returns_every_row_oldest_first():
    days = parse_ust_history(_read("ust_hist_2026.csv"))
    dates = [d for d, _ in days]
    assert len(days) > 150
    assert dates == sorted(dates)
    assert dates[-1] == "2026-11-05"
    _, points = days[-1]
    assert [p[0] for p in points] == sorted(p[0] for p in points)
    assert [10.0, 4.84] in [list(p) for p in points]


def test_parse_ust_history_skips_blank_cells_and_garbage():
    # 1.5 Month is blank through early 2025 - it must be skipped, not zeroed
    days = parse_ust_history(_read("ust_hist_2025.csv"))
    assert all(abs(p[0] - 0.125) > 1e-9 for p in days[0][1])
    assert parse_ust_history("") == []
    assert parse_ust_history("not,a\ncsv") == []


def test_parse_mof_history_reads_dates_and_skips_dashes():
    days = parse_mof_history(_read("mof_hist_all.csv"))
    dates = [d for d, _ in days]
    assert dates == sorted(dates)
    assert dates[0] == "2025-09-01"
    assert dates[-1] == "2026-10-30"
    assert all(len(points) >= 5 for _, points in days)
    assert parse_mof_history("") == []


# -- US --------------------------------------------------------------------


def test_us_history_has_13_months(hub, monkeypatch):
    _stub(monkeypatch, _ust_routes())
    _run("US")
    payload = hub.published["rates:curvehist:US"]
    dates = _iso_dates(payload)
    assert set(payload) == {"code", "days", "sources", "as_of"}
    assert payload["code"] == "US"
    assert payload["sources"] == [rates.UST]
    assert dates == sorted(dates)
    assert payload["as_of"] == dates[-1] == "2026-11-05"
    first = datetime.date.fromisoformat(dates[0])
    assert (TODAY - first).days >= 13 * 30
    for _, points in payload["days"]:
        assert [p[0] for p in points] == sorted(p[0] for p in points)


def test_comparison_crosses_year_boundary(hub, monkeypatch):
    """On 2 January the current-year file has almost no rows; the 1W / 1M / 1Y
    comparisons must come from last year's file."""
    today = datetime.date(2027, 1, 2)
    new_year = (
        'Date,"1 Mo","3 Mo","1 Yr","2 Yr","5 Yr","10 Yr","30 Yr"\n'
        "01/02/2027,4.00,4.05,4.10,4.20,4.40,4.70,5.10\n"
    )
    _stub(monkeypatch, {"/2027/all": new_year, "/2026/all": _read("ust_hist_2026.csv")})
    _run("US", today)
    dates = _iso_dates(hub.published["rates:curvehist:US"])
    assert dates[-1] == "2027-01-02"
    for back in (7, 30, 365):
        target = (today - datetime.timedelta(days=back)).isoformat()
        assert any(d <= target for d in dates), f"no day on or before {target}"


def test_last_year_failing_publishes_what_we_have(hub, monkeypatch):
    _stub(monkeypatch, _ust_routes(**{"/2025/all": OSError("boom")}))
    _run("US")
    payload = hub.published["rates:curvehist:US"]
    assert payload["as_of"] == "2026-11-05"
    assert len(payload["days"]) > 100
    assert "rates:curvehist:US" not in hub.errors


def test_everything_failing_is_an_error(hub, monkeypatch):
    _stub(monkeypatch, {})
    _run("US")
    assert "rates:curvehist:US" in hub.errors
    assert "rates:curvehist:US" not in hub.published


def test_previous_year_file_is_not_refetched_the_same_day(hub, monkeypatch):
    calls = _stub(monkeypatch, _ust_routes())
    _run("US")
    _run("US")
    assert sum("/2025/all" in u for u in calls) == 1
    assert sum("/2026/all" in u for u in calls) == 2   # the current file stays live


# -- euro area ---------------------------------------------------------------


def test_ecb_history_joins_tenors_by_date(hub, monkeypatch):
    two = _read("ecb_hist_SR_2Y.csv")
    ten = _read("ecb_hist_SR_10Y.csv")
    calls = _stub(monkeypatch, {"SR_2Y": two, "SR_10Y": ten, "SR_": OSError("n/a")})
    _run("XM")
    payload = hub.published["rates:curvehist:XM"]
    dates = _iso_dates(payload)
    assert payload["sources"] == [rates.ECB]
    assert dates == sorted(dates)
    assert payload["as_of"] == dates[-1]
    assert all("startPeriod=" in u for u in calls)
    assert len(dates) > 250
    last_points = dict(map(tuple, payload["days"][-1][1]))
    assert set(last_points) == {2.0, 10.0}
    assert all(points for _, points in payload["days"])


def test_ecb_history_keeps_a_day_with_only_one_tenor(hub, monkeypatch):
    two = "KEY,TIME_PERIOD,OBS_VALUE\nk,2026-11-03,2.1\nk,2026-11-04,2.2\n"
    ten = "KEY,TIME_PERIOD,OBS_VALUE\nk,2026-11-04,3.0\n"
    _stub(monkeypatch, {"SR_2Y": two, "SR_10Y": ten, "SR_": OSError("n/a")})
    _run("XM")
    days = {
        d: dict(map(tuple, pts))
        for d, pts in hub.published["rates:curvehist:XM"]["days"]
    }
    assert days["2026-11-03"] == {2.0: 2.1}
    assert days["2026-11-04"] == {2.0: 2.2, 10.0: 3.0}


# -- Japan -------------------------------------------------------------------


def test_japan_history_merges_archive_and_current_month(hub, monkeypatch):
    _stub(monkeypatch, {
        "historical/jgbcme_all.csv": _read("mof_hist_all.csv"),
        "interest_rate/jgbcme.csv": _read("mof_hist_month.csv"),
    })
    _run("JP")
    payload = hub.published["rates:curvehist:JP"]
    dates = _iso_dates(payload)
    assert payload["sources"] == [rates.MOF]
    assert dates == sorted(dates) and len(dates) == len(set(dates))
    # the archive lags to month end; the current-month file supplies the tail
    assert dates[-1] == "2026-11-05" == payload["as_of"]
    assert "2026-10-30" in dates
    assert (TODAY - datetime.date.fromisoformat(dates[0])).days >= 13 * 30 - 60


def test_japan_survives_the_archive_being_unreachable(hub, monkeypatch):
    _stub(monkeypatch, {
        "historical/jgbcme_all.csv": OSError("404"),
        "interest_rate/jgbcme.csv": _read("mof_hist_month.csv"),
    })
    _run("JP")
    assert hub.published["rates:curvehist:JP"]["as_of"] == "2026-11-05"


# -- everything else ---------------------------------------------------------


def test_single_day_history_when_source_has_none(hub, monkeypatch):
    meta = next(
        c for c in COUNTRIES if c.curve_source not in ("ust", "ecb", "mof", "none")
    )
    latest = Curve(meta.code, (CurvePoint(0.25, 3.0), CurvePoint(10.0, 4.0)),
                   False, ("FRED",), "2026-11-04")
    monkeypatch.setattr(RatesProvider, "_curve_for", lambda self, m: latest)
    _run(meta.code)
    payload = hub.published[f"rates:curvehist:{meta.code}"]
    assert payload["days"] == [["2026-11-04", [[0.25, 3.0], [10.0, 4.0]]]]
    assert payload["sources"] == ["FRED"]
    assert payload["as_of"] == "2026-11-04"


def test_unknown_country_is_an_error(hub):
    _run("ZZ")
    assert "rates:curvehist:ZZ" in hub.errors


def test_refresh_routes_curvehist_topics(hub, monkeypatch):
    seen = []
    monkeypatch.setattr(
        RatesProvider, "_fetch_curvehist",
        lambda self, topic, code, today=None: seen.append((topic, code)),
    )
    RatesProvider().refresh(["rates:curvehist:US"])
    assert seen == [("rates:curvehist:US", "US")]


def test_ust_url_is_parameterised_by_year():
    assert "/2025/all" in RatesProvider.ust_url(2025)
    assert "field_tdr_date_value=2025" in RatesProvider.ust_url(2025)
    assert RatesProvider.ust_url() == RatesProvider.ust_url(datetime.date.today().year)
