"""Curated metadata for the jurisdictions aurantium covers in the rates panels:
the mapping between a country's ISO code, its BIS policy-rate key, the source
that publishes its yield curve, and the attribution string the UI must render.

Every entry was verified against the live sources by tools/verify_rates.py
before being listed here. Widening coverage is an edit to COUNTRIES, not a
code change.

Licence note: BIS terms require citing BIS as source and state that inclusion
in a commercial product must not result in an additional charge to users.
Policy-rate content is therefore tier "free" and must never be gated.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

#: valid curve_source router keys; "none" = policy rate only, no curve
CURVE_SOURCES = frozenset({"ust", "ecb", "mof", "fred", "none"})

BIS = "BIS"
UST = "U.S. Treasury"
ECB = "ECB"
MOF = "Japan MOF"
FRED = "FRED"


class CountryMeta(NamedTuple):
    label: str                      # "Japan"
    code: str                       # ISO 3166-1 alpha-2; the RatesContext key
    bis_key: str                    # BIS CBPOL reference-area key
    curve_source: str               # one of CURVE_SOURCES
    tenors: tuple[float, ...]       # maturities the source publishes, in years
    fred_short: Optional[str]       # allowlisted FRED series id, or None
    fred_long: Optional[str]
    citation: str                   # attribution the UI renders
    tier: str                       # "free" | "pro"


_UST_TENORS = (0.0833, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0)
_ECB_TENORS = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0)
_MOF_TENORS = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 40.0)

COUNTRIES: tuple[CountryMeta, ...] = (
    CountryMeta("United States", "US", "US", "ust", _UST_TENORS,
                None, None, f"{BIS} · {UST}", "free"),
    CountryMeta("Euro area", "XM", "XM", "ecb", _ECB_TENORS,
                None, None, f"{BIS} · {ECB}", "free"),
    CountryMeta("Japan", "JP", "JP", "mof", _MOF_TENORS,
                None, None, f"{BIS} · {MOF}", "free"),
    CountryMeta("United Kingdom", "GB", "GB", "fred", (0.25, 10.0),
                "IR3TIB01GBM156N", "IRLTLT01GBM156N", f"{BIS} · {FRED}", "free"),
    CountryMeta("Canada", "CA", "CA", "fred", (0.25, 10.0),
                "IR3TIB01CAM156N", "IRLTLT01CAM156N", f"{BIS} · {FRED}", "free"),
    CountryMeta("Australia", "AU", "AU", "fred", (0.25, 10.0),
                "IR3TIB01AUM156N", "IRLTLT01AUM156N", f"{BIS} · {FRED}", "free"),
    CountryMeta("Switzerland", "CH", "CH", "fred", (0.25, 10.0),
                "IR3TIB01CHM156N", "IRLTLT01CHM156N", f"{BIS} · {FRED}", "free"),
    CountryMeta("Sweden", "SE", "SE", "none", (), None, None, BIS, "free"),
    CountryMeta("Norway", "NO", "NO", "none", (), None, None, BIS, "free"),
    CountryMeta("New Zealand", "NZ", "NZ", "none", (), None, None, BIS, "free"),
    CountryMeta("Korea", "KR", "KR", "none", (), None, None, BIS, "free"),
    CountryMeta("India", "IN", "IN", "none", (), None, None, BIS, "free"),
    CountryMeta("Brazil", "BR", "BR", "none", (), None, None, BIS, "free"),
    CountryMeta("Mexico", "MX", "MX", "none", (), None, None, BIS, "free"),
    CountryMeta("South Africa", "ZA", "ZA", "none", (), None, None, BIS, "free"),
    CountryMeta("China", "CN", "CN", "none", (), None, None, BIS, "free"),
)

_BY_CODE = {c.code: c for c in COUNTRIES}


def by_code(code: str) -> Optional[CountryMeta]:
    """Look up a country by ISO code. Never raises; unknown -> None."""
    if not code:
        return None
    return _BY_CODE.get(code.strip().upper())


def curve_capable() -> tuple[CountryMeta, ...]:
    """Countries with some curve to draw, complete or sparse."""
    return tuple(c for c in COUNTRIES if c.curve_source != "none")
