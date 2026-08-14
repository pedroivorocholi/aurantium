"""Global rates provider: BIS policy rates, sovereign yield curves.

Serves rates:policy (one envelope, all jurisdictions) and rates:curve:<CC>.
HTTP and parsing are kept separate — every upstream has a pure parse_*()
function tested against recorded fixtures, plus a thin fetch_*() wrapper.
"""

from __future__ import annotations

import csv
import datetime
import io
import os
import re
from typing import Any, Callable, NamedTuple, Optional

import requests

from .. import rates_allowlist
from ..datahub import DataHub, Provider
from ..rates_meta import UST, MOF, ECB, BIS, COUNTRIES, by_code


class PolicyPoint(NamedTuple):
    code: str
    rate: float
    as_of: str
    prev: Optional[float]
    last_change: Optional[str]
    direction: str          # "up" | "down" | "flat"


class CurvePoint(NamedTuple):
    years: float
    rate: float


class Curve(NamedTuple):
    code: str
    points: tuple[CurvePoint, ...]
    complete: bool
    sources: tuple[str, ...]
    as_of: str


def _f(value: Any) -> Optional[float]:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # reject NaN


def direction_of(rate: float, prev: Optional[float]) -> str:
    if prev is None or rate == prev:
        return "flat"
    return "up" if rate > prev else "down"


# -- FRED access control ---------------------------------------------------


class FredNotAllowed(RuntimeError):
    """Raised when a FRED series is not on the vetted allowlist.

    FRED carries third-party copyrighted series whose commercial
    redistribution is not permitted; see tools/verify_rates.py."""


def fred_allowed(series_id: str) -> bool:
    # read through the module, not a from-import: a from-import would bind the
    # frozenset here and make the allowlist untestable by monkeypatch
    return series_id in rates_allowlist.ALLOWED


def fetch_fred_series(
    series_id: str,
    api_key: str,
    *,
    limit: int = 24,
    get: Callable[..., Any] = requests.get,
) -> list[tuple[str, float]]:
    """Observations for one FRED series, oldest -> newest.

    Refuses a non-allowlisted series BEFORE making any request, so a network
    failure can never fail open."""
    if not fred_allowed(series_id):
        raise FredNotAllowed(series_id)
    resp = get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        },
        timeout=15,
    )
    resp.raise_for_status()
    out: list[tuple[str, float]] = []
    for obs in resp.json().get("observations", []):
        raw = obs.get("value")
        if raw in (None, ".", ""):
            continue
        try:
            out.append((obs.get("date", ""), float(raw)))
        except (TypeError, ValueError):
            continue
    out.reverse()
    return out


#: BIS column names vary by API version; try each in order.
_BIS_AREA_KEYS = ("REF_AREA", "ref_area", "JURISDICTION", "Reference area")
_BIS_DATE_KEYS = ("TIME_PERIOD", "time_period", "Period")
_BIS_VALUE_KEYS = ("OBS_VALUE", "obs_value", "Value")


def _pick(row: dict, keys: tuple[str, ...]) -> Optional[str]:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def parse_bis_policy(text: str) -> dict[str, PolicyPoint]:
    """BIS CBPOL CSV -> {ISO code: PolicyPoint}.

    The series carries history, so the last change and its direction are
    derived by walking back until the rate differs. Never raises: a
    malformed feed yields {} and the panel degrades to the FRED columns."""
    if not text or not text.strip():
        return {}
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception:
        return {}

    # group observations by jurisdiction, oldest -> newest
    series: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        area = _pick(row, _BIS_AREA_KEYS)
        period = _pick(row, _BIS_DATE_KEYS)
        value = _f(_pick(row, _BIS_VALUE_KEYS))
        if not area or not period or value is None:
            continue
        code = area.strip().upper()
        if by_code(code) is None:
            continue  # a jurisdiction we don't cover
        series.setdefault(code, []).append((period, value))

    out: dict[str, PolicyPoint] = {}
    for code, observations in series.items():
        observations.sort()
        as_of, rate = observations[-1]
        prev: Optional[float] = None
        last_change: Optional[str] = None
        for period, value in reversed(observations[:-1]):
            if value != rate:
                prev, last_change = value, period
                break
        out[code] = PolicyPoint(
            code=code,
            rate=rate,
            as_of=as_of,
            prev=prev,
            last_change=last_change,
            direction=direction_of(rate, prev),
        )
    return out


_EMPTY_US = Curve("US", (), False, (UST,), "")

#: Treasury's header mixes unit spellings within one row — "1 Mo", "1.5 Month",
#: "2 Mo", "4 Mo", "10 Yr" — so a lookup table can't cover it. Normalize.
_TENOR_LABEL = re.compile(r"^\s*([\d.]+)\s*(Mo(?:nth)?s?|Yrs?|Years?|Y|M)\s*$", re.I)


def tenor_years(label: str) -> Optional[float]:
    """"3 Mo" -> 0.25, "1.5 Month" -> 0.125, "10 Yr" -> 10.0, "40Y" -> 40.0.

    Returns None for anything that isn't a maturity label, which is how
    non-tenor columns ("Date") get skipped."""
    match = _TENOR_LABEL.match(label or "")
    if match is None:
        return None
    magnitude = _f(match.group(1))
    if magnitude is None:
        return None
    unit = match.group(2).lower()
    return magnitude / 12.0 if unit.startswith("m") else magnitude


def parse_ust_curve(text: str) -> Curve:
    """US Treasury daily par yield curve -> Curve.

    The CSV is scoped to a calendar year and sorted newest-first, so row 0 is
    today's curve. (The FiscalData JSON endpoint 404s — see the probe findings;
    do not reintroduce a JSON branch for it.)"""
    if not text or not text.strip():
        return _EMPTY_US
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception:
        return _EMPTY_US
    if not rows:
        return _EMPTY_US

    row = rows[0]
    as_of = str(row.get("Date", ""))
    points = []
    for label, cell in row.items():
        years = tenor_years(label)
        if years is None:
            continue
        value = _f(cell)   # Treasury leaves a tenor blank on days it isn't quoted
        if value is not None:
            points.append(CurvePoint(years, value))
    points.sort()
    return Curve("US", tuple(points), len(points) >= 5, (UST,), as_of)


def parse_ecb_curve(rows: dict[float, str]) -> Curve:
    """ECB publishes one series per tenor, so the caller fetches several and
    passes {maturity_years: csv_text}. Each CSV has a single observation."""
    points = []
    as_of = ""
    for years, text in sorted(rows.items()):
        try:
            parsed = list(csv.DictReader(io.StringIO(text or "")))
        except Exception:
            continue
        if not parsed:
            continue
        record = parsed[-1]
        value = _f(record.get("OBS_VALUE") or record.get("obs_value"))
        if value is None:
            continue
        as_of = str(record.get("TIME_PERIOD") or record.get("time_period") or as_of)
        points.append(CurvePoint(years, value))
    return Curve("XM", tuple(points), len(points) >= 5, (ECB,), as_of)


_EMPTY_JP = Curve("JP", (), False, (MOF,), "")

#: MOF dates are YYYY/M/D with no zero-padding, e.g. "2026/8/3"
_MOF_DATE = re.compile(r"^\s*\d{4}/\d{1,2}/\d{1,2}\s*$")


def parse_mof_curve(text: str) -> Curve:
    """Japan MOF JGB CSV -> Curve.

    The file is NOT a clean rectangle. Row 1 is a title carrying the units
    marker ("Interest Rate (August 2026),,,…,(Unit : %)"); row 2 is the real
    header ("Date,1Y,2Y,…,40Y"); then data rows; then a blank row and a footer
    note about clearing the browser cache. Taking the last non-empty row would
    read that footer as a curve — the last row whose first cell is a DATE is
    the one we want."""
    if not text or not text.strip():
        return _EMPTY_JP
    try:
        rows = [r for r in csv.reader(io.StringIO(text)) if r]
    except Exception:
        return _EMPTY_JP

    header_idx = None
    for i, row in enumerate(rows):
        if sum(1 for cell in row if tenor_years(cell) is not None) >= 5:
            header_idx = i
            break
    if header_idx is None:
        return _EMPTY_JP

    last = None
    for row in rows[header_idx + 1:]:
        if row and _MOF_DATE.match(row[0]):
            last = row
    if last is None:
        return _EMPTY_JP

    header = rows[header_idx]
    points = []
    for col, label in enumerate(header):
        years = tenor_years(label)
        if years is None or col >= len(last):
            continue
        value = _f(last[col])
        if value is not None:
            points.append(CurvePoint(years, value))
    points.sort()
    return Curve("JP", tuple(points), len(points) >= 5, (MOF,), as_of=last[0].strip())


# -- the join --------------------------------------------------------------


def build_policy_payload(
    policy: dict[str, PolicyPoint],
    enrich: dict[str, dict],
    partial: list[str],
) -> dict:
    """Join BIS policy rates with FRED short/long enrichment.

    Degrades per-source: whatever assembled is published, with the names of
    the sources that failed in `partial`."""
    countries: list[dict] = []
    sources: set[str] = set()
    codes = set(policy) | set(enrich)
    for meta in COUNTRIES:
        if meta.code not in codes:
            continue
        point = policy.get(meta.code)
        extra = enrich.get(meta.code) or {}
        short = extra.get("short")
        long = extra.get("long")
        # a slope needs two OBSERVED yields; a policy rate is not a 2y
        slope = None
        if short is not None and long is not None:
            slope = (long - short) * 100.0
        if point is not None:
            sources.add(BIS)
        if short is not None or long is not None:
            sources.add("FRED")
        countries.append(
            {
                "code": meta.code,
                "label": meta.label,
                "policy": point.rate if point else None,
                "prev": point.prev if point else None,
                "last_change": point.last_change if point else None,
                "direction": point.direction if point else "flat",
                "short": short,
                "long": long,
                "slope": slope,
                "citation": meta.citation,
                "has_curve": meta.curve_source != "none",
            }
        )
    countries.sort(key=lambda r: r["label"])
    as_of = max((p.as_of for p in policy.values()), default="")
    return {
        "countries": countries,
        "partial": list(partial),
        "sources": sorted(sources),
        "as_of": as_of,
    }


def build_curve_payload(curve: Curve) -> dict:
    """Serialize a Curve for the hub. Adds nothing the source didn't publish."""
    return {
        "code": curve.code,
        "points": [[p.years, p.rate] for p in curve.points],
        "complete": bool(curve.complete),
        "observed": len(curve.points),
        "sources": list(curve.sources),
        "as_of": curve.as_of,
    }


# -- the provider ----------------------------------------------------------

_ECB_SERIES = {
    0.25: "SR_3M", 0.5: "SR_6M", 1.0: "SR_1Y", 2.0: "SR_2Y", 3.0: "SR_3Y",
    5.0: "SR_5Y", 7.0: "SR_7Y", 10.0: "SR_10Y", 15.0: "SR_15Y",
    20.0: "SR_20Y", 30.0: "SR_30Y",
}
_ECB_BASE = "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM."
_TIMEOUT = 20
_UA = {"User-Agent": "aurantium/1.0"}


def _get_text(url: str, **kwargs) -> str:
    resp = requests.get(url, timeout=_TIMEOUT, headers=_UA, **kwargs)
    resp.raise_for_status()
    return resp.text


class RatesProvider(Provider):
    """Serves ``rates:policy`` and ``rates:curve:<CC>``."""

    # URLs confirmed live by tools/probe_rates.py — see
    # docs/superpowers/2026-08-07-rates-probe-findings.md. Both BIS's and
    # Treasury's first-choice candidates 404'd; these are the ones that work.
    # Do NOT "modernize" them back to the v2/fiscaldata paths.
    #: The v1 CSV endpoint ignores lastNObservations and returns the full
    #: history since 1986 (12.7 MB, 25k rows, 49 countries) — far too heavy to
    #: pull on every refresh. `startPeriod` IS honoured, though: measured
    #: 2026-08-14, the 3-year window returns 678 KB / 1,657 rows against the
    #: unwindowed 12.7 MB / 25,055. Keep the window.
    BIS_URL = "https://stats.bis.org/api/v1/data/WS_CBPOL/M../all?format=csv"
    BIS_URL_WINDOWED = BIS_URL + "&startPeriod={start}"
    MOF_URL = (
        "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
    )

    @staticmethod
    def ust_url() -> str:
        """Treasury's CSV export is scoped to a calendar year, so the year must
        be current — a hardcoded one silently returns nothing every January."""
        year = datetime.date.today().year
        return (
            "https://home.treasury.gov/resource-center/data-chart-center/"
            f"interest-rates/daily-treasury-rates.csv/{year}/all"
            f"?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
            "&page&_format=csv"
        )

    def topic_patterns(self) -> list[str]:
        return ["rates:*"]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        for topic in topics:
            parts = topic.split(":")
            if len(parts) == 2 and parts[1] == "policy":
                hub.run_async(lambda t=topic: self._fetch_policy(t))
            elif len(parts) == 3 and parts[1] == "curve":
                hub.run_async(lambda t=topic, c=parts[2]: self._fetch_curve(t, c))
            else:
                hub.publish_error(topic, f"unrecognized topic: {topic}")

    # -- rates:policy ------------------------------------------------------

    def _fetch_policy(self, topic: str) -> None:
        hub = DataHub.instance()
        partial: list[str] = []

        policy: dict[str, PolicyPoint] = {}
        try:
            policy = parse_bis_policy(self._bis_text())
        except Exception:
            partial.append(BIS)
        if not policy and BIS not in partial:
            partial.append(BIS)

        enrich = self._fred_enrichment(partial)

        if not policy and not enrich:
            hub.publish_error(topic, "no rates sources reachable")
            return
        hub.publish(topic, build_policy_payload(policy, enrich, partial))

    def _bis_text(self) -> str:
        """Fetch BIS policy rates, windowed if the API allows it.

        Three years is enough to derive the last change for any jurisdiction
        that has moved recently; the full feed is 12.7 MB and we only need the
        tail. If BIS rejects startPeriod, fall back to the full feed rather
        than failing — correctness beats bandwidth."""
        start = f"{datetime.date.today().year - 3}-01"
        try:
            return _get_text(self.BIS_URL_WINDOWED.format(start=start))
        except Exception:
            return _get_text(self.BIS_URL)

    def _fred_enrichment(self, partial: list[str]) -> dict[str, dict]:
        """Short/long yields for countries no curve source reaches.

        A missing FRED key is a NORMAL state, not an error: the free tier
        must work with zero configuration. Blank columns, no warning."""
        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            return {}
        out: dict[str, dict] = {}
        failures = 0
        for meta in COUNTRIES:
            if not (meta.fred_short or meta.fred_long):
                continue
            entry: dict = {}
            for field, series_id in (
                ("short", meta.fred_short),
                ("long", meta.fred_long),
            ):
                if not series_id:
                    continue
                try:
                    observations = fetch_fred_series(series_id, api_key, limit=2)
                except FredNotAllowed:
                    continue  # refused by the copyright filter, by design
                except Exception:
                    failures += 1
                    continue
                if observations:
                    entry[field] = observations[-1][1]
            if entry:
                out[meta.code] = entry
        if failures and "FRED" not in partial:
            partial.append("FRED")
        return out

    # -- rates:curve:<CC> --------------------------------------------------

    def _fetch_curve(self, topic: str, code: str) -> None:
        hub = DataHub.instance()
        meta = by_code(code)
        if meta is None:
            hub.publish_error(topic, f"unknown country: {code}")
            return
        if meta.curve_source == "none":
            hub.publish(
                topic,
                {
                    "code": meta.code,
                    "points": [],
                    "complete": False,
                    "observed": 0,
                    "sources": [BIS],
                    "as_of": "",
                    "note": f"No curve published for {meta.label}. Policy rate only.",
                },
            )
            return
        try:
            curve = self._curve_for(meta)
        except Exception as exc:
            hub.publish_error(topic, f"curve fetch failed for {meta.code}: {exc}")
            return
        hub.publish(topic, build_curve_payload(curve))

    def _curve_for(self, meta) -> Curve:
        if meta.curve_source == "ust":
            return parse_ust_curve(_get_text(self.ust_url()))
        if meta.curve_source == "mof":
            return parse_mof_curve(_get_text(self.MOF_URL))
        if meta.curve_source == "ecb":
            rows = {}
            for years, suffix in _ECB_SERIES.items():
                if years not in meta.tenors:
                    continue
                try:
                    rows[years] = _get_text(
                        f"{_ECB_BASE}{suffix}?format=csvdata&lastNObservations=1"
                    )
                except Exception:
                    continue
            return parse_ecb_curve(rows)
        # "fred": the sparse case — observed points only, never interpolated
        api_key = os.environ.get("FRED_API_KEY")
        points, as_of = [], ""
        if api_key:
            for years, series_id in (
                (0.25, meta.fred_short),
                (10.0, meta.fred_long),
            ):
                if not series_id:
                    continue
                try:
                    observations = fetch_fred_series(series_id, api_key, limit=2)
                except Exception:
                    continue
                if observations:
                    as_of = max(as_of, observations[-1][0])
                    points.append(CurvePoint(years, observations[-1][1]))
        points.sort()
        return Curve(meta.code, tuple(points), False, ("FRED",), as_of)
