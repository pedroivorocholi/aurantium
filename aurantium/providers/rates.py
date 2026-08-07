"""Global rates provider: BIS policy rates, sovereign yield curves.

Serves rates:policy (one envelope, all jurisdictions) and rates:curve:<CC>.
HTTP and parsing are kept separate — every upstream has a pure parse_*()
function tested against recorded fixtures, plus a thin fetch_*() wrapper.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, NamedTuple, Optional

from ..rates_meta import UST, MOF, ECB, BIS, by_code


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
