"""Bank of Canada, Tesouro Nacional and the (switched-off) Bank of England.

Fixtures in tests/fixtures/rates/ were recorded from the live upstreams on
2026-11-05 (boc_benchmark.json: the real ?recent=280 response; and
tesouro_prefixado.csv: the real file's header and its Prefixado rows, thinned,
plus one Selic day). The BoE workbook is synthetic, built here. Nothing in this
file touches the network or the real application cache directory."""

import datetime
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

from aurantium import rates_meta
from aurantium.providers import rates
from aurantium.providers.rates import (
    RatesProvider,
    TesouroSchemaError,
    parse_boc,
    parse_boe,
    parse_tesouro,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rates"
TODAY = datetime.date(2026, 11, 5)
BOC_URL_NEEDLE = "bond_yields_benchmark"


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
def hub(monkeypatch, tmp_path):
    fake = _FakeHub()
    monkeypatch.setattr(rates.DataHub, "instance", classmethod(lambda cls: fake))
    # the Tesouro file lives on disk: NEVER let a test reach the real cache dir
    cache_dir = tmp_path / "cache" / "rates"
    monkeypatch.setattr(rates, "_tesouro_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(rates, "_today", lambda: TODAY)
    rates._HIST_CACHE.clear()
    yield fake
    rates._HIST_CACHE.clear()


def _stub(monkeypatch, routes: dict):
    calls: list[str] = []

    def fake(url, **kwargs):
        calls.append(url)
        for needle, body in routes.items():
            if needle in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise OSError(f"unexpected url {url}")

    def fake_download(url, dest):
        # the Tesouro file is streamed to disk; route it through the same stub
        Path(dest).write_text(fake(url), encoding="utf-8")

    monkeypatch.setattr(rates, "_get_text", fake)
    monkeypatch.setattr(rates, "_download", fake_download)
    return calls


def _run_curve(code):
    RatesProvider()._fetch_curve(f"rates:curve:{code}", code)


def _run_hist(code, today=TODAY):
    RatesProvider()._fetch_curvehist(f"rates:curvehist:{code}", code, today=today)


# -- Bank of Canada ----------------------------------------------------------


def test_boc_parses_benchmarks():
    curve = parse_boc(_read("boc_benchmark.json"))
    assert curve.code == "CA"
    assert curve.sources == ("Bank of Canada",)
    assert curve.as_of == "2026-11-04"
    got = {p.years: p.rate for p in curve.points}
    assert got == {2.0: 3.22, 3.0: 3.32, 5.0: 3.43, 7.0: 3.52, 10.0: 3.70, 30.0: 4.04}
    assert curve.complete
    # the real-return bond is not a nominal tenor
    assert 1.81 not in got.values()


def test_boc_uses_latest_observation_per_series():
    text = json.dumps({"observations": [
        {"d": "2026-11-03", "BD.CDN.2YR.DQ.YLD": {"v": "3.00"},
         "BD.CDN.10YR.DQ.YLD": {"v": "3.60"}},
        {"d": "2026-11-04", "BD.CDN.2YR.DQ.YLD": {"v": "3.10"}},
    ]})
    got = {p.years: p.rate for p in parse_boc(text).points}
    assert got == {2.0: 3.10, 10.0: 3.60}


@pytest.mark.parametrize("bad", ["", "not json", "{}", '{"observations": []}'])
def test_boc_garbage_is_an_empty_curve(bad):
    curve = parse_boc(bad)
    assert curve.points == () and not curve.complete


def test_boc_one_request_feeds_curve_and_history(hub, monkeypatch):
    calls = _stub(monkeypatch, {BOC_URL_NEEDLE: _read("boc_benchmark.json")})
    _run_curve("CA")
    _run_hist("CA")
    assert len(calls) == 1 and "recent=280" in calls[0]
    curve = hub.published["rates:curve:CA"]
    assert curve["points"][0] == [2.0, 3.22] and curve["observed"] == 6
    hist = hub.published["rates:curvehist:CA"]
    dates = [d for d, _ in hist["days"]]
    assert dates == sorted(dates) and 250 < len(dates) <= 280
    assert hist["as_of"] == dates[-1] == "2026-11-04"
    assert dict(map(tuple, hist["days"][-1][1]))[10.0] == 3.70


def test_boc_failure_is_an_error_not_a_crash(hub, monkeypatch):
    _stub(monkeypatch, {BOC_URL_NEEDLE: OSError("down")})
    _run_curve("CA")
    _run_hist("CA")
    assert "rates:curve:CA" in hub.errors and "rates:curvehist:CA" in hub.errors


# -- Tesouro Nacional --------------------------------------------------------


def test_tesouro_builds_nominal_curve():
    curve = parse_tesouro(_read("tesouro_prefixado.csv"))
    assert curve.code == "BR"
    assert curve.sources == ("Tesouro Nacional (ODbL)",)
    assert curve.as_of == "2026-10-30"
    base = datetime.date(2026, 10, 30)
    by_maturity = {round(p.years, 3): p.rate for p in curve.points}
    # LTN 01/01/2032 and NTN-F 01/01/2037, rate = Taxa Compra Manha
    assert by_maturity[round((datetime.date(2032, 1, 1) - base).days / 365.25, 3)] == 15.38
    assert by_maturity[round((datetime.date(2037, 1, 1) - base).days / 365.25, 3)] == 15.23
    years = [p.years for p in curve.points]
    assert years == sorted(years)
    # Selic and the other families are not a nominal curve; the 2027, 2029 and 2031 maturities
    # appear in both families and count once
    assert len(curve.points) == 8
    assert curve.complete


def test_tesouro_prefixado_wins_a_shared_maturity():
    # 01/01/2031 exists in both families (15,30 vs 15,29): one point, the LTN
    curve = parse_tesouro(_read("tesouro_prefixado.csv"))
    base = datetime.date(2026, 10, 30)
    y = (datetime.date(2031, 1, 1) - base).days / 365.25
    assert [p.rate for p in curve.points if abs(p.years - y) < 1e-9] == [15.30]


def test_tesouro_schema_drift_degrades(hub, monkeypatch):
    drifted = _read("tesouro_prefixado.csv").replace("Taxa Compra Manha", "Taxa Compra")
    with pytest.raises(TesouroSchemaError) as err:
        parse_tesouro(drifted)
    assert "Taxa Compra Manha" in str(err.value)
    _stub(monkeypatch, {"precotaxatesourodireto": drifted})
    _run_curve("BR")
    _run_hist("BR")
    assert "Taxa Compra Manha" in hub.errors["rates:curve:BR"]
    assert "rates:curvehist:BR" in hub.errors
    assert "rates:curve:BR" not in hub.published
    # a drifted file must not be kept on disk as if it were good
    cache = rates._tesouro_cache_dir()
    assert not cache.exists() or not list(cache.iterdir())   # no .csv, no .part


def test_tesouro_history_is_nominal_only_and_windowed(hub, monkeypatch):
    _stub(monkeypatch, {"precotaxatesourodireto": _read("tesouro_prefixado.csv")})
    _run_hist("BR")
    payload = hub.published["rates:curvehist:BR"]
    dates = [d for d, _ in payload["days"]]
    assert set(payload) == {"code", "days", "sources", "as_of"}
    assert payload["sources"] == ["Tesouro Nacional (ODbL)"]
    assert dates == sorted(dates) and len(dates) == len(set(dates))
    assert dates[-1] == payload["as_of"] == "2026-10-30"
    cutoff = (TODAY - datetime.timedelta(days=RatesProvider.HISTORY_DAYS)).isoformat()
    assert dates[0] >= cutoff and len(dates) >= 80
    for _, points in payload["days"]:
        assert [p[0] for p in points] == sorted(p[0] for p in points)
        assert all(0 < p[0] < 40 for p in points)


def test_tesouro_payloads_never_carry_raw_rows(hub, monkeypatch):
    _stub(monkeypatch, {"precotaxatesourodireto": _read("tesouro_prefixado.csv")})
    _run_curve("BR")
    _run_hist("BR")
    blob = json.dumps(hub.published)
    for forbidden in ("Taxa Compra", "Taxa Venda", "PU ", "Tipo Titulo", "Data Base"):
        assert forbidden not in blob


def test_tesouro_downloads_once_a_day_and_keeps_it_on_disk(hub, monkeypatch):
    calls = _stub(monkeypatch, {"precotaxatesourodireto": _read("tesouro_prefixado.csv")})
    _run_curve("BR")
    _run_hist("BR")
    assert len(calls) == 1                       # curve + history share one download
    files = list(rates._tesouro_cache_dir().glob("*.csv"))
    assert [f.name for f in files] == ["precotaxatesourodireto-2026-11-05.csv"]

    rates._HIST_CACHE.clear()                    # "next launch", same day
    _run_curve("BR")
    assert len(calls) == 1                       # served from disk


def test_tesouro_old_copies_are_deleted_after_a_fresh_download(hub, monkeypatch):
    cache = rates._tesouro_cache_dir()
    cache.mkdir(parents=True)
    old = cache / "precotaxatesourodireto-2026-11-01.csv"
    old.write_text(_read("tesouro_prefixado.csv"), encoding="utf-8")
    _stub(monkeypatch, {"precotaxatesourodireto": _read("tesouro_prefixado.csv")})
    _run_curve("BR")
    assert not old.exists()
    assert len(list(cache.glob("*.csv"))) == 1


def test_tesouro_falls_back_to_an_older_copy_when_offline(hub, monkeypatch):
    cache = rates._tesouro_cache_dir()
    cache.mkdir(parents=True)
    (cache / "precotaxatesourodireto-2026-11-01.csv").write_text(
        _read("tesouro_prefixado.csv"), encoding="utf-8")
    calls = _stub(monkeypatch, {"precotaxatesourodireto": OSError("offline")})
    _run_curve("BR")
    assert hub.published["rates:curve:BR"]["observed"] == 8
    # a fallback is served but not remembered as today's: the next refresh retries
    _run_curve("BR")
    assert len(calls) == 2
    assert [f.name for f in cache.iterdir()] == [
        "precotaxatesourodireto-2026-11-01.csv"]


def test_tesouro_cache_dir_is_a_rates_folder_of_the_app_cache():
    # the unpatched helper (this test has no hub fixture) names <cache>/rates;
    # it is only inspected here, never written
    assert rates._tesouro_cache_dir().name == "rates"


# -- Bank of England (off) ---------------------------------------------------


def _boe_zip() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "1. Notes"
    sheet = wb.create_sheet("4. spot curve")
    sheet["A1"] = "Spot curve"
    sheet["A3"] = "years:"
    for col, yrs in enumerate((0.5, 1, 2, 5, 10, 25), start=2):
        sheet.cell(row=3, column=col, value=yrs)
    rows = [
        (datetime.datetime(2026, 11, 3), (4.0, 4.1, 4.2, 4.3, 4.6, 5.0)),
        (datetime.datetime(2026, 11, 4), (4.01, 4.11, 4.21, 4.31, 4.61, 5.01)),
    ]
    for r, (when, vals) in enumerate(rows, start=5):
        sheet.cell(row=r, column=1, value=when)
        for c, v in enumerate(vals, start=2):
            sheet.cell(row=r, column=c, value=v)
    wb.create_sheet("4. real spot")                  # must be ignored
    buf = io.BytesIO()
    wb.save(buf)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("GLC Nominal daily data_2025 to present.xlsx", buf.getvalue())
        z.writestr("GLC Real daily data_2025 to present.xlsx", b"junk")
    return out.getvalue()


def test_boe_parses_the_nominal_spot_sheet():
    curve = parse_boe(_boe_zip())
    assert curve.code == "GB"
    assert curve.sources == ("Bank of England",)
    assert curve.as_of == "2026-11-04"
    assert [(p.years, p.rate) for p in curve.points] == [
        (0.5, 4.01), (1.0, 4.11), (2.0, 4.21), (5.0, 4.31), (10.0, 4.61), (25.0, 5.01)]
    assert curve.complete


def test_boe_garbage_is_an_empty_curve():
    assert parse_boe(b"").points == ()
    assert parse_boe(b"not a zip").points == ()


def test_boe_disabled_until_permitted(hub, monkeypatch):
    assert rates_meta.BOE_PERMITTED is False
    assert rates_meta.by_code("GB").curve_source == "fred"
    # even a meta forced to "boe" cannot reach the network while the flag is off
    calls = _stub(monkeypatch, {})
    monkeypatch.setattr(
        rates, "_get_bytes",
        lambda *a, **k: calls.append("bytes") or pytest.fail("BoE was fetched"),
    )
    forced = rates_meta.by_code("GB")._replace(curve_source="boe")
    with pytest.raises(RuntimeError, match="not permitted"):
        RatesProvider()._curve_for(forced)
    with pytest.raises(RuntimeError, match="not permitted"):
        RatesProvider()._history_for(forced, TODAY)
    assert calls == []


def test_boe_switch_is_one_flag_in_rates_meta():
    off = {c.code: c for c in rates_meta._build_countries(False)}
    on = {c.code: c for c in rates_meta._build_countries(True)}
    assert off["GB"].curve_source == "fred"
    assert on["GB"].curve_source == "boe"
    assert on["GB"].citation.endswith("Bank of England")
    assert {k: v for k, v in off.items() if k != "GB"} == {
        k: v for k, v in on.items() if k != "GB"}


def test_boe_enabled_fetches_the_zip(hub, monkeypatch):
    monkeypatch.setattr(rates_meta, "BOE_PERMITTED", True)
    gb = next(c for c in rates_meta._build_countries(True) if c.code == "GB")
    monkeypatch.setattr(rates, "_get_bytes", lambda url, **k: _boe_zip())
    curve = RatesProvider()._curve_for(gb)
    assert curve.as_of == "2026-11-04"


# -- citations ---------------------------------------------------------------


def test_citations_present_for_new_sources(hub, monkeypatch):
    _stub(monkeypatch, {
        BOC_URL_NEEDLE: _read("boc_benchmark.json"),
        "precotaxatesourodireto": _read("tesouro_prefixado.csv"),
    })
    _run_curve("CA")
    _run_curve("BR")
    _run_hist("CA")
    _run_hist("BR")
    assert "Bank of Canada" in hub.published["rates:curve:CA"]["sources"]
    assert "Bank of Canada" in hub.published["rates:curvehist:CA"]["sources"]
    assert "Tesouro Nacional (ODbL)" in hub.published["rates:curve:BR"]["sources"]
    assert "Tesouro Nacional (ODbL)" in hub.published["rates:curvehist:BR"]["sources"]
    assert "Bank of Canada" in rates_meta.by_code("CA").citation
    assert "Tesouro Nacional (ODbL)" in rates_meta.by_code("BR").citation


# -- folded Task 1 review minors ----------------------------------------------


def test_ecb_history_fetches_tenors_in_parallel_but_capped(hub, monkeypatch):
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def fake(url, **kwargs):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        if "SR_5Y" in url:
            raise OSError("one tenor down")          # skip-on-failure survives
        return "KEY,TIME_PERIOD,OBS_VALUE\nk,2026-11-04,2.2\n"

    monkeypatch.setattr(rates, "_get_text", fake)
    _run_hist("XM")
    assert 2 <= state["max"] <= 4
    pts = hub.published["rates:curvehist:XM"]["days"][-1][1]
    assert len(pts) == 10 and 5.0 not in [p[0] for p in pts]


def test_mof_archive_is_parsed_once_a_day(hub, monkeypatch):
    archive = _read("mof_hist_all.csv")
    calls = _stub(monkeypatch, {
        "historical/jgbcme_all.csv": archive,
        "interest_rate/jgbcme.csv": _read("mof_hist_month.csv"),
    })
    parsed = []
    real = rates.parse_mof_history
    monkeypatch.setattr(rates, "parse_mof_history",
                        lambda t: parsed.append(t) or real(t))
    _run_hist("JP")
    _run_hist("JP")
    assert sum("historical/" in u for u in calls) == 1
    assert sum(t == archive for t in parsed) == 1   # parsed once, not per refresh
    assert hub.published["rates:curvehist:JP"]["as_of"] == "2026-11-05"


# -- review fixes ------------------------------------------------------------


def test_concurrent_curve_and_history_share_one_download(hub, monkeypatch):
    """rates:curve:BR and rates:curvehist:BR run together on the hub's pool;
    the second must wait for the first, not download and parse again."""
    calls = []
    body = _read("tesouro_prefixado.csv")
    boc = _read("boc_benchmark.json")

    def slow_download(url, dest):
        calls.append(url)
        time.sleep(0.2)
        Path(dest).write_text(body, encoding="utf-8")

    def slow_text(url, **kwargs):
        calls.append(url)
        time.sleep(0.2)
        return boc

    monkeypatch.setattr(rates, "_download", slow_download)
    monkeypatch.setattr(rates, "_get_text", slow_text)
    threads = [
        threading.Thread(target=_run_curve, args=(c,)) for c in ("BR", "CA")
    ] + [
        threading.Thread(target=_run_hist, args=(c,)) for c in ("BR", "CA")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum("precotaxatesourodireto" in u for u in calls) == 1
    assert sum(BOC_URL_NEEDLE in u for u in calls) == 1
    assert not hub.errors
    assert {"rates:curve:BR", "rates:curvehist:BR",
            "rates:curve:CA", "rates:curvehist:CA"} <= set(hub.published)
    cache = rates._tesouro_cache_dir()
    assert [f.name for f in cache.iterdir()] == ["precotaxatesourodireto-2026-11-05.csv"]


def test_a_good_save_sweeps_stale_part_files(hub, monkeypatch):
    cache = rates._tesouro_cache_dir()
    cache.mkdir(parents=True)
    (cache / "abc123.part").write_text("x")
    (cache / "precotaxatesourodireto-2026-10-01.tmp").write_text("x")
    _stub(monkeypatch, {"precotaxatesourodireto": _read("tesouro_prefixado.csv")})
    _run_curve("BR")
    assert [f.name for f in cache.iterdir()] == ["precotaxatesourodireto-2026-11-05.csv"]


def test_download_streams_to_the_file(monkeypatch, tmp_path):
    chunks = [b"Tipo Titulo;", b"Data Base"]

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            return iter(chunks)

        @property
        def content(self):
            raise AssertionError("must not buffer the whole body")

        text = content

    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return Resp()

    monkeypatch.setattr(rates.requests, "get", fake_get)
    dest = tmp_path / "x.part"
    rates._download("http://example.test/f.csv", dest)
    assert seen["stream"] is True
    assert dest.read_bytes() == b"Tipo Titulo;Data Base"


def test_boe_prefers_the_spot_curve_sheet_over_the_short_end_sheet():
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "1. Notes"
    short = wb.create_sheet("4. spot curve short end")
    spot = wb.create_sheet("4. spot curve")
    for sheet, bump in ((short, 9.0), (spot, 0.0)):
        for col, yrs in enumerate((0.5, 1, 2, 5, 10, 25), start=2):
            sheet.cell(row=3, column=col, value=yrs)
        sheet.cell(row=5, column=1, value=datetime.datetime(2026, 11, 4))
        for col in range(2, 8):
            sheet.cell(row=5, column=col, value=4.0 + bump)
    buf = io.BytesIO()
    wb.save(buf)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("GLC Nominal daily data.xlsx", buf.getvalue())
    assert {p.rate for p in parse_boe(out.getvalue()).points} == {4.0}


def test_boe_tenors_cover_the_published_range():
    assert max(rates_meta._build_countries(True)[3].tenors) == 40.0
