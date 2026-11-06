"""Global rates provider: BIS policy rates, sovereign yield curves.

Serves rates:policy (one envelope, all jurisdictions) and rates:curve:<CC>.
HTTP and parsing are kept separate — every upstream has a pure parse_*()
function tested against recorded fixtures, plus a thin fetch_*() wrapper.
"""

from __future__ import annotations

import concurrent.futures
import csv
import datetime
import io
import json
import os
import re
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

import requests

from .. import rates_allowlist
from ..datahub import DataHub, Provider
from .. import rates_meta
from ..rates_meta import UST, MOF, ECB, BIS, BOC, TESOURO, BOE, COUNTRIES, by_code


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
    redistribution is not permitted; see tools/data/verify_rates.py."""


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


# -- Bank of Canada -----------------------------------------------------------

#: Valet series ids of the group ``bond_yields_benchmark`` (read from the live
#: seriesDetail, 2026-11-05). LONG is the long-term benchmark, roughly 30y.
#: BD.CDN.RRB.DQ.YLD is the real-return bond - not a nominal tenor, so it is
#: deliberately absent.
_BOC_SERIES = {
    "BD.CDN.2YR.DQ.YLD": 2.0,
    "BD.CDN.3YR.DQ.YLD": 3.0,
    "BD.CDN.5YR.DQ.YLD": 5.0,
    "BD.CDN.7YR.DQ.YLD": 7.0,
    "BD.CDN.10YR.DQ.YLD": 10.0,
    "BD.CDN.LONG.DQ.YLD": 30.0,
}
_EMPTY_CA = Curve("CA", (), False, (BOC,), "")


def _boc_observations(text: str) -> list:
    """Valet JSON -> observation dicts, oldest first. Never raises."""
    try:
        obs = json.loads(text or "").get("observations") or []
    except (ValueError, AttributeError):
        return []
    obs = [o for o in obs if isinstance(o, dict) and _iso(str(o.get("d", "")))]
    return sorted(obs, key=lambda o: o["d"])


def _boc_point(obs: dict, series: str) -> Optional[float]:
    cell = obs.get(series)
    return _f(cell.get("v")) if isinstance(cell, dict) else None


def parse_boc(text: str) -> Curve:
    """Valet benchmark-yield JSON -> the latest Curve, using the most recent
    observation OF EACH SERIES."""
    latest: dict[float, tuple[str, float]] = {}
    for obs in _boc_observations(text):          # ascending, later days overwrite
        for series, years in _BOC_SERIES.items():
            value = _boc_point(obs, series)
            if value is not None:
                latest[years] = (obs["d"], value)
    if not latest:
        return _EMPTY_CA
    points = tuple(CurvePoint(y, v) for y, (_, v) in sorted(latest.items()))
    as_of = max(d for d, _ in latest.values())
    return Curve("CA", points, len(points) >= 5, (BOC,), as_of)


def parse_boc_history(text: str) -> list:
    """Every observation day of a Valet response -> oldest-first days."""
    out = []
    for obs in _boc_observations(text):
        points = []
        for series, years in _BOC_SERIES.items():
            value = _boc_point(obs, series)
            if value is not None:
                points.append([years, value])
        if points:
            points.sort()
            out.append((_iso(obs["d"]), points))
    return out


# -- Tesouro Nacional ---------------------------------------------------------


class TesouroSchemaError(ValueError):
    """The Tesouro CSV no longer has the columns we read."""


#: nominal (fixed-rate) families, in priority order: when both list a maturity
#: the zero-coupon LTN ("Tesouro Prefixado") wins over the coupon NTN-F
_TESOURO_NOMINAL = ("Tesouro Prefixado", "Tesouro Prefixado com Juros Semestrais")
_TESOURO_COLUMNS = ("Tipo Titulo", "Data Vencimento", "Data Base", "Taxa Compra Manha")


def _iso_dmy(text: str) -> Optional[str]:
    """Tesouro dd/mm/yyyy -> ISO (the shared _iso reads Treasury m/d/y)."""
    try:
        day, month, year = (int(x) for x in (text or "").strip().split("/"))
        return datetime.date(year, month, day).isoformat()
    except (ValueError, TypeError):
        return None


def _br_number(cell: str) -> Optional[float]:
    """"15,38" -> 15.38. The file uses a decimal comma and no thousands mark in
    the rate column."""
    return _f((cell or "").replace(",", "."))


def parse_tesouro_history(csv_text: str, since: str = "") -> list:
    """Tesouro Direto price/rate CSV -> oldest-first nominal curves,
    [(iso Data Base, [[years, rate], ...])], for every Data Base >= `since`
    (an ISO date, "" = all).

    The file is ';'-separated with decimal commas and dd/mm/yyyy dates. Only
    the two nominal families are read and only (maturity, rate) pairs leave this
    function: the raw rows are ODbL data we may cite but never re-export.
    Raises TesouroSchemaError when a needed column is missing."""
    text = (csv_text or "").lstrip("﻿")
    if not text.strip():
        raise TesouroSchemaError("Tesouro file is empty")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    missing = [c for c in _TESOURO_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise TesouroSchemaError(
            "Tesouro file changed its columns; missing " + ", ".join(missing)
        )
    by_day: dict[str, dict[str, tuple[int, float]]] = {}
    for row in reader:
        kind = row["Tipo Titulo"]
        if kind not in _TESOURO_NOMINAL:
            continue
        base = _iso_dmy(row["Data Base"])
        if base is None or base < since:
            continue
        due = _iso_dmy(row["Data Vencimento"])
        rate = _br_number(row["Taxa Compra Manha"])
        if due is None or rate is None:
            continue
        rank = _TESOURO_NOMINAL.index(kind)
        day = by_day.setdefault(base, {})
        if due not in day or rank < day[due][0]:
            day[due] = (rank, rate)
    out = []
    for base, bonds in sorted(by_day.items()):
        origin = datetime.date.fromisoformat(base)
        points = []
        for due, (_, rate) in bonds.items():
            years = (datetime.date.fromisoformat(due) - origin).days / 365.25
            if years > 0:
                points.append([years, rate])
        if points:
            points.sort()
            out.append((base, points))
    return out


def _tesouro_curve(days: list) -> Curve:
    if not days:
        raise TesouroSchemaError("Tesouro file has no nominal curve rows")
    as_of, points = days[-1]
    return Curve("BR", tuple(CurvePoint(y, r) for y, r in points),
                 len(points) >= 5, (TESOURO,), as_of)


def parse_tesouro(csv_text: str) -> Curve:
    """The latest Data Base's nominal curve: Prefixado + Prefixado com Juros
    Semestrais, years = (vencimento - data base) / 365.25, rate = Taxa Compra
    Manha."""
    return _tesouro_curve(parse_tesouro_history(csv_text))


# -- Bank of England (switched off, see rates_meta.BOE_PERMITTED) --------------


def parse_boe(zip_bytes: bytes) -> Curve:
    """The Bank of England "latest yield curve data" ZIP -> Curve, read from the
    nominal workbook's spot-curve sheet (rows = dates, columns = maturity in
    years, a header row of maturities above the data).

    UNVERIFIED against the real workbook: built from a synthetic one, because
    nothing fetches BoE while rates_meta.BOE_PERMITTED is False. Re-check the
    sheet names and header layout against the real file when permission
    arrives. Never raises; unreadable -> an empty curve."""
    empty = Curve("GB", (), False, (BOE,), "")
    try:
        import openpyxl

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            name = next(
                n for n in z.namelist()
                if "nominal" in n.lower() and n.lower().endswith((".xlsx", ".xlsm"))
            )
            book = openpyxl.load_workbook(
                io.BytesIO(z.read(name)), read_only=True, data_only=True
            )
        # BoE ships both a "spot curve" and a "spot curve short end" sheet:
        # take the full one, and only fall back to any non-short, non-real spot
        names = [t for t in book.sheetnames
                 if "spot" in t.lower() and "short" not in t.lower()
                 and "real" not in t.lower()]
        title = next((t for t in names if "spot curve" in t.lower()), names[0])
        rows = [list(r) for r in book[title].iter_rows(values_only=True)]
    except Exception:
        return empty

    def num(cell) -> Optional[float]:
        if isinstance(cell, (int, float)) and not isinstance(cell, bool):
            return _f(cell)
        return None

    header, start = None, 0
    for i, row in enumerate(rows):
        years = [num(c) for c in row[1:]]
        if sum(y is not None for y in years) >= 5:
            header, start = years, i + 1
            break
    if header is None:
        return empty
    last = None
    for row in rows[start:]:
        if row and isinstance(row[0], datetime.date):
            if any(num(c) is not None for c in row[1:]):
                last = row
    if last is None:
        return empty
    points = []
    for col, years in enumerate(header, start=1):
        value = num(last[col]) if col < len(last) else None
        if years is not None and value is not None:
            points.append(CurvePoint(years, value))
    points.sort()
    when = last[0].date() if isinstance(last[0], datetime.datetime) else last[0]
    return Curve("GB", tuple(points), len(points) >= 5, (BOE,), when.isoformat())


# -- curve history ---------------------------------------------------------


def _iso(date_text: str) -> Optional[str]:
    """Normalise the date spellings the sources use to YYYY-MM-DD:
    Treasury "11/05/2026", MOF "2026/8/3", ECB/FRED "2026-08-06"."""
    text = (date_text or "").strip()
    try:
        if "/" in text:
            a, b, c = (int(x) for x in text.split("/"))
            if a > 31:
                return datetime.date(a, b, c).isoformat()
            return datetime.date(c, a, b).isoformat()
        return datetime.date.fromisoformat(text[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def parse_ust_history(text: str) -> list:
    """Every row of a Treasury yearly CSV -> [(iso, [[years, rate], ...])],
    oldest first. Blank cells (a tenor not quoted that day) are skipped, never
    zero-filled."""
    if not text or not text.strip():
        return []
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception:
        return []
    out = []
    for row in rows:
        iso = _iso(str(row.get("Date", "")))
        if iso is None:
            continue
        points = []
        for label, cell in row.items():
            years = tenor_years(label)
            value = _f(cell) if years is not None else None
            if value is not None:
                points.append([years, value])
        if points:
            points.sort()
            out.append((iso, points))
    out.sort(key=lambda d: d[0])
    return out


def parse_mof_history(text: str) -> list:
    """Every dated row of a MOF JGB CSV (the monthly file or the all-time
    archive; same layout parse_mof_curve reads) -> oldest-first days.
    "-" cells (a tenor not yet issued on that date) are skipped."""
    if not text or not text.strip():
        return []
    try:
        rows = [r for r in csv.reader(io.StringIO(text)) if r]
    except Exception:
        return []
    header = None
    start = 0
    for i, row in enumerate(rows):
        if sum(1 for cell in row if tenor_years(cell) is not None) >= 5:
            header, start = row, i + 1
            break
    if header is None:
        return []
    out = []
    for row in rows[start:]:
        if not _MOF_DATE.match(row[0]):
            continue
        iso = _iso(row[0])
        points = []
        for col, label in enumerate(header):
            years = tenor_years(label)
            if years is None or col >= len(row):
                continue
            value = _f(row[col])
            if value is not None:
                points.append([years, value])
        if iso and points:
            points.sort()
            out.append((iso, points))
    out.sort(key=lambda d: d[0])
    return out


def parse_ecb_history(rows: dict[float, str]) -> list:
    """{maturity_years: csv_text} (each a full series) -> oldest-first days,
    joined by date. A day appears if at least one tenor was published."""
    by_date: dict[str, list] = {}
    for years, text in rows.items():
        try:
            parsed = list(csv.DictReader(io.StringIO(text or "")))
        except Exception:
            continue
        for record in parsed:
            iso = _iso(str(record.get("TIME_PERIOD") or record.get("time_period") or ""))
            value = _f(record.get("OBS_VALUE") or record.get("obs_value"))
            if iso and value is not None:
                by_date.setdefault(iso, []).append([years, value])
    return [(iso, sorted(pts)) for iso, pts in sorted(by_date.items())]


def build_history_payload(code: str, days: list, sources: tuple[str, ...]) -> dict:
    """Serialize curve history for the hub: days oldest -> newest."""
    days = sorted(days, key=lambda d: d[0])
    return {
        "code": code,
        "days": [[iso, [list(p) for p in points]] for iso, points in days],
        "sources": list(sources),
        "as_of": days[-1][0] if days else "",
    }


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

#: key -> (iso date fetched, value): downloaded text keyed by URL, and parsed
#: results keyed "parsed:..."; see RatesProvider._cached_text / _cached_parsed
_HIST_CACHE: dict[str, tuple[str, Any]] = {}


def _today() -> datetime.date:
    return datetime.date.today()


def _get_bytes(url: str, **kwargs) -> bytes:
    resp = requests.get(url, timeout=_TIMEOUT, headers=_UA, **kwargs)
    resp.raise_for_status()
    return resp.content


#: one lock per cache key, held across check + load + store so concurrent topics
#: (rates:curve:BR and rates:curvehist:BR share a pool) download and parse once
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class _Uncached:
    """A load() result to serve now but not remember as today's."""

    def __init__(self, value: Any):
        self.value = value


def _download(url: str, dest: Path) -> None:
    """Stream a big file straight to `dest`; never hold the body in memory."""
    with requests.get(url, timeout=_TIMEOUT, headers=_UA, stream=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(1 << 16):
                fh.write(chunk)


def _tesouro_cache_dir() -> Path:
    """<app cache>/rates, next to cache.py's SQLite file. Tests replace this."""
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation
    ) or str(Path.home() / ".aurantium" / "cache")
    return Path(base) / "rates"


def _get_text(url: str, **kwargs) -> str:
    resp = requests.get(url, timeout=_TIMEOUT, headers=_UA, **kwargs)
    resp.raise_for_status()
    return resp.text


class RatesProvider(Provider):
    """Serves ``rates:policy``, ``rates:curve:<CC>``, ``rates:curvehist:<CC>``."""

    # URLs confirmed live by tools/data/probe_rates.py — see
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
    # Measured 2026-11-05: MOF DOES publish history. The all-time archive lives
    # in a historical/ subfolder (a jgbcme_all.csv sibling 404s): 1.2 MB, one
    # row per business day since 1974-09-24. It only lags to the last month
    # end (it ended 2026-10-30 on 2026-11-05), so the current-month file above
    # supplies the tail.
    MOF_ARCHIVE_URL = (
        "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
        "historical/jgbcme_all.csv"
    )
    #: calendar days of history served: 13 months plus slack for weekends
    HISTORY_DAYS = 400
    #: Valet ?recent=N counts observations (business days). ~280 covers the 13
    #: months the panel compares over, and the SAME response feeds the latest
    #: curve and the history, so Canada costs one request per day.
    BOC_RECENT = 280
    BOC_URL = (
        "https://www.bankofcanada.ca/valet/observations/group/"
        "bond_yields_benchmark/json?recent={n}"
    )
    # Found 2026-11-05 through the Tesouro Transparente CKAN API
    # (https://www.tesourotransparente.gov.br/ckan/api/3/action/package_search
    #  ?q=taxas+titulos+ofertados): dataset "taxas-dos-titulos-ofertados-pelo-
    # tesouro-direto" (licence odc-odbl), its CSV resource. ~15 MB, every
    # bond since 2004, newest Data Base first. Cite "Tesouro Nacional (ODbL)";
    # never re-export the rows.
    TESOURO_URL = (
        "https://www.tesourotransparente.gov.br/ckan/dataset/"
        "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
        "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv"
    )
    #: Bank of England "latest yield curve data" ZIP. NOT fetched while
    #: rates_meta.BOE_PERMITTED is False; unverified until permission arrives.
    BOE_URL = (
        "https://www.bankofengland.co.uk/-/media/boe/files/statistics/"
        "yield-curves/latest-yield-curve-data.zip"
    )

    @staticmethod
    def ust_url(year: Optional[int] = None) -> str:
        """Treasury's CSV export is scoped to a calendar year, so the default
        must be the current year — a hardcoded one silently returns nothing
        every January. Pass a year only to reach a previous file (history)."""
        if year is None:
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
            elif len(parts) == 3 and parts[1] == "curvehist":
                hub.run_async(lambda t=topic, c=parts[2]: self._fetch_curvehist(t, c))
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
        if meta.curve_source == "boc":
            return parse_boc(self._boc_text(_today()))
        if meta.curve_source == "tesouro":
            return _tesouro_curve(self._tesouro_days(_today()))
        if meta.curve_source == "boe":
            return self._boe_curve()
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

    # -- rates:curvehist:<CC> ----------------------------------------------

    def _fetch_curvehist(
        self, topic: str, code: str, today: Optional[datetime.date] = None
    ) -> None:
        """Publish ~13 months of daily curves (runs on the hub's pool, like
        every other topic) so the panel can draw 1W / 1M / 1Y-ago lines and
        spread sparklines."""
        hub = DataHub.instance()
        meta = by_code(code)
        if meta is None:
            hub.publish_error(topic, f"unknown country: {code}")
            return
        today = today or datetime.date.today()
        try:
            days, sources = self._history_for(meta, today)
        except Exception as exc:
            hub.publish_error(topic, f"curve history failed for {meta.code}: {exc}")
            return
        hub.publish(topic, build_history_payload(meta.code, days, sources))

    def _history_for(self, meta, today: datetime.date) -> tuple[list, tuple[str, ...]]:
        """Dispatch on the country's curve source -> (days, sources).

        Sources with no cheap history return ONE day (today's curve) so the
        panel can disable its comparison chips. Extend a branch here when a
        new source gains history (Bank of Canada / Tesouro plug in below)."""
        cutoff = (today - datetime.timedelta(days=self.HISTORY_DAYS)).isoformat()
        if meta.curve_source == "ust":
            return self._ust_history(today, cutoff), (UST,)
        if meta.curve_source == "ecb":
            return self._ecb_history(meta, cutoff), (ECB,)
        if meta.curve_source == "mof":
            return self._mof_history(today, cutoff), (MOF,)
        if meta.curve_source == "boc":
            days = parse_boc_history(self._boc_text(today))
            if not days:
                raise RuntimeError("no Bank of Canada history")
            return [d for d in days if d[0] >= cutoff], (BOC,)
        if meta.curve_source == "tesouro":
            days = self._tesouro_days(today)
            return [d for d in days if d[0] >= cutoff], (TESOURO,)
        if meta.curve_source == "none":
            return [], (BIS,)
        # "fred", "boe" and anything new: latest only (a switched-off "boe"
        # raises inside _curve_for before any request)
        curve = self._curve_for(meta)
        iso = _iso(curve.as_of)
        days = []
        if iso and curve.points:
            days = [(iso, [[p.years, p.rate] for p in curve.points])]
        return days, curve.sources

    @staticmethod
    def _cached_parsed(key: str, today: datetime.date, load: Callable[[], Any]) -> Any:
        """Run `load` (download + parse) at most once a day and keep only the
        parsed result in memory, never the multi-MB text."""
        stamp = today.isoformat()
        with _lock_for("parsed:" + key):
            hit = _HIST_CACHE.get("parsed:" + key)
            if hit is not None and hit[0] == stamp:
                return hit[1]
            value = load()
            if isinstance(value, _Uncached):
                return value.value
            _HIST_CACHE["parsed:" + key] = (stamp, value)
            return value

    def _boc_text(self, today: datetime.date) -> str:
        return self._cached_text(self.BOC_URL.format(n=self.BOC_RECENT), today)

    def _boe_curve(self) -> Curve:
        # read the flag at call time, through the module: this is the guard that
        # keeps every code path off BoE until written permission exists
        if not rates_meta.BOE_PERMITTED:
            raise RuntimeError("Bank of England curve not permitted")
        curve = parse_boe(_get_bytes(self.BOE_URL))
        if not curve.points:
            raise RuntimeError("Bank of England file had no spot curve")
        return curve

    # -- Tesouro: one ~15 MB file, downloaded at most once a day --------------

    _TESOURO_PREFIX = "precotaxatesourodireto-"

    def _tesouro_days(self, today: datetime.date) -> list:
        """Nominal curves for the last HISTORY_DAYS (oldest first), parsed once
        a day and shared by rates:curve:BR and rates:curvehist:BR."""

        def load() -> Any:
            days, fresh = self._tesouro_load(today)
            return days if fresh else _Uncached(days)

        return self._cached_parsed("tesouro", today, load)

    def _tesouro_load(self, today: datetime.date) -> tuple:
        """(days, fresh). `fresh` is False when an older disk copy stood in
        because the download failed: serve it, but do not remember it as
        today's."""
        cutoff = (today - datetime.timedelta(days=self.HISTORY_DAYS)).isoformat()
        folder = _tesouro_cache_dir()
        final = folder / f"{self._TESOURO_PREFIX}{today.isoformat()}.csv"

        def days_of(path: Path) -> list:
            # The file is pure ASCII (checked 2026-11-05: no byte above 0x7f);
            # decode as UTF-8 so an accented title, if one ever appears, is kept
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            days = parse_tesouro_history(text, since=cutoff)    # raises on drift
            return days or parse_tesouro_history(text)[-1:]     # stale: latest

        if final.is_file():
            try:
                return days_of(final), True
            except OSError:
                pass
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                dir=folder, suffix=".part", delete=False)
        except OSError:                       # no usable cache dir: still work
            folder = None
            handle = tempfile.NamedTemporaryFile(suffix=".part", delete=False)
        handle.close()
        part = Path(handle.name)
        try:
            _download(self.TESOURO_URL, part)
        except Exception:
            part.unlink(missing_ok=True)
            older = (
                sorted(folder.glob(f"{self._TESOURO_PREFIX}*.csv"))
                if folder is not None and folder.is_dir() else []
            )
            if not older:
                raise
            return days_of(older[-1]), False
        try:
            days = days_of(part)              # a drifted file raises here, unsaved
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        if folder is None:
            part.unlink(missing_ok=True)
            return days, True
        try:
            os.replace(part, final)
            for stale in folder.iterdir():    # old days, abandoned .part/.tmp
                if stale != final and stale.suffix in (".csv", ".part", ".tmp"):
                    stale.unlink(missing_ok=True)
        except OSError:
            part.unlink(missing_ok=True)      # a missing disk cache is fine
        return days, True

    @staticmethod
    def _cached_text(url: str, today: datetime.date) -> str:
        """Fetch once per day: big, slow-moving downloads (last year's Treasury
        file never changes; MOF's archive grows monthly). Serialised per URL."""
        stamp = today.isoformat()
        with _lock_for("text:" + url):
            hit = _HIST_CACHE.get(url)
            if hit is not None and hit[0] == stamp:
                return hit[1]
            text = _get_text(url)
            _HIST_CACHE[url] = (stamp, text)
            return text

    def _ust_history(self, today: datetime.date, cutoff: str) -> list:
        current = parse_ust_history(_get_text(self.ust_url(today.year)))
        try:
            previous = parse_ust_history(
                self._cached_text(self.ust_url(today.year - 1), today)
            )
        except Exception:
            previous = []                   # publish what we have
        merged = dict(previous + current)   # current year wins on overlap
        if not merged:
            raise RuntimeError("no Treasury history")
        return [(i, p) for i, p in sorted(merged.items()) if i >= cutoff]

    def _ecb_history(self, meta, cutoff: str) -> list:
        wanted = [(y, s) for y, s in _ECB_SERIES.items() if y in meta.tenors]

        def one(item):
            years, suffix = item
            try:
                return years, _get_text(
                    f"{_ECB_BASE}{suffix}?format=csvdata&startPeriod={cutoff}"
                )
            except Exception:
                return years, None                  # skip this tenor, keep the rest

        # ~11 independent series with a 20 s timeout each: fetch a few at a time
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            rows = {y: t for y, t in pool.map(one, wanted) if t is not None}
        days = parse_ecb_history(rows)
        if not days:
            raise RuntimeError("no ECB history")
        return days

    def _mof_history(self, today: datetime.date, cutoff: str) -> list:
        merged: dict[str, list] = {}
        for fetch in (
            # the 1.2 MB archive is parsed once a day, not on every refresh
            lambda: self._cached_parsed(
                "mof-archive", today,
                lambda: parse_mof_history(_get_text(self.MOF_ARCHIVE_URL)),
            ),
            lambda: parse_mof_history(_get_text(self.MOF_URL)),
        ):
            try:
                merged.update(fetch())  # monthly file wins
            except Exception:
                continue
        if not merged:
            raise RuntimeError("no MOF history")
        return [(i, p) for i, p in sorted(merged.items()) if i >= cutoff]
